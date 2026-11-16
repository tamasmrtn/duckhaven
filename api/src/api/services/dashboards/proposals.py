"""Proposed changes to a dashboard: operations on its file, applied without saving.

The assistant does not write a dashboard. It proposes operations against the
dashboard's newest file, the draft when there is one, and a person accepts the
result as the draft or discards it. An operation names what it changes, a widget
by its ``id`` or a parameter by its name, so a reviewer reads the proposal as a
list of changes rather than as a whole new file to compare.

:func:`apply` works on the file as a plain dict and leaves checking the result
to :func:`~api.services.dashboards.loader.parse`, so a proposal is held to the
same rules as any other file. What it checks itself is what only the operations
can get wrong, such as a widget that is not on the dashboard.
"""

from __future__ import annotations

import copy
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

#: A proposal changes a dashboard by at most this many operations.
MAX_OPERATIONS = 40


class ProposalInvalid(ValueError):
    """An operation that cannot apply to the file, with its position in the list."""

    def __init__(self, index: int, message: str) -> None:
        super().__init__(f"Operation {index + 1}: {message}")
        self.index = index


class _Op(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SetMetadata(_Op):
    """Change the dashboard's title, description or labels. Omitted fields stay."""

    op: Literal["set_metadata"]
    title: str | None = None
    description: str | None = None
    labels: list[str] | None = None


class AddWidget(_Op):
    """Add a widget, at the end of row ``row``, or before ``position`` in it.

    Without ``row``, or with the row after the last, the widget gets a row of its
    own at the end.
    """

    op: Literal["add_widget"]
    widget: dict
    row: int | None = Field(default=None, ge=0, description="The row's index, from 0.")
    position: int | None = Field(default=None, ge=0, description="Its index in the row.")


class UpdateWidget(_Op):
    """Replace widget ``id`` with ``widget``, which keeps the same id."""

    op: Literal["update_widget"]
    id: str
    widget: dict


class MoveWidget(_Op):
    """Move widget ``id`` to row ``row``, or to a row of its own at the end.

    The row after the last is a row of its own at the end too.
    """

    op: Literal["move_widget"]
    id: str
    row: int | None = Field(default=None, ge=0)
    position: int | None = Field(default=None, ge=0)


class RemoveWidget(_Op):
    op: Literal["remove_widget"]
    id: str


class AddParameter(_Op):
    op: Literal["add_parameter"]
    name: str
    parameter: dict


class UpdateParameter(_Op):
    """Replace parameter ``name`` with ``parameter``."""

    op: Literal["update_parameter"]
    name: str
    parameter: dict


class RemoveParameter(_Op):
    op: Literal["remove_parameter"]
    name: str


Operation = Annotated[
    SetMetadata
    | AddWidget
    | UpdateWidget
    | MoveWidget
    | RemoveWidget
    | AddParameter
    | UpdateParameter
    | RemoveParameter,
    Field(discriminator="op"),
]


def apply(data: dict, operations: list[Operation]) -> dict:
    """The file ``data`` with ``operations`` applied in order. ``data`` is left as it is.

    A row left empty by a move or a removal is dropped. Raises
    :class:`ProposalInvalid` for an operation that names something the file
    does not have, or adds something it already has.
    """
    out = copy.deepcopy(data)
    out.setdefault("parameters", {})
    rows: list[dict] = out.setdefault("rows", [])
    for index, op in enumerate(operations):
        try:
            _apply(out, rows, op)
        except _Refused as exc:
            raise ProposalInvalid(index, str(exc)) from None
        rows[:] = [row for row in rows if row.get("widgets")]
    if not out["parameters"]:
        del out["parameters"]
    return out


class _Refused(Exception):
    pass


def _apply(out: dict, rows: list[dict], op: _Op) -> None:
    if isinstance(op, SetMetadata):
        for key in ("title", "description", "labels"):
            value = getattr(op, key)
            if value is not None:
                out[key] = value
    elif isinstance(op, AddWidget):
        widget_id = op.widget.get("id")
        if widget_id in _ids(rows):
            raise _Refused(f"The dashboard already has a widget {widget_id!r}.")
        _place(rows, op.widget, op.row, op.position)
    elif isinstance(op, UpdateWidget):
        if op.widget.get("id", op.id) != op.id:
            raise _Refused("update_widget keeps the widget's id; use a new widget to rename it.")
        row, at = _find(rows, op.id)
        row["widgets"][at] = {**op.widget, "id": op.id}
    elif isinstance(op, MoveWidget):
        row, at = _find(rows, op.id)
        widget = row["widgets"].pop(at)
        _place(rows, widget, op.row, op.position)
    elif isinstance(op, RemoveWidget):
        row, at = _find(rows, op.id)
        row["widgets"].pop(at)
    elif isinstance(op, AddParameter):
        if op.name in out["parameters"]:
            raise _Refused(f"The dashboard already has a parameter {op.name!r}.")
        out["parameters"][op.name] = op.parameter
    elif isinstance(op, UpdateParameter):
        _parameter(out, op.name)
        out["parameters"][op.name] = op.parameter
    elif isinstance(op, RemoveParameter):
        _parameter(out, op.name)
        del out["parameters"][op.name]


def _ids(rows: list[dict]) -> set[str]:
    return {w.get("id") for row in rows for w in row.get("widgets", [])}


def _find(rows: list[dict], widget_id: str) -> tuple[dict, int]:
    for row in rows:
        for at, widget in enumerate(row.get("widgets", [])):
            if widget.get("id") == widget_id:
                return row, at
    raise _Refused(f"The dashboard has no widget {widget_id!r}.")


def _parameter(out: dict, name: str) -> None:
    if name not in out["parameters"]:
        raise _Refused(f"The dashboard has no parameter {name!r}.")


def _place(rows: list[dict], widget: dict, row: int | None, position: int | None) -> None:
    if row is None or row == len(rows):
        rows.append({"widgets": [widget]})
        return
    if row > len(rows):
        raise _Refused(
            f"There is no row {row}; rows count from 0, and row {len(rows)} is a new one "
            "at the end."
        )
    widgets = rows[row]["widgets"]
    widgets.insert(len(widgets) if position is None else position, widget)
