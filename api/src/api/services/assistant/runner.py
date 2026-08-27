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
import logging
import uuid
from collections.abc import AsyncIterator

import httpx
from httpx import ASGITransport
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from pydantic_ai import DeferredToolRequests, DeferredToolResults, ToolApproved, ToolDenied
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
)
from pydantic_ai.usage import UsageLimits
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import settings
from api.models.assistant import AssistantConversation
from api.services.assistant.access import service_account_can_write
from api.services.assistant.agent import get_agent
from api.services.assistant.deps import AssistantDeps
from api.services.assistant.gateway import Gateway, GatewayError
from api.services.assistant.identity import (
    AssistantIdentityError,
    ephemeral_pat,
    resolve_service_account,
)
from api.services.assistant.persistence import load_history, save_turn
from api.services.assistant.prompts import format_summary

logger = logging.getLogger(__name__)
_tracer = trace.get_tracer("duckhaven.api")


def _safe_error_message(exc: BaseException) -> str:
    """Map a turn failure to a client-safe message; log the rest server-side."""
    if isinstance(exc, UsageLimitExceeded):
        return "The assistant reached its step limit for this turn. Try a more specific question."
    if isinstance(exc, GatewayError | AssistantIdentityError | AssistantDisabledError):
        return str(exc)
    logger.exception("Assistant turn failed", exc_info=exc)
    return "The assistant hit an internal error."


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


def _event_to_frame(event, *, scoped: bool) -> dict | None:
    """Map a Pydantic AI stream event to a client SSE frame, or None to skip.

    ``scoped`` reflects whether the request carried a worksheet selection — it
    comes from request state, not the model's output, so a model that ignores the
    scoping instructions can't mislabel a full-file rewrite as a scoped edit.
    """
    # A text part's *first* chunk rides on PartStartEvent, not on a delta: the
    # parts manager builds TextPart(content=<first chunk>) and streams only the
    # rest as TextPartDelta. Mapping deltas alone therefore swallows the opening
    # words of every text segment in a turn, which is why a streamed reply read
    # as "me find the customer table first." while the persisted one said "Let me
    # find the customer table first.".
    if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
        if event.part.content:
            return {"type": "token", "text": event.part.content}
        return None
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
                "scoped": scoped,
            }
        return {"type": "tool_call", "tool": part.tool_name, "args": args}
    return None


def _resolved_ids(deferred_results: DeferredToolResults | None) -> set[str]:
    """Tool-call ids being resumed, so history-sanitizing keeps their pending call."""
    if deferred_results is None:
        return set()
    ids: set[str] = set()
    ids.update(getattr(deferred_results, "approvals", None) or {})
    ids.update(getattr(deferred_results, "calls", None) or {})
    return ids


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
    selection_sql: str | None = None,
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
                service_account_id=str(service_account_id),
            )
            # One cheap call, so the instructions can name this workspace's
            # subject areas. Best-effort: if it fails the assistant simply runs
            # without the semantic section rather than the turn failing, which is
            # the same state a workspace with no models is in anyway.
            summary: str | None = None
            try:
                published = await gateway.list_semantic_models()
                if published:
                    summary = format_summary(published)
            except Exception:  # noqa: BLE001 — advisory context, never fatal
                summary = None

            yield AssistantDeps(
                gateway=gateway,
                catalog=catalog,
                can_write=can_write,
                query_timeout_s=_QUERY_TIMEOUT_S,
                service_account_id=service_account_id,
                editor_sql=editor_sql,
                selection_sql=selection_sql,
                semantic_summary=summary,
            )


