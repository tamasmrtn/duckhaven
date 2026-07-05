from __future__ import annotations

from datetime import UTC, datetime

import pytest_asyncio
from conftest import seed_workspace

import api.services.assistant.runner as runner_mod
from api.config import settings
from api.models.query import Schedule
from api.models.user import User
from api.services.auth import hash_password
from api.services.scheduler import scanner as scheduler_mod


@pytest_asyncio.fixture
async def assistant_schedule(db_session):
    user = User(email="sched@a.local", password_hash=hash_password("pw"), name="S", role="user")
    db_session.add(user)
    await db_session.flush()
    ws, _ = await seed_workspace(db_session, user_id=user.id, slug="asched")
    schedule = Schedule(
        workspace_id=ws.id,
        job_type="assistant_run",
        assistant_prompt="Summarize the largest tables.",
        cron="0 * * * *",
        enabled=True,
        created_by=user.id,
    )
    db_session.add(schedule)
    await db_session.commit()
    await db_session.refresh(schedule)
    return schedule


async def test_assistant_run_invokes_run_turn(db_session, assistant_schedule, monkeypatch):
    monkeypatch.setattr(settings, "assistant_enabled", True)
    calls = {}

    async def fake_run_turn(session_factory, **kwargs):
        calls.update(kwargs)
        return "done"

    monkeypatch.setattr(runner_mod, "run_turn", fake_run_turn)

    now = datetime.now(tz=UTC)
    ran = await scheduler_mod._dispatch_schedule(db_session, assistant_schedule, now)

    assert ran is True
    assert assistant_schedule.last_run_at == now
    assert assistant_schedule.next_run_at is not None
    assert calls["prompt"] == "Summarize the largest tables."


async def test_assistant_run_skipped_when_disabled(db_session, assistant_schedule, monkeypatch):
    monkeypatch.setattr(settings, "assistant_enabled", False)
    called = False

    async def fake_run_turn(*a, **k):
        nonlocal called
        called = True

    monkeypatch.setattr(runner_mod, "run_turn", fake_run_turn)

    now = datetime.now(tz=UTC)
    ran = await scheduler_mod._dispatch_schedule(db_session, assistant_schedule, now)

    assert ran is False
    assert called is False
    assert assistant_schedule.last_run_at is None
    # next_run_at is still advanced so the schedule doesn't backlog.
    assert assistant_schedule.next_run_at is not None
