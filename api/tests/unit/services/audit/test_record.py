"""The audit recorder: events commit and roll back with the change they record."""

from __future__ import annotations

import json
import logging
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.audit import AuditEvent
from api.models.user import SERVICE_ACCOUNT_PROVIDER, User
from api.models.workspace import Workspace
from api.services.audit import context as audit_context
from api.services.audit import record, record_now
from api.services.audit.registry import ACTIONS, render_reference


def _user(email="actor@audit.local", provider="local") -> User:
    return User(id=uuid.uuid4(), email=email, name=email, auth_provider=provider)


@pytest.fixture
def request_ctx():
    """A request's context, as the middleware sets it."""
    ctx = audit_context.AuditContext(request_id="r1", source_ip="10.0.0.7", user_agent="curl/8")
    token = audit_context._current.set(ctx)
    yield ctx
    audit_context._current.reset(token)


async def _count(db: AsyncSession) -> int:
    return await db.scalar(select(func.count()).select_from(AuditEvent))


async def test_an_event_commits_with_the_change_and_logs_one_line(db_session, request_ctx, caplog):
    actor, subject = _user(), _user("member@audit.local")
    audit_context.set_actor(actor, auth_method="session", credential_id=uuid.uuid4())
    workspace = Workspace(id=uuid.uuid4(), slug="sales", name="Sales")

    with caplog.at_level(logging.INFO, logger="duckhaven.audit"):
        record(
            db_session,
            "workspace_member.add",
            target_id="x",
            workspace=workspace,
            subject=subject,
            after={"role": "reader"},
        )
        assert not [r for r in caplog.records if r.name == "duckhaven.audit"]
        await db_session.commit()

    row = (await db_session.scalars(select(AuditEvent))).one()
    assert (row.category, row.actor_email, row.actor_type, row.auth_method) == (
        "access",
        actor.email,
        "user",
        "session",
    )
    assert (row.workspace_slug, row.subject_email) == ("sales", subject.email)
    assert row.self_change is False
    assert (row.request_id, row.source_ip, row.user_agent) == ("r1", "10.0.0.7", "curl/8")
    assert row.after == {"role": "reader"}
    lines = [json.loads(r.getMessage()) for r in caplog.records if r.name == "duckhaven.audit"]
    assert [line["id"] for line in lines] == [str(row.id)]
    assert lines[0]["actor"]["email"] == actor.email


async def test_a_rollback_leaves_no_event_and_no_log_line(db_session, request_ctx, caplog):
    with caplog.at_level(logging.INFO, logger="duckhaven.audit"):
        record(db_session, "catalog.create", target_name="sales")
        await db_session.rollback()
        # The next commit must not emit what was rolled back.
        await db_session.commit()

    assert await _count(db_session) == 0
    assert not [r for r in caplog.records if r.name == "duckhaven.audit"]


async def test_changing_your_own_access_is_marked(db_session, request_ctx):
    actor = _user()
    audit_context.set_actor(actor, auth_method="pat")
    record(db_session, "workspace_member.add", subject=actor)
    await db_session.commit()

    assert (await db_session.scalars(select(AuditEvent))).one().self_change is True


async def test_acting_for_someone_records_both_and_marks_their_own_change(db_session, request_ctx):
    assistant = _user("assistant@duckhaven.local", SERVICE_ACCOUNT_PROVIDER)
    person = _user("person@audit.local")
    audit_context.set_actor(assistant, auth_method="pat", on_behalf_of=person)
    record(db_session, "catalog_grant.create", subject=person)
    await db_session.commit()

    row = (await db_session.scalars(select(AuditEvent))).one()
    assert (row.actor_type, row.on_behalf_of_email, row.self_change) == (
        "service_account",
        person.email,
        True,
    )


async def test_outside_a_request_the_system_acts(db_session):
    record(db_session, "catalog_grant.delete", details={"reason": "catalog_dropped"})
    await db_session.commit()

    row = (await db_session.scalars(select(AuditEvent))).one()
    assert (row.actor_type, row.auth_method, row.actor_id) == ("system", "system", None)


@pytest.mark.parametrize("key", ["token", "client_secret", "password_hash", "private_key"])
def test_a_secret_never_reaches_an_event(db_session, key):
    with pytest.raises(ValueError, match="may not carry"):
        record(db_session, "pat.issue", details={"nested": [{key: "x"}]})


def test_an_unknown_action_is_a_programming_error(db_session):
    with pytest.raises(KeyError):
        record(db_session, "grant.oops")


async def test_record_now_survives_the_requests_rollback(db_session, request_ctx):
    await record_now(db_session, "auth.sign_in_failed", target_name="who@audit.local")
    await db_session.rollback()

    assert await _count(db_session) == 1


async def test_record_now_drops_a_repeat_within_the_minute(db_session, request_ctx):
    for _ in range(3):
        await record_now(db_session, "auth.sign_in_failed", target_name="who@audit.local")
    await record_now(db_session, "auth.sign_in_failed", target_name="other@audit.local")

    assert await _count(db_session) == 2


async def test_record_now_keeps_a_repeat_whose_details_differ(db_session, request_ctx):
    for reason in ("callback_failed", "missing_identity_claims", "callback_failed"):
        await record_now(db_session, "auth.sign_in_failed", details={"reason": reason})

    assert await _count(db_session) == 2


async def test_record_now_never_raises(request_ctx, caplog):
    class Broken:
        bind = None

    request_ctx.bind = object()  # not an engine
    await record_now(Broken(), "auth.sign_in_failed", target_name="x")

    assert "Failed to record audit event" in caplog.text


def test_the_reference_lists_every_action():
    table = render_reference()
    assert all(f"`{name}`" in table for name in ACTIONS)
