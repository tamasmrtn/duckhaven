"""The judged tier's logic, tested without a judge.

Tier 2 itself costs money and needs a key, so almost none of it can run in CI.
What *can* — and what is most likely to be subtly wrong — is the arithmetic
around the judge: how two orderings combine into one verdict, how a flip is
counted, and when a run is declared untrustworthy. Those are pure functions and
they are tested here, free, on every pull request.

The rubrics are checked for the clauses that carry their meaning rather than
word for word. A rubric is prompt engineering: it should be editable without
breaking a test, but the two override rules are the reason the scale produces
the right answer on negative cases, and losing them silently would gut the tier.
"""

import pytest

from tests.evals import judge
from tests.evals.compare import MAX_TRUSTWORTHY_FLIP_RATE, _report
from tests.evals.judge import CaseScore, resolve_pair, summarise_scores

# ── Combining the two orderings ───────────────────────────────────────────────
#
# resolve_pair takes the verdict with A shown first and the verdict with B shown
# first. Both name a *slot*, so the second must be read inverted. A win requires
# the judge to agree with itself after the swap.


@pytest.mark.parametrize(
    ("shown_a_first", "shown_b_first", "winner", "flipped"),
    [
        # Agreement: the judge picked the same answer whichever slot it was in.
        ("1", "2", "A", False),
        ("2", "1", "B", False),
        # Disagreement: it picked slot 1 both times. That is position bias, not
        # a preference, and it must not be recorded as a win for anyone.
        ("1", "1", "tie", True),
        ("2", "2", "tie", True),
        # A genuine tie either way is a tie, and is not a flip.
        ("tie", "tie", "tie", False),
        ("1", "tie", "tie", False),
        ("tie", "1", "tie", False),
        ("2", "tie", "tie", False),
    ],
)
def test_resolve_pair(shown_a_first, shown_b_first, winner, flipped):
    assert resolve_pair(shown_a_first, shown_b_first) == (winner, flipped)


def test_a_slot_preference_never_becomes_a_win():
    """The failure this guards: judges favour whichever answer is shown first by
    a reported 10-15 points of win rate. Counting one order as evidence would
    manufacture a result out of that bias."""
    always_slot_one = resolve_pair("1", "1")
    always_slot_two = resolve_pair("2", "2")

    assert always_slot_one[0] == "tie"
    assert always_slot_two[0] == "tie"
    assert always_slot_one[1] and always_slot_two[1]


# ── The report ────────────────────────────────────────────────────────────────


def _outcome(winner, *, flipped=False, category="product_knowledge"):
    return {
        "case": "c",
        "category": category,
        "provenance": "hand",
        "negative": False,
        "winner": winner,
        "flipped": flipped,
        "reason": "",
    }


def test_win_rate_is_over_decided_pairs_only():
    """A rate diluted by ties says more about how often the judge could tell
    than about which arm is better."""
    report = _report("a", "b", [_outcome("A"), _outcome("A"), _outcome("B"), _outcome("tie")])

    assert report["wins_a"] == 2
    assert report["wins_b"] == 1
    assert report["ties"] == 1
    assert report["win_rate_a"] == pytest.approx(2 / 3, abs=1e-3)  # report rounds to 3dp


def test_an_all_tie_run_reports_no_win_rate_rather_than_zero():
    """Zero would read as 'A lost'; None reads as 'nothing was decided'."""
    report = _report("a", "b", [_outcome("tie"), _outcome("tie")])

    assert report["win_rate_a"] is None


def test_a_high_flip_rate_marks_the_run_untrustworthy():
    flippy = [_outcome("tie", flipped=True)] * 3 + [_outcome("A")]

    report = _report("a", "b", flippy)

    assert report["flip_rate"] == 0.75
    assert report["trustworthy"] is False


def test_a_normal_flip_rate_leaves_the_run_trustworthy():
    outcomes = [_outcome("A")] * 9 + [_outcome("tie", flipped=True)]

    report = _report("a", "b", outcomes)

    assert report["flip_rate"] == 0.1
    assert report["flip_rate"] <= MAX_TRUSTWORTHY_FLIP_RATE
    assert report["trustworthy"] is True


def test_the_report_records_which_judge_produced_it():
    """Without this an absolute or pairwise number cannot be compared to an older
    one: a shift could be the assistant or the judge, and nothing distinguishes
    them after the fact."""
    report = _report("a", "b", [_outcome("A")])

    assert report["judge_model"] == judge.JUDGE_MODEL
    assert report["judge_temperature"] == 0.0
    assert report["assistant_model"]


def test_results_are_broken_down_by_category():
    """So a regression can be localised rather than just observed."""
    report = _report(
        "a",
        "b",
        [_outcome("A", category="governance"), _outcome("B", category="unanswerable")],
    )

    assert report["by_category"] == {"governance": {"A": 1}, "unanswerable": {"B": 1}}


# ── Absolute scoring ──────────────────────────────────────────────────────────


def _score(name, faithfulness, relevancy=5.0, *, negative=False, category="product_knowledge"):
    return CaseScore(
        case=name,
        category=category,
        provenance="hand",
        negative=negative,
        faithfulness=faithfulness,
        relevancy=relevancy,
        reason="",
    )


def test_one_confabulation_on_a_negative_case_fails_the_run():
    """Even with a strong mean. That single case is the thing this tier exists
    to catch, and an average is exactly the wrong way to look at it."""
    scores = [_score(f"good{i}", 5.0) for i in range(20)]
    scores.append(_score("invented", 1.0, negative=True))

    summary = summarise_scores(scores)

    assert summary["faithfulness"] > judge.MIN_FAITHFULNESS
    assert summary["confabulated_on_negative_cases"] == ["invented"]
    assert summary["passed"] is False


def test_a_low_faithfulness_mean_fails_the_run():
    summary = summarise_scores([_score("a", 3.0), _score("b", 3.0)])

    assert summary["passed"] is False


def test_a_healthy_run_passes():
    summary = summarise_scores([_score("a", 5.0), _score("b", 4.5)])

    assert summary["passed"] is True


def test_scores_are_reported_by_category_and_provenance():
    summary = summarise_scores([_score("a", 5.0), _score("b", 3.0, category="governance")])

    assert summary["faithfulness_by_category"] == {"governance": 3.0, "product_knowledge": 5.0}
    assert summary["faithfulness_by_provenance"] == {"hand": 4.0}


# ── The rubrics ───────────────────────────────────────────────────────────────


def test_the_faithfulness_rubric_keeps_its_two_override_rules():
    """These are why the scale produces the right answer on a negative case:
    admitting ignorance must score top, and a fluent invention must score bottom
    however reasonable it sounds."""
    rubric = judge.FAITHFULNESS_RUBRIC

    assert "Admitting ignorance is faithful" in rubric
    assert "fluent, specific and plausible but describes a feature absent" in rubric
    assert "scores 1, however reasonable it sounds" in rubric


def test_the_relevancy_rubric_does_not_punish_a_refusal():
    rubric = judge.RELEVANCY_RUBRIC

    assert "Do not penalise an answer for being negative" in rubric
    assert "clarifying question is fully relevant" in rubric


def test_the_pairwise_rubric_ranks_correctness_above_everything():
    rubric = judge.PAIRWISE_RUBRIC

    assert "invents a product capability\n   loses to one that says it does not know" in rubric
    assert rubric.index("Correctness against the context") < rubric.index("Concision")


def test_the_judge_is_pinned_and_deterministic():
    assert judge.JUDGE_MODEL
    assert judge.JUDGE_SETTINGS.get("temperature") == 0.0
