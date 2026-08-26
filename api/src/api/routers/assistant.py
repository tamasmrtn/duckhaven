import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.responses import StreamingResponse

from api.config import settings
from api.deps import get_current_user, get_db, get_session_factory
from api.models.assistant import AssistantConversation, AssistantToolCall
from api.models.user import User
from api.schemas.assistant import (
    ApprovalRequest,
    AssistantStatusOut,
    ConversationCreate,
    ConversationDetailOut,
    ConversationOut,
    ConversationUpdate,
    ToolCallOut,
    TranscriptItem,
    TurnRequest,
)
from api.schemas.page import Page
from api.services.assistant import resume_turn, stream_turn
from api.services.assistant.persistence import is_history_truncated, render_transcript_with_sql
from api.services.paging import paginate
from api.services.workspace import assert_workspace_member, get_workspace

router = APIRouter()

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _require_enabled() -> None:
    if not settings.assistant_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The AI assistant is not enabled in this deployment.",
        )


async def _load_conversation(
    db: AsyncSession, ws_id: uuid.UUID, conversation_id: uuid.UUID, user_id: uuid.UUID
) -> AssistantConversation:
    conversation = await db.get(AssistantConversation, conversation_id)
    if conversation is None or conversation.workspace_id != ws_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    # A conversation is private to its creator.
    if conversation.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return conversation


async def _workspace(db: AsyncSession, ws: str, user: User):
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    await assert_workspace_member(db, workspace.id, user.id)
    return workspace