async def _maybe_generate_title(
    session_factory: async_sessionmaker[AsyncSession],
    conversation_id: uuid.UUID,
    prompt: str | None,
) -> None:
    """Name a still-unnamed conversation from its opening message. Best-effort:
    a title-model failure must never fail the turn."""
    from api.services.assistant.title import DEFAULT_TITLE, generate_title

    if not prompt:
        return
    async with session_factory() as db:
        conversation = await db.get(AssistantConversation, conversation_id)
        if conversation is None or conversation.title != DEFAULT_TITLE:
            return
    try:
        title = await generate_title(prompt)
    except Exception:  # noqa: BLE001 — titling is cosmetic; never break the turn
        return
    if not title:
        return
    async with session_factory() as db:
        conversation = await db.get(AssistantConversation, conversation_id)
        if conversation is not None and conversation.title == DEFAULT_TITLE:
            conversation.title = title
            await db.commit()


async def _persist(
    session_factory: async_sessionmaker[AsyncSession],
    conversation_id: uuid.UUID,
    result,
    deps: AssistantDeps,
) -> uuid.UUID:
    async with session_factory() as db:
        # Lock the conversation row so concurrent turns serialize their ordinal
        # allocation (a no-op on SQLite; the unique constraint is the backstop).
        conversation = (
            await db.execute(
                select(AssistantConversation)
                .where(AssistantConversation.id == conversation_id)
                .with_for_update()
            )
        ).scalar_one()
        # Attribute the conversation to the acting service account (config-driven,
        # so update if it changed since the last turn).
        if conversation.service_account_id != deps.service_account_id:
            conversation.service_account_id = deps.service_account_id
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
    selection_sql: str | None = None,
) -> AsyncIterator[str]:
    require_enabled()
    async with _semaphore():
        async with _turn_context(
            session_factory, workspace_id, workspace_slug, catalog, editor_sql, selection_sql
        ) as deps:
            queue: asyncio.Queue = asyncio.Queue()
            scoped = deps.selection_sql is not None

            async def handler(ctx, events) -> None:
                async for event in events:
                    frame = _event_to_frame(event, scoped=scoped)
                    if frame is not None:
                        await queue.put(frame)

            async def do_run() -> None:
                # The span opens here, inside the detached task, not around the
                # create_task call below: the task is where the turn's work (and its
                # trace context) actually lives. No-op until an SDK is configured.
                with _tracer.start_as_current_span(
                    "assistant.turn",
                    attributes={
                        "duckhaven.conversation_id": str(conversation_id),
                        "duckhaven.workspace_id": str(workspace_id),
                        "duckhaven.assistant.model": settings.assistant_model,
                        "duckhaven.assistant.resumed": deferred_results is not None,
                    },
                ) as span:
                    try:
                        async with session_factory() as db:
                            history = await load_history(
                                db, conversation_id, _resolved_ids(deferred_results)
                            )
                        is_first_turn = not history
                        result = await get_agent().run(
                            prompt,
                            message_history=history,
                            deferred_tool_results=deferred_results,
                            deps=deps,
                            usage_limits=UsageLimits(
                                request_limit=settings.assistant_request_limit
                            ),
                            event_stream_handler=handler,
                        )
                        # Shield persistence: once the turn has run, its messages and
                        # audit rows must land even if this task is cancelled during
                        # cleanup (e.g. client disconnect / shutdown).
                        message_id = await asyncio.shield(
                            _persist(session_factory, conversation_id, result, deps)
                        )
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
                        if is_first_turn:
                            await _maybe_generate_title(session_factory, conversation_id, prompt)
                    except Exception as exc:  # noqa: BLE001 — surfaced as an SSE error frame
                        span.record_exception(exc)
                        span.set_status(Status(StatusCode.ERROR, str(exc)))
                        await queue.put({"type": "error", "message": _safe_error_message(exc)})
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
                # Real Stop: the composer's Stop button aborts the client fetch,
                # which Starlette surfaces here as a generator close. Cancel the
                # in-flight turn rather than letting it run on — the model stops,
                # Gateway.run_sql cancels any query still executing on the agent,
                # and a turn cancelled mid-run persists nothing. The shielded
                # _persist inside do_run still protects a turn that *already*
                # finished, so completing the run just before Stop is never lost.
                # On normal completion the task is already done, so this is a no-op.
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
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
    selection_sql: str | None = None,
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
        selection_sql=selection_sql,
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
