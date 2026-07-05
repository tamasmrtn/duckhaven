"""Turn orchestration: load history → run the agent → persist → stream.

The runner owns its own database sessions via the session factory rather than the
request-scoped session, because a ``StreamingResponse`` body runs *after* the
request handler returns and its ``get_db`` session is already closed. Loopback tool
calls open their own sessions too, so nothing here shares a session across the
concurrent run and stream.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncIterator

import httpx
from httpx import ASGITransport
from pydantic_ai import DeferredToolRequests, DeferredToolResults, ToolApproved, ToolDenied
from pydantic_ai.messages import FunctionToolCallEvent, PartDeltaEvent, TextPartDelta
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import settings
from api.models.assistant import AssistantConversation
from api.services.assistant.access import service_account_can_write
from api.services.assistant.agent import get_agent
from api.services.assistant.deps import AssistantDeps
from api.services.assistant.gateway import Gateway
from api.services.assistant.identity import ephemeral_pat, resolve_service_account
from api.services.assistant.persistence import load_history, save_turn

# Per-run SQL wait ceiling for the assistant's loopback queries.
_QUERY_TIMEOUT_S = 120.0

_run_semaphore: asyncio.Semaphore | None = None


class AssistantDisabledError(RuntimeError):
    """The assistant feature is not enabled in this deployment."""


def _semaphore() -> asyncio.Semaphore:
    global _run_semaphore
    if _run_semaphore is None:
        _run_semaphore = asyncio.Semaphore(settings.assistant_max_concurrency)
    return _run_semaphore


def require_enabled() -> None:
    if not settings.assistant_enabled:
        raise AssistantDisabledError("The AI assistant is not enabled in this deployment.")


def _sse(frame: dict) -> str:
    return f"data: {json.dumps(frame)}\n\n"


def _event_to_frame(event) -> dict | None:
    """Map a Pydantic AI stream event to a client SSE frame, or None to skip."""
    if isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
        if event.delta.content_delta:
            return {"type": "token", "text": event.delta.content_delta}
        return None
    if isinstance(event, FunctionToolCallEvent):
        part = event.part
        args = part.args
        if isinstance(args, str):
            with contextlib.suppress(json.JSONDecodeError):
                args = json.loads(args)
        # A proposed worksheet edit is a client-side action: surface it as its own
        # frame the editor can apply, not a generic tool-call line.
        if part.tool_name == "propose_sql_edit" and isinstance(args, dict):
            return {
                "type": "propose_edit",
                "sql": args.get("sql", ""),
                "explanation": args.get("explanation", ""),
            }
        return {"type": "tool_call", "tool": part.tool_name, "args": args}
    return None


def _deferred_sql(call) -> str | None:
    args = call.args
    if isinstance(args, str):
        with contextlib.suppress(json.JSONDecodeError):
            args = json.loads(args)
    return args.get("sql") if isinstance(args, dict) else None


@contextlib.asynccontextmanager
async def _turn_context(
    session_factory: async_sessionmaker[AsyncSession],
    workspace_id: uuid.UUID,
    workspace_slug: str,
    catalog: str | None,
    editor_sql: str | None = None,
) -> AsyncIterator[AssistantDeps]:
    """Resolve identity, mint an ephemeral PAT, and build the governed gateway."""
    async with session_factory() as db:
        service_account = await resolve_service_account(db)
        can_write = await service_account_can_write(db, workspace_id, service_account.id)
        service_account_id = service_account.id

    async with ephemeral_pat(session_factory, service_account_id) as token:
        # Import here to avoid a circular import at module load (main imports the
        # assistant router, which imports this module).
        from api.main import api_app

        transport = ASGITransport(app=api_app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://assistant.internal",
            headers={"Authorization": f"Bearer {token}"},
            timeout=_QUERY_TIMEOUT_S + 30.0,
        ) as client:
            gateway = Gateway(
                client,
                workspace_slug,
                row_cap=settings.assistant_result_row_cap,
                byte_cap=settings.assistant_result_byte_cap,
            )
            yield AssistantDeps(
                gateway=gateway,
                catalog=catalog,
                can_write=can_write,
                query_timeout_s=_QUERY_TIMEOUT_S,
                editor_sql=editor_sql,
            )


async def _persist(
    session_factory: async_sessionmaker[AsyncSession],
    conversation_id: uuid.UUID,
    result,
    deps: AssistantDeps,
) -> uuid.UUID:
    async with session_factory() as db:
        conversation = await db.get(AssistantConversation, conversation_id)
        message = await save_turn(
            db,
            conversation,
            new_messages_json=result.new_messages_json(),
            usage=result.usage,
            records=deps.records,
        )
        return message.id


async def _stream(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    conversation_id: uuid.UUID,
    workspace_id: uuid.UUID,
    workspace_slug: str,
    catalog: str | None,
    prompt: str | None,
    deferred_results: DeferredToolResults | None,
    editor_sql: str | None = None,
) -> AsyncIterator[str]:
    require_enabled()
    async with _semaphore():
        async with _turn_context(
            session_factory, workspace_id, workspace_slug, catalog, editor_sql
        ) as deps:
            queue: asyncio.Queue = asyncio.Queue()

            async def handler(ctx, events) -> None:
                async for event in events:
                    frame = _event_to_frame(event)
                    if frame is not None:
                        await queue.put(frame)

            async def do_run() -> None:
                try:
                    async with session_factory() as db:
                        history = await load_history(db, conversation_id)
                    result = await get_agent().run(
                        prompt,
                        message_history=history,
                        deferred_tool_results=deferred_results,
                        deps=deps,
                        event_stream_handler=handler,
                    )
                    message_id = await _persist(session_factory, conversation_id, result, deps)
                    output = result.output
                    if isinstance(output, DeferredToolRequests):
                        for call in output.approvals:
                            await queue.put(
                                {
                                    "type": "approval_required",
                                    "tool_call_id": call.tool_call_id,
                                    "tool": call.tool_name,
                                    "sql": _deferred_sql(call),
                                }
                            )
                    else:
                        usage = result.usage
                        await queue.put(
                            {
                                "type": "done",
                                "message_id": str(message_id),
                                "usage": {
                                    "input": usage.input_tokens or 0,
                                    "output": usage.output_tokens or 0,
                                },
                            }
                        )
                except Exception as exc:  # noqa: BLE001 — surfaced as an SSE error frame
                    await queue.put({"type": "error", "message": str(exc)})
                finally:
                    await queue.put(None)

            task = asyncio.create_task(do_run())
            try:
                while True:
                    frame = await queue.get()
                    if frame is None:
                        break
                    yield _sse(frame)
            finally:
                await task


def stream_turn(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    conversation_id: uuid.UUID,
    workspace_id: uuid.UUID,
    workspace_slug: str,
    prompt: str,
    catalog: str | None,
    editor_sql: str | None = None,
) -> AsyncIterator[str]:
    """Stream a new user turn as SSE frames."""
    return _stream(
        session_factory,
        conversation_id=conversation_id,
        workspace_id=workspace_id,
        workspace_slug=workspace_slug,
        catalog=catalog,
        prompt=prompt,
        deferred_results=None,
        editor_sql=editor_sql,
    )


def resume_turn(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    conversation_id: uuid.UUID,
    workspace_id: uuid.UUID,
    workspace_slug: str,
    tool_call_id: str,
    approved: bool,
    reason: str | None,
    catalog: str | None,
) -> AsyncIterator[str]:
    """Resume a turn after the user approves or denies a pending write."""
    decision = (
        ToolApproved() if approved else ToolDenied(reason or "The user did not approve this write.")
    )
    results = DeferredToolResults(approvals={tool_call_id: decision})
    return _stream(
        session_factory,
        conversation_id=conversation_id,
        workspace_id=workspace_id,
        workspace_slug=workspace_slug,
        catalog=catalog,
        prompt=None,
        deferred_results=results,
    )


async def run_turn(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    conversation_id: uuid.UUID,
    workspace_id: uuid.UUID,
    workspace_slug: str,
    prompt: str,
    catalog: str | None = None,
) -> str:
    """Run a turn to completion without streaming (used by scheduled runs).

    Returns the assistant's final text. A write that would need approval is treated
    as declined, since a scheduled run has no human to confirm it.
    """
    require_enabled()
    async with _semaphore():
        async with _turn_context(session_factory, workspace_id, workspace_slug, catalog) as deps:
            async with session_factory() as db:
                history = await load_history(db, conversation_id)
            result = await get_agent().run(prompt, message_history=history, deps=deps)
            await _persist(session_factory, conversation_id, result, deps)
            output = result.output
            if isinstance(output, DeferredToolRequests):
                return "A write was proposed but requires interactive approval; skipped."
            return output
