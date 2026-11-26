"""Saving an assistant turn against real Postgres.

The unit suite runs on SQLite, which stores NUL and ignores a column's width, so it
cannot say whether a turn survives the database it ships on. These run the real
statements. They need only ``DATABASE_URL``: no model, no Polaris.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.usage import RunUsage
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from api.models.assistant import AssistantConversation, AssistantMessage, AssistantToolCall
from api.models.user import User
from api.models.workspace import Workspace
from api.services.assistant.deps import ToolCallRecord
from api.services.assistant.persistence import (
    pending_call,
    save_turn,
    save_unfinished_turn_calls,
)
from api.services.text_safety import REPLACEMENT

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def conversation(db_session, admin_user: User) -> AssistantConversation:
    workspace = Workspace(slug="it-assistant", name="IT assistant")
    db_session.add(workspace)
    await db_session.flush()
    conv = AssistantConversation(workspace_id=workspace.id, user_id=admin_user.id, title="t")
    db_session.add(conv)
    await db_session.commit()
    await db_session.refresh(conv)
    return conv


def _turn(answer: str) -> bytes:
    return ModelMessagesTypeAdapter.dump_json(
        [
            ModelRequest(parts=[UserPromptPart(content="what is in the cell?")]),
            ModelResponse(parts=[TextPart(content=answer)]),
        ]
    )


async def test_postgres_refuses_nul_in_jsonb(db_session):
    """The premise: a NUL anywhere in a stored message fails the whole insert."""
    with pytest.raises(DBAPIError, match="unsupported Unicode escape sequence"):
        await db_session.execute(text("SELECT CAST(:v AS jsonb)"), {"v": '{"cell": "a\\u0000b"}'})


async def test_a_turn_whose_data_holds_nul_is_saved(db_session, conversation):
    await save_turn(
        db_session,
        conversation,
        new_messages_json=_turn("It holds a\x00b."),
        usage=RunUsage(input_tokens=1, output_tokens=1),
        records={
            "c1": ToolCallRecord(
                tool="run_sql",
                args={"sql": "SELECT chr(0)"},
                status="error",
                detail="Query failed: a\x00b",
                tables=[{"catalog": "c", "schema_name": "s", "table": "t\x00"}],
            )
        },
    )

    saved = (await db_session.execute(select(AssistantMessage))).scalar_one()
    call = (await db_session.execute(select(AssistantToolCall))).scalar_one()
    assert saved.payload[-1]["parts"][0]["content"] == f"It holds a{REPLACEMENT}b."
    assert call.detail == f"Query failed: a{REPLACEMENT}b"
    assert call.tables[0]["table"] == f"t{REPLACEMENT}"


async def test_a_title_of_255_four_byte_characters_fits_the_column(db_session, conversation):
    """The title limit counts characters, as the column does, not bytes."""
    conversation.title = "\U0001f986" * 255

    await db_session.commit()

    await db_session.refresh(conversation)
    assert len(conversation.title) == 255


async def test_a_pending_call_is_read_back_from_the_saved_turn(db_session, conversation):
    asked = ModelMessagesTypeAdapter.dump_json(
        [
            ModelRequest(parts=[UserPromptPart(content="delete it")]),
            ModelResponse(
                parts=[ToolCallPart(tool_name="run_sql", args={"sql": "DELETE"}, tool_call_id="c1")]
            ),
        ]
    )
    await save_turn(
        db_session,
        conversation,
        new_messages_json=asked,
        usage=RunUsage(input_tokens=1, output_tokens=1),
        records={},
    )

    assert await pending_call(db_session, conversation.id, "c1") is not None
    assert await pending_call(db_session, conversation.id, "c2") is None


async def test_the_calls_of_a_turn_that_failed_are_audited_without_saving_the_turn(
    db_session, conversation
):
    """The failed turn's calls hold whatever the data did, NUL included, and the new
    flag is stored: the audit survives the failure it is there to explain."""
    await save_unfinished_turn_calls(
        db_session,
        conversation.id,
        {
            "c1": ToolCallRecord(
                tool="run_sql",
                args={"sql": "SELECT chr(0)"},
                status="error",
                detail="Query failed: a\x00b",
                latency_ms=7,
            )
        },
    )

    call = (await db_session.execute(select(AssistantToolCall))).scalar_one()
    assert call.from_failed_turn is True
    assert call.detail == f"Query failed: a{REPLACEMENT}b"
    assert (await db_session.execute(select(AssistantMessage))).scalars().all() == []
