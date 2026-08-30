import uuid

import pytest
import pytest_asyncio
from conftest import seed_workspace
from httpx import AsyncClient
from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.usage import RunUsage

from api.config import settings
from api.models.assistant import AssistantConversation
from api.models.user import User
from api.models.workspace import WorkspaceMember
from api.services.assistant.deps import ToolCallRecord
from api.services.assistant.identity import ASSISTANT_EMAIL
from api.services.assistant.persistence import save_turn
from api.services.auth import hash_password


def _turn_json(user_text: str, assistant_text: str) -> bytes:
    messages = [
        ModelRequest(parts=[UserPromptPart(content=user_text)]),
        ModelResponse(parts=[TextPart(content=assistant_text)]),
    ]
    return ModelMessagesTypeAdapter.dump_json(messages)


@pytest.fixture(autouse=True)
def _enable_assistant(monkeypatch):
    monkeypatch.setattr(settings, "assistant_enabled", True)


@pytest_asyncio.fixture
async def user(db_session) -> User:
    u = User(email="a@assist.local", password_hash=hash_password("pw"), name="A", role="user")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest_asyncio.fixture
async def workspace(db_session, user):
    ws, _ = await seed_workspace(db_session, user_id=user.id)
    return ws


@pytest_asyncio.fixture
async def assistant_account(db_session):
    """The assistant's service account as startup creates it: no access at all."""
    account = User(
        email=ASSISTANT_EMAIL,
        name="Assistant",
        role="user",
        auth_provider="service_account",
    )
    db_session.add(account)
    await db_session.commit()
    await db_session.refresh(account)
    return account


@pytest_asyncio.fixture
async def assistant_member(db_session, workspace, assistant_account):
    """...and then added to the workspace as a reader."""
    db_session.add(
        WorkspaceMember(workspace_id=workspace.id, user_id=assistant_account.id, role="reader")
    )
    await db_session.commit()
    return assistant_account


@pytest_asyncio.fixture
async def authed(client: AsyncClient, user) -> AsyncClient:
    await client.post("/auth/login", json={"email": "a@assist.local", "password": "pw"})
    return client


async def test_disabled_returns_503(authed, workspace, monkeypatch):
    monkeypatch.setattr(settings, "assistant_enabled", False)
    resp = await authed.get(f"/workspaces/{workspace.slug}/assistant/conversations")
    assert resp.status_code == 503


async def test_status_reports_enabled_but_without_access(authed, workspace, assistant_account):
    # The account is created with no membership, so the assistant is on and
    # unusable here — the UI needs both facts to say so without starting a turn.
    resp = await authed.get(f"/workspaces/{workspace.slug}/assistant/status")
    assert resp.status_code == 200
    assert resp.json() == {"enabled": True, "availability": "no_workspace_access"}


async def test_status_reports_access_once_the_account_is_a_member(
    authed, workspace, assistant_member
):
    resp = await authed.get(f"/workspaces/{workspace.slug}/assistant/status")
    assert resp.status_code == 200
    assert resp.json() == {"enabled": True, "availability": "ok"}


async def test_status_reports_disabled_without_503(authed, workspace, monkeypatch):
    monkeypatch.setattr(settings, "assistant_enabled", False)
    resp = await authed.get(f"/workspaces/{workspace.slug}/assistant/status")
    # Reachable even when disabled — that is the whole point.
    assert resp.status_code == 200
    assert resp.json() == {"enabled": False, "availability": "disabled"}


async def test_status_distinguishes_a_disabled_account_from_no_membership(
    authed, workspace, assistant_member, db_session
):
    # Telling an admin to add a membership when the account is disabled sends
    # them somewhere that cannot fix it, so the two states are reported apart.
    assistant_member.is_active = False
    await db_session.commit()

    resp = await authed.get(f"/workspaces/{workspace.slug}/assistant/status")
    assert resp.json() == {"enabled": True, "availability": "account_unavailable"}