@router.get("/workspaces/{workspace}/assistant/status", response_model=AssistantStatusOut)
async def assistant_status(
    ws: Annotated[str, Path(alias="workspace")],
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AssistantStatusOut:
    """Whether the AI assistant is enabled for this deployment.

    Deliberately readable even when the assistant is off, so the UI can render a
    clear disabled state instead of a failure."""
    # Deliberately not gated by `_require_enabled`: the UI needs to learn the
    # assistant is off in order to show a clear disabled state.
    await _workspace(db, ws, user)
    return AssistantStatusOut(enabled=settings.assistant_enabled)


@router.get("/workspaces/{workspace}/assistant/conversations", response_model=Page[ConversationOut])
async def list_conversations(
    ws: Annotated[str, Path(alias="workspace")],
    limit: int = Query(default=100, ge=1, le=1000),
    cursor: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Page[ConversationOut]:
    """The caller's own assistant conversations, most recently updated first.

    Private, unlike most workspace resources: a conversation is visible only to
    the person who started it."""
    _require_enabled()
    workspace = await _workspace(db, ws, user)
    rows, next_cursor, has_more = await paginate(
        db,
        select(AssistantConversation).where(
            AssistantConversation.workspace_id == workspace.id,
            AssistantConversation.user_id == user.id,
        ),
        sort=[AssistantConversation.updated_at.desc(), AssistantConversation.id.desc()],
        limit=limit,
        cursor=cursor,
    )
    return Page[ConversationOut](
        items=[ConversationOut.model_validate(r[0], from_attributes=True) for r in rows],
        cursor=next_cursor,
        has_more=has_more,
    )


@router.post(
    "/workspaces/{workspace}/assistant/conversations",
    response_model=ConversationOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation(
    ws: Annotated[str, Path(alias="workspace")],
    body: ConversationCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AssistantConversation:
    """Start a conversation. Empty until the first message is sent.

    Owned by the caller: only they can read, rename or delete it."""
    _require_enabled()
    workspace = await _workspace(db, ws, user)
    conversation = AssistantConversation(
        workspace_id=workspace.id,
        user_id=user.id,
        title=body.title or "New conversation",
    )
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    return conversation


@router.get(
    "/workspaces/{workspace}/assistant/conversations/{conversation_id}",
    response_model=ConversationDetailOut,
)
async def get_conversation(
    ws: Annotated[str, Path(alias="workspace")],
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ConversationDetailOut:
    """One conversation with its full transcript and any pending tool calls.

    Reports whether the history was truncated, so the UI can say that earlier
    turns are no longer in the model's context rather than silently dropping
    them."""
    _require_enabled()
    workspace = await _workspace(db, ws, user)
    conversation = await _load_conversation(db, workspace.id, conversation_id, user.id)
    transcript = await render_transcript_with_sql(db, conversation.id)
    truncated = await is_history_truncated(db, conversation.id)
    tool_calls = (
        (
            await db.execute(
                select(AssistantToolCall)
                .where(AssistantToolCall.conversation_id == conversation.id)
                .order_by(AssistantToolCall.created_at)
            )
        )
        .scalars()
        .all()
    )
    return ConversationDetailOut(
        id=conversation.id,
        workspace_id=conversation.workspace_id,
        title=conversation.title,
        total_input_tokens=conversation.total_input_tokens,
        total_output_tokens=conversation.total_output_tokens,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        transcript=[TranscriptItem(**item) for item in transcript],
        tool_calls=[ToolCallOut.model_validate(tc) for tc in tool_calls],
        history_truncated=truncated,
    )


@router.patch(
    "/workspaces/{workspace}/assistant/conversations/{conversation_id}",
    response_model=ConversationOut,
)
async def rename_conversation(
    ws: Annotated[str, Path(alias="workspace")],
    conversation_id: uuid.UUID,
    body: ConversationUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AssistantConversation:
    """Rename a conversation. The title is the only editable field."""
    _require_enabled()
    workspace = await _workspace(db, ws, user)
    conversation = await _load_conversation(db, workspace.id, conversation_id, user.id)
    conversation.title = body.title
    await db.commit()
    await db.refresh(conversation)
    return conversation


@router.delete(
    "/workspaces/{workspace}/assistant/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_conversation(
    ws: Annotated[str, Path(alias="workspace")],
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    """Delete a conversation and its transcript.

    Queries the assistant ran during it survive in the query history, which is an
    audit record of what was executed."""
    _require_enabled()
    workspace = await _workspace(db, ws, user)
    conversation = await _load_conversation(db, workspace.id, conversation_id, user.id)
    await db.delete(conversation)
    await db.commit()


@router.post(
    "/workspaces/{workspace}/assistant/conversations/{conversation_id}/messages",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": (
                "A server-sent event stream of the assistant's turn: token deltas, "
                "tool calls, and any write awaiting approval."
            ),
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        },
    },
)
async def send_message(
    ws: Annotated[str, Path(alias="workspace")],
    conversation_id: uuid.UUID,
    body: TurnRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> StreamingResponse:
    """Send a prompt and stream the assistant's turn back as server-sent events.

    The response is `text/event-stream`, not JSON: token deltas, tool calls and
    results arrive as they happen. A turn that wants to write pauses and emits an
    approval request; answer it at `.../approvals` to resume."""
    _require_enabled()
    workspace = await _workspace(db, ws, user)
    conversation = await _load_conversation(db, workspace.id, conversation_id, user.id)
    stream = stream_turn(
        session_factory,
        conversation_id=conversation.id,
        workspace_id=workspace.id,
        workspace_slug=ws,
        prompt=body.prompt,
        catalog=body.catalog,
        editor_sql=body.editor_sql,
        selection_sql=body.selection_sql,
    )
    return StreamingResponse(stream, media_type="text/event-stream", headers=_SSE_HEADERS)


@router.post(
    "/workspaces/{workspace}/assistant/conversations/{conversation_id}/approvals",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": (
                "A server-sent event stream of the assistant's turn: token deltas, "
                "tool calls, and any write awaiting approval."
            ),
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        },
    },
)
async def approve_write(
    ws: Annotated[str, Path(alias="workspace")],
    conversation_id: uuid.UUID,
    body: ApprovalRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> StreamingResponse:
    """Approve or reject a write the assistant paused on, resuming the turn.

    Streams the rest of the turn as server-sent events, the same shape sending a
    message does. Rejecting resumes the turn with the refusal rather than
    aborting it, so the assistant can explain or take another route."""
    _require_enabled()
    workspace = await _workspace(db, ws, user)
    conversation = await _load_conversation(db, workspace.id, conversation_id, user.id)
    stream = resume_turn(
        session_factory,
        conversation_id=conversation.id,
        workspace_id=workspace.id,
        workspace_slug=ws,
        tool_call_id=body.tool_call_id,
        approved=body.approved,
        reason=body.reason,
        catalog=body.catalog,
    )
    return StreamingResponse(stream, media_type="text/event-stream", headers=_SSE_HEADERS)
