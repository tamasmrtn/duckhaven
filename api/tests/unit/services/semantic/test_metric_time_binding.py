"""Which timestamp a metric is measured on.

This is the regression the ``time_dimension`` binding exists for. A fact table
routinely carries several dates — when the order was placed, when the row was
written, when it shipped — and "revenue last month" against the wrong one returns
a different number and no error at all. It is the most expensive kind of wrong
answer because nothing about it looks wrong.
"""

from __future__ import annotations

from datetime import date

import pytest

from api.services.semantic.compile import MetricQuery, compile_metric_query
from api.services.semantic.errors import SemanticError
from api.services.semantic.timespec import TimeRange
from tests.unit.services.semantic.conftest import (
    make_dataset,
    make_dimension,
    make_metric,
    make_model,
)

TODAY = date(2026, 8, 17)


def two_axis_model(*, bound_to: str | None, default: str | None = None):
    """One table, two plausible dates, one of them correct."""
    return make_model(
        datasets=[make_dataset("orders")],
        dimensions=[
            make_dimension(
                "order_date", "orders", kind="time", is_default_time=default == "order_date"
            ),
            make_dimension(
                "created_at", "orders", kind="time", is_default_time=default == "created_at"
            ),
        ],
        metrics=[make_metric("revenue", "orders", time_dimension=bound_to)],
    )


def compiled(model, **kwargs):
    return compile_metric_query(model, MetricQuery(**kwargs), today=TODAY).sql


def test_the_metrics_own_binding_decides_the_grain_column():
    sql = compiled(two_axis_model(bound_to="order_date"), metrics=("revenue",), grain="month")

    assert "DATE_TRUNC('MONTH', orders.order_date)" in sql
    assert "created_at" not in sql


def test_the_binding_also_decides_the_filter_column():
    sql = compiled(
        two_axis_model(bound_to="order_date"),
        metrics=("revenue",),
        time_range=TimeRange(kind="last_complete", grain="month", n=1),
    )

    assert "orders.order_date >=" in sql
    assert "created_at" not in sql


def test_the_binding_wins_over_the_datasets_default():
    """A metric that names its axis is not overridden by the dataset's default."""
    model = two_axis_model(bound_to="order_date", default="created_at")

    sql = compiled(model, metrics=("revenue",), grain="month")

    assert "orders.order_date" in sql
    assert "created_at" not in sql


def test_an_unbound_metric_falls_back_to_the_marked_default():
    model = two_axis_model(bound_to=None, default="order_date")

    assert "orders.order_date" in compiled(model, metrics=("revenue",), grain="month")


def test_an_unbound_metric_with_one_time_dimension_is_unambiguous():
    model = make_model(
        datasets=[make_dataset("orders")],
        dimensions=[make_dimension("order_date", "orders", kind="time")],
        metrics=[make_metric("revenue", "orders", time_dimension=None)],
    )

    assert "orders.order_date" in compiled(model, metrics=("revenue",), grain="month")


def test_two_candidate_axes_and_no_binding_is_refused_not_guessed():
    """The whole failure mode, caught. Guessing here is a wrong number."""
    model = two_axis_model(bound_to=None)

    with pytest.raises(SemanticError, match="No time dimension"):
        compiled(model, metrics=("revenue",), grain="month")


def test_metrics_measured_on_different_dates_cannot_share_a_period_column():
    model = make_model(
        datasets=[make_dataset("orders")],
        dimensions=[
            make_dimension("order_date", "orders", kind="time"),
            make_dimension("shipped_at", "orders", kind="time"),
        ],
        metrics=[
            make_metric("revenue", "orders", time_dimension="order_date"),
            make_metric(
                "shipped_value", "orders", expr="total_amount", time_dimension="shipped_at"
            ),
        ],
    )

    with pytest.raises(SemanticError, match="different dates"):
        compiled(model, metrics=("revenue", "shipped_value"), grain="month")


def test_a_grain_the_dimension_does_not_offer_is_refused():
    model = make_model(
        datasets=[make_dataset("orders")],
        dimensions=[
            make_dimension(
                "order_month", "orders", kind="time", time_grains=("month", "quarter", "year")
            )
        ],
        metrics=[make_metric("revenue", "orders", time_dimension="order_month")],
    )

    with pytest.raises(SemanticError) as excinfo:
        compiled(model, metrics=("revenue",), grain="day")

    message = str(excinfo.value)
    assert "day" in message
    assert "month" in message


def test_a_metric_bound_to_a_categorical_dimension_is_refused():
    model = make_model(
        datasets=[make_dataset("orders")],
        dimensions=[make_dimension("region", "orders", kind="categorical")],
        metrics=[make_metric("revenue", "orders", time_dimension="region")],
    )

    with pytest.raises(SemanticError, match="not a time dimension"):
        compiled(model, metrics=("revenue",), grain="month")


def test_no_time_request_needs_no_time_dimension():
    """A timeless question must still work on a model with two candidate axes."""
    sql = compiled(two_axis_model(bound_to=None), metrics=("revenue",))

    assert "DATE_TRUNC" not in sql
