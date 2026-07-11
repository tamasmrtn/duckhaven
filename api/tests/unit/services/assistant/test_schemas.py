"""Schema-boundary normalization for assistant turn requests."""

import pytest

from api.schemas.assistant import TurnRequest


@pytest.mark.parametrize("blank", ["", "   ", "\n\t "])
def test_blank_selection_is_normalized_to_none(blank):
    # An empty/whitespace-only selection must become None at the boundary, so
    # `scoped` (runner: `is not None`) and the selection tool (truthiness) agree
    # there is no selection.
    assert TurnRequest(prompt="hi", selection_sql=blank).selection_sql is None


def test_real_selection_is_preserved():
    assert TurnRequest(prompt="hi", selection_sql="id = 1").selection_sql == "id = 1"


def test_missing_selection_stays_none():
    assert TurnRequest(prompt="hi").selection_sql is None
