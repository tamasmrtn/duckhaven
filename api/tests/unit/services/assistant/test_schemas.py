"""Schema-boundary normalization for assistant turn requests."""

import pytest
from pydantic import ValidationError

from api.schemas.assistant import (
    ApprovalRequest,
    ConversationCreate,
    ConversationUpdate,
    TurnRequest,
)
from api.services.text_safety import REPLACEMENT


@pytest.mark.parametrize("blank", ["", "   ", "\n\t "])
def test_blank_selection_is_normalized_to_none(blank):
    # An empty/whitespace-only selection must become None at the boundary, so
    # `scoped` (runner: `is not None`) and the selection tool (truthiness) agree
    # there is no selection.
    page = TurnRequest(prompt="hi", page_context={"page": "worksheet", "selection": blank})
    assert page.page_context.selection is None


def test_real_selection_is_preserved():
    page = TurnRequest(prompt="hi", page_context={"page": "worksheet", "selection": "id = 1"})
    assert page.page_context.selection == "id = 1"


def test_missing_selection_stays_none():
    page = TurnRequest(prompt="hi", page_context={"page": "worksheet", "sql": "SELECT 1"})
    assert page.page_context.selection is None
    assert TurnRequest(prompt="hi").page_context is None


def test_a_dashboard_page_says_where_it_is_and_nothing_it_shows():
    page = TurnRequest(
        prompt="why did this drop?",
        page_context={
            "page": "dashboard",
            "name": "sales-overview",
            "version": 3,
            "params": {"period": "last_90_days", "country": None},
            "widget": "revenue",
        },
    ).page_context
    assert (page.name, page.version, page.widget) == ("sales-overview", 3, "revenue")
    assert page.params == {"period": "last_90_days", "country": None}


@pytest.mark.parametrize(
    "context",
    [
        # The page sends where it is, never the file or the rows.
        {"page": "dashboard", "name": "sales", "spec": {"rows": []}},
        {"page": "dashboard", "name": "sales", "rows": [[1]]},
        {"page": "dashboard", "name": "Sales Overview"},
        {"page": "dashboard", "name": "sales", "params": {"p": {"nested": 1}}},
        {"page": "dashboard", "name": "sales", "params": {f"p{i}": 1 for i in range(9)}},
        {"page": "dashboard", "name": "sales", "params": {"p": "x" * 201}},
        {"page": "settings"},
    ],
)
def test_a_page_context_out_of_shape_is_refused(context):
    with pytest.raises(ValidationError):
        TurnRequest(prompt="hi", page_context=context)


def test_a_title_is_as_long_as_the_column_and_no_longer():
    assert ConversationCreate(title="x" * 255).title == "x" * 255
    with pytest.raises(ValidationError):
        ConversationCreate(title="x" * 256)
    with pytest.raises(ValidationError):
        ConversationUpdate(title="x" * 256)


def test_a_title_may_be_left_out_or_empty():
    assert ConversationCreate().title is None
    assert ConversationCreate(title="").title == ""


@pytest.mark.parametrize("bad", ["a\x00b", "a\ud800b"])
def test_text_the_database_cannot_hold_is_replaced_not_refused(bad):
    """NUL reaches Postgres as an error and a lone surrogate cannot be encoded at all,
    on every field the person types."""
    kept = f"a{REPLACEMENT}b"

    assert ConversationCreate(title=bad).title == kept
    assert ConversationUpdate(title=bad).title == kept
    request = TurnRequest(prompt=bad, catalog=bad)
    assert (request.prompt, request.catalog) == (kept, kept)
    approval = ApprovalRequest(tool_call_id=bad, approved=False, reason=bad, catalog=bad)
    assert (approval.tool_call_id, approval.reason, approval.catalog) == (kept,) * 3


def test_a_title_of_nothing_but_nul_is_still_a_title():
    """Replaced before the length check, so it counts as the character it became."""
    assert ConversationUpdate(title="\x00").title == REPLACEMENT


def test_the_length_limit_counts_what_is_kept():
    """A NUL is one character before and after, so it cannot be used to slip past 255."""
    with pytest.raises(ValidationError):
        ConversationCreate(title="\x00" * 256)
