import uuid

from fastapi import APIRouter, Depends, HTTPException, status
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
    ToolCallOut,
    TranscriptItem,
    TurnRequest,
)
from api.services.assistant import resume_turn, stream_turn
from api.services.assistant.persistence import render_transcript_with_sql
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


@router.get("/workspaces/{ws}/assistant/status", response_model=AssistantStatusOut)
async def assistant_status(
    ws: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AssistantStatusOut:
    # Deliberately not gated by `_require_enabled`: the UI needs to learn the
    # assistant is off in order to show a clear disabled state.
    await _workspace(db, ws, user)
    return AssistantStatusOut(enabled=settings.assistant_enabled)


@router.get("/workspaces/{ws}/assistant/conversations", response_model=list[ConversationOut])
async def list_conversations(
    ws: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[AssistantConversation]:
    _require_enabled()
    workspace = await _workspace(db, ws, user)
    result = await db.execute(
        select(AssistantConversation)
        .where(
            AssistantConversation.workspace_id == workspace.id,
            AssistantConversation.user_id == user.id,
        )
        .order_by(AssistantConversation.updated_at.desc())
    )
    return list(result.scalars().all())


@router.post(
    "/workspaces/{ws}/assistant/conversations",
    response_model=ConversationOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation(
    ws: str,
    body: ConversationCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AssistantConversation:
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
    "/workspaces/{ws}/assistant/conversations/{conversation_id}",
    response_model=ConversationDetailOut,
)
async def get_conversation(
    ws: str,
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ConversationDetailOut:
    _require_enabled()
    workspace = await _workspace(db, ws, user)
    conversation = await _load_conversation(db, workspace.id, conversation_id, user.id)
    transcript = await render_transcript_with_sql(db, conversation.id)
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
    )


@router.delete(
    "/workspaces/{ws}/assistant/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_conversation(
    ws: str,
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    _require_enabled()
    workspace = await _workspace(db, ws, user)
    conversation = await _load_conversation(db, workspace.id, conversation_id, user.id)
    await db.delete(conversation)
    await db.commit()


@router.post("/workspaces/{ws}/assistant/conversations/{conversation_id}/messages")
async def send_message(
    ws: str,
    conversation_id: uuid.UUID,
    body: TurnRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> StreamingResponse:
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


@router.post("/workspaces/{ws}/assistant/conversations/{conversation_id}/approvals")
async def approve_write(
    ws: str,
    conversation_id: uuid.UUID,
    body: ApprovalRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> StreamingResponse:
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
