"""Schema-boundary normalization for assistant turn requests."""

import pytest
from pydantic import ValidationError

from api.schemas.assistant import TurnRequest


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
