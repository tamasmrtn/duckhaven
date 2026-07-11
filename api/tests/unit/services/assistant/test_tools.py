"""Unit tests for individual tool functions, called directly (no agent/model)."""

from types import SimpleNamespace

from api.services.assistant.tools import get_worksheet_selection, get_worksheet_sql


def _ctx(**deps_kwargs):
    return SimpleNamespace(deps=SimpleNamespace(**deps_kwargs))


async def test_get_worksheet_selection_returns_the_selected_text():
    ctx = _ctx(selection_sql="WHERE id = 1")
    assert await get_worksheet_selection(ctx) == "WHERE id = 1"


async def test_get_worksheet_selection_notes_when_nothing_is_selected():
    ctx = _ctx(selection_sql=None)
    assert "no text is currently selected" in await get_worksheet_selection(ctx)


async def test_get_worksheet_sql_notes_when_editor_is_empty():
    ctx = _ctx(editor_sql=None)
    assert "empty or not open" in await get_worksheet_sql(ctx)
