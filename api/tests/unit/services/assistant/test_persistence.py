import pytest_asyncio
from conftest import seed_workspace
from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.usage import RunUsage
from sqlalchemy import select

from api.models.assistant import AssistantConversation, AssistantToolCall
from api.models.user import User
from api.services.assistant.deps import ToolCallRecord
from api.services.assistant.persistence import (
    load_history,
    render_transcript,
    save_turn,
)
from api.services.auth import hash_password


@pytest_asyncio.fixture
async def conversation(db_session) -> AssistantConversation:
    user = User(email="u@p.local", password_hash=hash_password("pw"), name="U", role="user")
    db_session.add(user)
    await db_session.commit()
    ws, _ = await seed_workspace(db_session, user_id=user.id)
    conv = AssistantConversation(workspace_id=ws.id, user_id=user.id, title="t")
    db_session.add(conv)
    await db_session.commit()
    await db_session.refresh(conv)
    return conv


def _turn_json(user_text: str, assistant_text: str) -> bytes:
    messages = [
        ModelRequest(parts=[UserPromptPart(content=user_text)]),
        ModelResponse(parts=[TextPart(content=assistant_text)]),
    ]
    return ModelMessagesTypeAdapter.dump_json(messages)


async def test_save_and_load_roundtrip(db_session, conversation):
    await save_turn(
        db_session,
        conversation,
        new_messages_json=_turn_json("hi", "hello"),
        usage=RunUsage(input_tokens=3, output_tokens=5),
        records={"c1": ToolCallRecord(tool="list_catalogs", args={}, status="ok", latency_ms=9)},
    )
    history = await load_history(db_session, conversation.id)
    transcript = render_transcript(history)
    assert transcript == [
        {"role": "user", "text": "hi"},
        {"role": "assistant", "text": "hello"},
    ]
    assert conversation.total_input_tokens == 3
    assert conversation.total_output_tokens == 5
    tool_calls = (await db_session.execute(select(AssistantToolCall))).scalars().all()
    assert len(tool_calls) == 1
    assert tool_calls[0].tool == "list_catalogs"
    assert tool_calls[0].status == "ok"


async def test_ordinals_increment_across_turns(db_session, conversation):
    await save_turn(
        db_session,
        conversation,
        new_messages_json=_turn_json("one", "first"),
        usage=RunUsage(input_tokens=1, output_tokens=1),
        records={},
    )
    await save_turn(
        db_session,
        conversation,
        new_messages_json=_turn_json("two", "second"),
        usage=RunUsage(input_tokens=1, output_tokens=1),
        records={},
    )
    history = await load_history(db_session, conversation.id)
    transcript = render_transcript(history)
    assert [i["text"] for i in transcript] == ["one", "first", "two", "second"]