async def test_approval_still_answerable_without_workspace_access(
    authed, workspace, db_session, user, assistant_account, monkeypatch
):
    # A paused turn must stay resolvable in both directions: rejecting a write
    # needs no data access, and refusing here would strand it with no way out.
    conv = AssistantConversation(workspace_id=workspace.id, user_id=user.id, title="t")
    db_session.add(conv)
    await db_session.commit()
    await db_session.refresh(conv)

    async def _resumed(*_args, **_kwargs):
        yield 'data: {"type": "token", "text": "denied"}\n\n'

    monkeypatch.setattr("api.routers.assistant.resume_turn", _resumed)

    resp = await authed.post(
        f"/workspaces/{workspace.slug}/assistant/conversations/{conv.id}/approvals",
        json={"tool_call_id": "call-1", "approved": False, "reason": "no"},
    )
    assert resp.status_code == 200, resp.text
    assert "denied" in resp.text


async def test_turn_refused_without_workspace_access(
    authed, workspace, db_session, user, assistant_account
):
    # No membership: the turn is refused outright rather than spending a model run
    # to discover every tool call is denied. The suite blocks real model requests,
    # so a turn that did start would fail loudly here.
    conv = AssistantConversation(workspace_id=workspace.id, user_id=user.id, title="t")
    db_session.add(conv)
    await db_session.commit()
    await db_session.refresh(conv)

    resp = await authed.post(
        f"/workspaces/{workspace.slug}/assistant/conversations/{conv.id}/messages",
        json={"prompt": "hello"},
    )
    assert resp.status_code == 403
    assert "no access to this workspace" in resp.json()["message"]


async def test_non_member_forbidden(authed, user, db_session):
    other, _ = await seed_workspace(db_session, user_id=user.id, role=None, slug="other-ws")
    resp = await authed.get(f"/workspaces/{other.slug}/assistant/conversations")
    assert resp.status_code == 403


async def test_conversation_crud(authed, workspace):
    created = await authed.post(
        f"/workspaces/{workspace.slug}/assistant/conversations", json={"title": "Explore"}
    )
    assert created.status_code == 201
    conv_id = created.json()["id"]
    assert created.json()["title"] == "Explore"

    listed = await authed.get(f"/workspaces/{workspace.slug}/assistant/conversations")
    assert listed.status_code == 200
    assert [c["id"] for c in listed.json()["items"]] == [conv_id]

    detail = await authed.get(f"/workspaces/{workspace.slug}/assistant/conversations/{conv_id}")
    assert detail.status_code == 200
    assert detail.json()["transcript"] == []
    assert detail.json()["tool_calls"] == []

    deleted = await authed.delete(f"/workspaces/{workspace.slug}/assistant/conversations/{conv_id}")
    assert deleted.status_code == 204
    gone = await authed.get(f"/workspaces/{workspace.slug}/assistant/conversations/{conv_id}")
    assert gone.status_code == 404


