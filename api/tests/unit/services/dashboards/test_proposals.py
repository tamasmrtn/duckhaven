"""Applying a proposal's operations to a dashboard file, before anything checks the result."""

import pytest
from pydantic import TypeAdapter, ValidationError

from api.services.dashboards import proposals
from api.services.dashboards.proposals import Operation, ProposalInvalid

OPS = TypeAdapter(list[Operation])


def _kpi(widget_id: str) -> dict:
    return {"id": widget_id, "type": "kpi", "title": widget_id, "query": {"model": "sales"}}


FILE = {
    "version": 1,
    "name": "sales",
    "title": "Sales",
    "parameters": {"period": {"type": "time_range"}},
    "rows": [{"widgets": [_kpi("a"), _kpi("b")]}, {"widgets": [_kpi("c")]}],
}


def _apply(*ops: dict) -> dict:
    return proposals.apply(FILE, OPS.validate_python(list(ops)))


def _layout(data: dict) -> list[list[str]]:
    return [[w["id"] for w in row["widgets"]] for row in data["rows"]]


def test_operations_apply_in_order_and_leave_the_file_as_it_was():
    out = _apply(
        {"op": "add_widget", "widget": _kpi("d"), "row": 0, "position": 1},
        {"op": "remove_widget", "id": "a"},
        {"op": "set_metadata", "title": "Sales, weekly"},
    )

    assert _layout(out) == [["d", "b"], ["c"]]
    assert out["title"] == "Sales, weekly"
    assert _layout(FILE) == [["a", "b"], ["c"]]
    assert FILE["title"] == "Sales"


def test_a_widget_without_a_row_gets_a_row_of_its_own_at_the_end():
    assert _layout(_apply({"op": "add_widget", "widget": _kpi("d")})) == [["a", "b"], ["c"], ["d"]]
    assert _layout(_apply({"op": "add_widget", "widget": _kpi("d"), "row": 1})) == [
        ["a", "b"],
        ["c", "d"],
    ]


def test_the_row_after_the_last_is_a_new_row_at_the_end():
    """Rows count from 0, so on a two-row file row 2 is the next one: a model
    moving a widget below the others names it rather than leaving row out."""
    out = _apply({"op": "move_widget", "id": "a", "row": 2})
    assert _layout(out) == [["b"], ["c"], ["a"]]
    out = _apply({"op": "add_widget", "widget": _kpi("d"), "row": 2, "position": 0})
    assert _layout(out) == [["a", "b"], ["c"], ["d"]]


def test_moving_the_last_widget_out_of_a_row_drops_the_row():
    out = _apply({"op": "move_widget", "id": "c", "row": 0, "position": 0})
    assert _layout(out) == [["c", "a", "b"]]
    # Row indexes count rows after the operations before.
    out = _apply({"op": "remove_widget", "id": "c"}, {"op": "move_widget", "id": "a"})
    assert _layout(out) == [["b"], ["a"]]


def test_an_update_replaces_the_whole_widget_and_keeps_its_id():
    widget = {**_kpi("x"), "id": "b", "width": 4}
    out = _apply({"op": "update_widget", "id": "b", "widget": widget})
    assert out["rows"][0]["widgets"][1] == widget
    # Without an id, the widget keeps the one it had.
    out = _apply({"op": "update_widget", "id": "b", "widget": {"type": "text", "markdown": "Hi"}})
    assert out["rows"][0]["widgets"][1] == {"type": "text", "markdown": "Hi", "id": "b"}


def test_parameters_are_added_replaced_and_removed_by_name():
    region = {"type": "dimension", "model": "sales", "dimension": "region"}
    out = _apply({"op": "add_parameter", "name": "region", "parameter": region})
    assert out["parameters"] == {"period": {"type": "time_range"}, "region": region}
    out = _apply({"op": "update_parameter", "name": "period", "parameter": {"type": "dimension"}})
    assert out["parameters"]["period"] == {"type": "dimension"}
    out = _apply({"op": "remove_parameter", "name": "period"})
    assert "parameters" not in out


def test_metadata_changes_only_what_it_names():
    out = _apply({"op": "set_metadata", "labels": ["sales"]})
    assert (out["title"], out["labels"]) == ("Sales", ["sales"])


@pytest.mark.parametrize(
    ("op", "says"),
    [
        ({"op": "remove_widget", "id": "nope"}, "no widget 'nope'"),
        ({"op": "move_widget", "id": "nope"}, "no widget 'nope'"),
        ({"op": "add_widget", "widget": _kpi("a")}, "already has a widget 'a'"),
        ({"op": "add_widget", "widget": _kpi("d"), "row": 3}, "no row 3"),
        ({"op": "update_widget", "id": "a", "widget": _kpi("z")}, "keeps the widget's id"),
        ({"op": "remove_parameter", "name": "nope"}, "no parameter 'nope'"),
        ({"op": "update_parameter", "name": "nope", "parameter": {}}, "no parameter 'nope'"),
        (
            {"op": "add_parameter", "name": "period", "parameter": {}},
            "already has a parameter 'period'",
        ),
    ],
)
def test_an_operation_that_cannot_apply_names_itself(op, says):
    with pytest.raises(ProposalInvalid, match=says) as caught:
        _apply({"op": "set_metadata", "title": "First"}, op)
    assert caught.value.index == 1
    assert str(caught.value).startswith("Operation 2: ")


def test_an_operation_takes_only_its_own_fields():
    with pytest.raises(ValidationError):
        OPS.validate_python([{"op": "remove_widget", "id": "a", "publish": True}])
    with pytest.raises(ValidationError):
        OPS.validate_python([{"op": "publish"}])
