"""Join path resolution, and the three ways it refuses.

The rules under test are all refusals, which is the point: every one of them has
a plausible wrong answer sitting right next to it, and picking that answer would
produce a number rather than an error.
"""

from __future__ import annotations

import pytest

from api.services.semantic.errors import SemanticError
from api.services.semantic.joins import MAX_HOPS, reachable, resolve_path
from tests.unit.services.semantic.conftest import (
    make_dataset,
    make_dimension,
    make_metric,
    make_model,
    make_relationship,
)


def test_a_dataset_reaches_itself_without_a_join(star):
    assert resolve_path(star, "orders", "orders") == ()


def test_one_hop(star):
    path = resolve_path(star, "orders", "customers")

    assert [r.name for r in path] == ["orders_to_customers"]


def test_two_hops_are_allowed():
    model = make_model(
        datasets=[
            make_dataset("orders"),
            make_dataset("customers", primary_key=("id",)),
            make_dataset("countries", primary_key=("code",)),
        ],
        relationships=[
            make_relationship("orders_to_customers", "orders", "customers"),
            make_relationship(
                "customers_to_countries",
                "customers",
                "countries",
                columns=(("country_code", "code"),),
            ),
        ],
    )

    path = resolve_path(model, "orders", "countries")

    assert [r.name for r in path] == ["orders_to_customers", "customers_to_countries"]
    assert len(path) == MAX_HOPS


def test_three_hops_are_refused():
    """The depth limit, which is about answers a person can still check.

    Three joins deep the query stops being obviously right to whoever reads it,
    and an answer nobody can verify is worth less than a refusal.
    """
    model = make_model(
        datasets=[
            make_dataset("a"),
            make_dataset("b", primary_key=("id",)),
            make_dataset("c", primary_key=("id",)),
            make_dataset("d", primary_key=("id",)),
        ],
        relationships=[
            make_relationship("a_b", "a", "b", columns=(("b_id", "id"),)),
            make_relationship("b_c", "b", "c", columns=(("c_id", "id"),)),
            make_relationship("c_d", "c", "d", columns=(("d_id", "id"),)),
        ],
    )

    with pytest.raises(SemanticError, match="cannot be reached"):
        resolve_path(model, "a", "d")


def test_traversal_never_goes_the_fan_out_way(star):
    """``many_to_one`` is directed. Walking it backwards multiplies fact rows."""
    with pytest.raises(SemanticError, match="cannot be reached"):
        resolve_path(star, "customers", "orders")


def test_two_paths_to_the_same_dataset_are_ambiguous():
    """Both paths named in the error, neither one chosen.

    An order's country could be the customer's or the shipping address's. Those
    are different numbers, and only a person knows which was meant.
    """
    model = make_model(
        datasets=[
            make_dataset("orders"),
            make_dataset("customers", primary_key=("id",)),
            make_dataset("addresses", primary_key=("id",)),
            make_dataset("countries", primary_key=("code",)),
        ],
        relationships=[
            make_relationship("via_customer", "orders", "customers"),
            make_relationship(
                "via_address", "orders", "addresses", columns=(("address_id", "id"),)
            ),
            make_relationship(
                "customer_country",
                "customers",
                "countries",
                columns=(("country_code", "code"),),
            ),
            make_relationship(
                "address_country",
                "addresses",
                "countries",
                columns=(("country_code", "code"),),
            ),
        ],
    )

    with pytest.raises(SemanticError) as excinfo:
        resolve_path(model, "orders", "countries")

    message = str(excinfo.value)
    assert "more than one way" in message
    assert "customer_country" in message
    assert "address_country" in message


def test_an_ambiguous_dataset_is_absent_from_reachable():
    """Not usable without a decision, so not offered as something that works."""
    model = make_model(
        datasets=[
            make_dataset("orders"),
            make_dataset("customers", primary_key=("id",)),
            make_dataset("addresses", primary_key=("id",)),
            make_dataset("countries", primary_key=("code",)),
        ],
        relationships=[
            make_relationship("via_customer", "orders", "customers"),
            make_relationship(
                "via_address", "orders", "addresses", columns=(("address_id", "id"),)
            ),
            make_relationship(
                "customer_country", "customers", "countries", columns=(("cc", "code"),)
            ),
            make_relationship(
                "address_country", "addresses", "countries", columns=(("cc", "code"),)
            ),
        ],
    )

    assert set(reachable(model, "orders")) == {"customers", "addresses"}


def test_an_unreachable_target_lists_what_is_reachable(star):
    model = make_model(
        datasets=[make_dataset("orders"), make_dataset("marketing")],
        metrics=[make_metric("revenue", "orders")],
        dimensions=[make_dimension("channel", "marketing")],
    )

    with pytest.raises(SemanticError) as excinfo:
        resolve_path(model, "orders", "marketing")

    assert "cannot be reached" in str(excinfo.value)


def test_a_cycle_does_not_loop_forever():
    model = make_model(
        datasets=[
            make_dataset("a", primary_key=("id",)),
            make_dataset("b", primary_key=("id",)),
        ],
        relationships=[
            make_relationship("a_b", "a", "b", columns=(("b_id", "id"),)),
            make_relationship("b_a", "b", "a", columns=(("a_id", "id"),)),
        ],
    )

    assert [r.name for r in resolve_path(model, "a", "b")] == ["a_b"]
