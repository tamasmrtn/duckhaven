"""Unit tests for individual tool functions, called directly (no agent/model)."""

import uuid
from types import SimpleNamespace

from api.schemas.assistant import DashboardContext, WorksheetContext
from api.services.assistant.deps import AssistantDeps
from api.services.assistant.tools import get_worksheet_selection, get_worksheet_sql


def _ctx(page=None):
    deps = AssistantDeps(
        gateway=None,
        catalog=None,
        can_write=False,
        query_timeout_s=30.0,
        service_account_id=uuid.uuid4(),
        page=page,
    )
    return SimpleNamespace(deps=deps)


async def test_get_worksheet_selection_returns_the_selected_text():
    ctx = _ctx(WorksheetContext(page="worksheet", sql="SELECT 1", selection="WHERE id = 1"))
    assert await get_worksheet_selection(ctx) == "WHERE id = 1"


async def test_get_worksheet_selection_notes_when_nothing_is_selected():
    ctx = _ctx(WorksheetContext(page="worksheet", sql="SELECT 1"))
    assert "no text is currently selected" in await get_worksheet_selection(ctx)


async def test_get_worksheet_sql_notes_when_editor_is_empty():
    assert "empty or not open" in await get_worksheet_sql(_ctx())


async def test_a_dashboard_page_has_no_worksheet():
    ctx = _ctx(DashboardContext(page="dashboard", name="sales"))
    assert "empty or not open" in await get_worksheet_sql(ctx)
    assert ctx.deps.dashboard.name == "sales"
