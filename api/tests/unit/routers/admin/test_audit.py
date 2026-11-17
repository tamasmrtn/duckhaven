"""GET /admin/audit/events: the audit log, newest first, for holders of audit:read."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import get_args

import pytest
from httpx import AsyncClient

from api.models.audit import ACTOR_TYPES, AUTH_METHODS, CATEGORIES, OUTCOMES
from api.models.user import User
from api.schemas.audit import AuditActor, Category, Outcome
from api.services.audit import record
from api.services.auth import hash_password

URL = "/admin/audit/events"
T0 = datetime(2026, 11, 1, tzinfo=UTC)


async def _user(db, email: str, role: str = "user") -> User:
    u = User(email=email, password_hash=hash_password("pw"), name=email, role=role)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _signed_in(client: AsyncClient, db, role: str) -> User:
    u = await _user(db, f"{role}@audit.local", role)
    await client.post("/auth/login", json={"email": u.email, "password": "pw"})
    return u


async def _event(db, action: str, minutes: int, **fields):
    """An event ``minutes`` after T0, with any column set directly."""
    event = record(db, action, target_name=fields.pop("target_name", action))
    event.event_time = T0 + timedelta(minutes=minutes)
    for name, value in fields.items():
        setattr(event, name, value)
    await db.commit()
    return event


async def _names(client: AsyncClient, **params) -> list[str]:
    resp = await client.get(URL, params=params)
    assert resp.status_code == 200, resp.text
    return [e["target"]["name"] for e in resp.json()["items"]]


async def test_without_audit_read_the_log_is_refused(client, db_session):
    await _signed_in(client, db_session, "user")

    assert (await client.get(URL)).status_code == 403


async def test_events_come_newest_first_in_pages(client, db_session):
    await _signed_in(client, db_session, "admin")
    for minute, name in enumerate(["a", "b", "c"]):
        await _event(db_session, "workspace.create", minute, target_name=name)

    first = (await client.get(URL, params={"limit": 2, "category": "access"})).json()
    rest = (
        await client.get(URL, params={"limit": 2, "category": "access", "cursor": first["cursor"]})
    ).json()

    assert [e["target"]["name"] for e in first["items"]] == ["c", "b"]
    assert first["has_more"] is True
    assert [e["target"]["name"] for e in rest["items"]] == ["a"]
    assert rest["has_more"] is False


async def test_an_event_reads_in_full(client, db_session):
    admin = await _signed_in(client, db_session, "admin")
    ann = await _user(db_session, "ann@audit.local")
    await _event(
        db_session,
        "workspace_member.add",
        0,
        target_name="ann@audit.local",
        actor_id=admin.id,
        actor_email=admin.email,
        actor_type="user",
        subject_id=ann.id,
        subject_email=ann.email,
        workspace_slug="ops",
        after={"role": "owner"},
    )

    (event,) = (await client.get(URL, params={"action": "workspace_member.add"})).json()["items"]

    assert event["label"] == "Added a workspace member"
    assert event["category"] == "access" and event["outcome"] == "success"
    assert event["actor"]["email"] == admin.email
    assert event["subject"] == {"id": str(ann.id), "email": ann.email}
    assert event["workspace"]["slug"] == "ops"
    assert event["after"] == {"role": "owner"}


async def test_action_matches_a_name_or_a_prefix(client, db_session):
    await _signed_in(client, db_session, "admin")
    await _event(db_session, "workspace.create", 0)
    await _event(db_session, "workspace_member.add", 1)
    await _event(db_session, "workspace_member.remove", 2)

    assert await _names(client, action="workspace.*") == ["workspace.create"]
    assert await _names(client, action="workspace_member.*") == [
        "workspace_member.remove",
        "workspace_member.add",
    ]
    assert await _names(client, action="workspace_member") == []


async def test_actor_matches_calls_made_on_their_behalf(client, db_session):
    await _signed_in(client, db_session, "admin")
    ann = await _user(db_session, "ann@audit.local")
    await _event(db_session, "pat.issue", 0, target_name="own", actor_id=ann.id)
    await _event(db_session, "pat.issue", 1, target_name="delegated", on_behalf_of_id=ann.id)
    await _event(db_session, "pat.issue", 2, target_name="someone else", actor_id=uuid.uuid4())

    assert await _names(client, actor_id=str(ann.id)) == ["delegated", "own"]


async def test_the_other_filters(client, db_session):
    await _signed_in(client, db_session, "admin")
    ann = await _user(db_session, "ann@audit.local")
    ws_id = uuid.uuid4()
    await _event(db_session, "workspace.create", 0, target_name="early")
    await _event(
        db_session,
        "workspace_member.add",
        10,
        target_name="mine",
        workspace_id=ws_id,
        workspace_slug="ops",
        subject_id=ann.id,
        self_change=True,
    )
    await _event(db_session, "auth.sign_in_failed", 20, target_name="failed", outcome="failure")

    window = {
        "since": (T0 + timedelta(minutes=10)).isoformat(),
        "until": (T0 + timedelta(hours=1)).isoformat(),
    }
    assert await _names(client, **window) == [
        "failed",
        "mine",
    ]
    assert await _names(client, until=(T0 + timedelta(minutes=10)).isoformat()) == ["early"]
    assert await _names(client, workspace="ops") == ["mine"]
    assert await _names(client, workspace=str(ws_id)) == ["mine"]
    assert await _names(client, subject_id=str(ann.id)) == ["mine"]
    assert await _names(client, self_change="true") == ["mine"]
    assert await _names(client, outcome="failure") == ["failed"]
    assert await _names(client, target_type="workspace") == ["early"]


@pytest.mark.parametrize("params", [{"category": "nope"}, {"outcome": "nope"}, {"limit": 1001}])
async def test_an_invalid_filter_is_refused(client, db_session, params):
    await _signed_in(client, db_session, "admin")

    assert (await client.get(URL, params=params)).status_code == 422


def test_the_schema_offers_the_values_the_table_allows():
    assert get_args(Category) == CATEGORIES
    assert get_args(Outcome) == OUTCOMES
    assert get_args(AuditActor.model_fields["type"].annotation) == ACTOR_TYPES
    (methods, _none) = get_args(AuditActor.model_fields["auth_method"].annotation)
    assert get_args(methods) == AUTH_METHODS