async def test_rename_conversation(authed, workspace):
    created = await authed.post(
        f"/workspaces/{workspace.slug}/assistant/conversations", json={"title": "Explore"}
    )
    conv_id = created.json()["id"]

    renamed = await authed.patch(
        f"/workspaces/{workspace.slug}/assistant/conversations/{conv_id}",
        json={"title": "Revenue investigation"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Revenue investigation"

    detail = await authed.get(f"/workspaces/{workspace.slug}/assistant/conversations/{conv_id}")
    assert detail.json()["title"] == "Revenue investigation"


async def test_rename_conversation_404_for_another_users_conversation(
    authed, client, workspace, db_session
):
    created = await authed.post(f"/workspaces/{workspace.slug}/assistant/conversations", json={})
    conv_id = created.json()["id"]

    other = User(email="c@assist.local", password_hash=hash_password("pw"), name="C", role="user")
    db_session.add(other)
    await db_session.commit()
    from api.models.workspace import WorkspaceMember

    db_session.add(WorkspaceMember(workspace_id=workspace.id, user_id=other.id, role="reader"))
    await db_session.commit()
    await client.post("/auth/login", json={"email": "c@assist.local", "password": "pw"})

    resp = await client.patch(
        f"/workspaces/{workspace.slug}/assistant/conversations/{conv_id}",
        json={"title": "hijacked"},
    )
    assert resp.status_code == 404


async def test_conversation_is_private_to_creator(authed, client, workspace, db_session):
    created = await authed.post(f"/workspaces/{workspace.slug}/assistant/conversations", json={})
    conv_id = created.json()["id"]

    # A second workspace member cannot read the first user's conversation.
    other = User(email="b@assist.local", password_hash=hash_password("pw"), name="B", role="user")
    db_session.add(other)
    await db_session.commit()
    from api.models.workspace import WorkspaceMember

    db_session.add(WorkspaceMember(workspace_id=workspace.id, user_id=other.id, role="reader"))
    await db_session.commit()
    await client.post("/auth/login", json={"email": "b@assist.local", "password": "pw"})
    resp = await client.get(f"/workspaces/{workspace.slug}/assistant/conversations/{conv_id}")
    assert resp.status_code == 404


async def test_history_truncated_false_under_cap(authed, workspace, db_session):
    created = await authed.post(
        f"/workspaces/{workspace.slug}/assistant/conversations", json={"title": "Explore"}
    )
    conv_id = created.json()["id"]
    conversation = await db_session.get(AssistantConversation, uuid.UUID(conv_id))
    await save_turn(
        db_session,
        conversation,
        new_messages_json=_turn_json("hi", "hello"),
        usage=RunUsage(input_tokens=1, output_tokens=1),
        records={},
    )

    detail = await authed.get(f"/workspaces/{workspace.slug}/assistant/conversations/{conv_id}")
    assert detail.json()["history_truncated"] is False


async def test_tool_call_tables_are_returned_for_deep_links(authed, workspace, db_session):
    created = await authed.post(
        f"/workspaces/{workspace.slug}/assistant/conversations", json={"title": "Explore"}
    )
    conv_id = created.json()["id"]
    conversation = await db_session.get(AssistantConversation, uuid.UUID(conv_id))
    await save_turn(
        db_session,
        conversation,
        new_messages_json=_turn_json("query", "done"),
        usage=RunUsage(input_tokens=1, output_tokens=1),
        records={
            "c1": ToolCallRecord(
                tool="run_sql",
                args={"sql": "SELECT * FROM raw.events"},
                status="ok",
                tables=[{"catalog": "acme", "schema_name": "raw", "table": "events"}],
            )
        },
    )

    detail = await authed.get(f"/workspaces/{workspace.slug}/assistant/conversations/{conv_id}")
    tool_calls = detail.json()["tool_calls"]
    assert len(tool_calls) == 1
    assert tool_calls[0]["tables"] == [{"catalog": "acme", "schema_name": "raw", "table": "events"}]


async def test_history_truncated_true_over_cap(authed, workspace, db_session, monkeypatch):
    monkeypatch.setattr(settings, "assistant_history_turn_cap", 2)
    created = await authed.post(
        f"/workspaces/{workspace.slug}/assistant/conversations", json={"title": "Explore"}
    )
    conv_id = created.json()["id"]
    conversation = await db_session.get(AssistantConversation, uuid.UUID(conv_id))
    for i in range(3):
        await save_turn(
            db_session,
            conversation,
            new_messages_json=_turn_json(f"q{i}", f"a{i}"),
            usage=RunUsage(input_tokens=1, output_tokens=1),
            records={},
        )

    detail = await authed.get(f"/workspaces/{workspace.slug}/assistant/conversations/{conv_id}")
    assert detail.json()["history_truncated"] is True
