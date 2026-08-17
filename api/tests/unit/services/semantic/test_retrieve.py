"""Matching what somebody said to what a definition is called.

Two properties carry most of the weight. Synonyms have to work, because they are
the whole reason "turnover" finds revenue instead of finding nothing. And ranking
has to be deterministic, because an assistant that answers the same question two
different ways on two runs cannot be debugged.
"""

from __future__ import annotations

from api.services.semantic.retrieve import ambiguous, search
from tests.unit.services.semantic.conftest import (
    make_dataset,
    make_dimension,
    make_metric,
    make_model,
)


def names(hits):
    return [h.name for h in hits]


def test_an_exact_name_wins(star):
    hits = search([star], "what was revenue last month?")

    assert hits[0].name == "revenue"
    assert hits[0].kind == "metric"


def test_a_synonym_finds_the_metric(star):
    """The reason synonyms exist: nobody types the column name."""
    assert search([star], "what was our turnover?")[0].name == "revenue"
    assert search([star], "show me GMV")[0].name == "revenue"


def test_a_multi_word_synonym_matches_inside_a_sentence(star):
    assert "segment" in names(search([star], "break it down by customer segment"))


def test_a_dimension_is_found_too(star):
    assert "country" in names(search([star], "revenue by country"))


def test_a_dimension_synonym_works(star):
    assert "country" in names(search([star], "revenue by nation"))


def test_metrics_outrank_dimensions_at_equal_score():
    """A question is usually about what to measure; the slice is secondary."""
    model = make_model(
        datasets=[make_dataset("orders")],
        dimensions=[make_dimension("churn", "orders")],
        metrics=[make_metric("churn_rate", "orders", synonyms=("churn",), time_dimension=None)],
    )

    hits = search([model], "churn")

    assert hits[0].kind == "metric"


def test_ranking_is_deterministic(star):
    """Same question, same order, every time — or nobody can debug a bad answer."""
    first = search([star], "revenue by country and segment")
    second = search([star], "revenue by country and segment")

    assert names(first) == names(second)


def test_an_unrelated_question_matches_nothing(star):
    assert search([star], "what is the weather like") == []


def test_an_empty_question_matches_nothing(star):
    assert search([star], "   ") == []


def test_stop_words_alone_do_not_match(star):
    assert search([star], "how many do we") == []


def test_results_are_capped(star):
    assert len(search([star], "revenue country segment category orders customers", limit=2)) == 2


def test_search_spans_several_models():
    sales = make_model(
        datasets=[make_dataset("orders")],
        metrics=[make_metric("revenue", "orders", time_dimension=None)],
        slug="sales",
    )
    marketing = make_model(
        datasets=[make_dataset("visits")],
        metrics=[make_metric("sessions", "visits", agg="count", expr=None, time_dimension=None)],
        slug="marketing",
    )

    hits = search([sales, marketing], "how many sessions and how much revenue")

    assert {h.model for h in hits} == {"sales", "marketing"}


def test_a_tie_between_two_metrics_is_reported_as_ambiguous():
    """ "How many customers?" against two definitions must produce a question.

    Both metrics answer to the same word and mean different things. Returning the
    tie is what lets the assistant ask instead of taking whichever sorted first.
    """
    model = make_model(
        datasets=[make_dataset("customers", primary_key=("id",))],
        metrics=[
            make_metric(
                "total_customers",
                "customers",
                agg="count",
                expr=None,
                synonyms=("customers",),
                time_dimension=None,
            ),
            make_metric(
                "active_customers",
                "customers",
                agg="count_distinct",
                expr="id",
                synonyms=("customers",),
                time_dimension=None,
            ),
        ],
    )

    hits = search([model], "how many customers do we have?")
    tied = ambiguous(hits)

    assert {h.name for h in tied} == {"total_customers", "active_customers"}


def test_a_clear_winner_is_not_ambiguous(star):
    assert ambiguous(search([star], "revenue")) == []


def test_a_single_match_is_not_ambiguous(star):
    assert ambiguous(search([star], "unique customers")) == []


def test_hits_carry_what_is_needed_to_act(star):
    hit = search([star], "revenue")[0]
    payload = hit.as_dict()

    assert payload["model"] == "sales"
    assert payload["expression"] == "SUM(total_amount) FILTER (WHERE status <> 'test')"
    assert payload["time_dimension"] == "order_date"
    assert "test orders" in payload["caveat"]


def test_dimension_hits_carry_sample_values(star):
    """So a filter can be written against what is stored, not what was said."""
    hit = next(h for h in search([star], "country") if h.kind == "dimension")

    assert "United States" in hit.as_dict()["sample_values"]
