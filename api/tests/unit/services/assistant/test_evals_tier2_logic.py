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


def _rep(outcomes):
    return _report("a", "b", outcomes, "model-a", "model-b")


def test_win_rate_is_over_decided_pairs_only():
    """A rate diluted by ties says more about how often the judge could tell
    than about which arm is better."""
    report = _rep([_outcome("A"), _outcome("A"), _outcome("B"), _outcome("tie")])

    assert report["wins_a"] == 2
    assert report["wins_b"] == 1
    assert report["ties"] == 1
    assert report["win_rate_a"] == pytest.approx(2 / 3, abs=1e-3)  # report rounds to 3dp


def test_an_all_tie_run_reports_no_win_rate_rather_than_zero():
    """Zero would read as 'A lost'; None reads as 'nothing was decided'."""
    report = _rep([_outcome("tie"), _outcome("tie")])

    assert report["win_rate_a"] is None


def test_a_high_flip_rate_marks_the_run_untrustworthy():
    flippy = [_outcome("tie", flipped=True)] * 3 + [_outcome("A")]

    report = _rep(flippy)

    assert report["flip_rate"] == 0.75
    assert report["trustworthy"] is False


def test_a_normal_flip_rate_leaves_the_run_trustworthy():
    outcomes = [_outcome("A")] * 9 + [_outcome("tie", flipped=True)]

    report = _rep(outcomes)

    assert report["flip_rate"] == 0.1
    assert report["flip_rate"] <= MAX_TRUSTWORTHY_FLIP_RATE
    assert report["trustworthy"] is True


def test_plain_ties_do_not_dilute_the_flip_rate():
    """A tie the judge reached in both orders cannot flip, so counting it in the
    denominator hides position bias behind cases the rubric could not separate.
    Half the comparable pairs flipping is not a 12% flip rate."""
    outcomes = [_outcome("A")] * 6 + [_outcome("tie", flipped=True)] * 6 + [_outcome("tie")] * 30

    report = _rep(outcomes)

    assert report["flip_rate"] == 0.5
    assert report["trustworthy"] is False


def test_a_run_the_judge_never_decided_reports_no_flip_rate():
    """Nothing was comparable, so there is no rate — None, not a reassuring 0.0."""
    report = _rep([_outcome("tie"), _outcome("tie")])

    assert report["flip_rate"] is None
    assert report["trustworthy"] is False


def test_the_report_records_which_judge_produced_it():
    """Without this an absolute or pairwise number cannot be compared to an older
    one: a shift could be the assistant or the judge, and nothing distinguishes
    them after the fact."""
    report = _rep([_outcome("A")])

    assert report["judge_model"] == judge.JUDGE_MODEL
    assert report["judge_temperature"] == 0.0
    # The arms' own models, not the process default. Asserting merely truthy let
    # the report record a model that never ran.
    assert (report["model_a"], report["model_b"]) == ("model-a", "model-b")


def test_results_are_broken_down_by_category():
    """So a regression can be localised rather than just observed."""
    report = _rep([_outcome("A", category="governance"), _outcome("B", category="unanswerable")])

    assert report["by_category"] == {"governance": {"A": 1}, "unanswerable": {"B": 1}}


# ── Absolute scoring ──────────────────────────────────────────────────────────


def _score(
    name,
    faithfulness,
    relevancy=5.0,
    *,
    sample=0,
    negative=False,
    category="product_knowledge",
    grounded=True,
):
    return CaseScore(
        case=name,
        category=category,
        provenance="hand",
        negative=negative,
        grounded=grounded,
        faithfulness=faithfulness,
        relevancy=relevancy,
        reason="",
        sample=sample,
    )


def test_one_confabulation_on_a_negative_case_fails_the_run():
    """Even with a strong mean. That single case is the thing this tier exists
    to catch, and an average is exactly the wrong way to look at it."""
    scores = [_score(f"good{i}", 5.0) for i in range(20)]
    scores.append(_score("invented", 1.0, negative=True))

    summary = summarise_scores(scores, k=1)

    assert summary["faithfulness"] > judge.MIN_FAITHFULNESS
    assert summary["confabulated_on_negative_cases"] == ["invented"]
    assert summary["passed"] is False


def test_a_low_faithfulness_mean_fails_the_run():
    summary = summarise_scores([_score("a", 3.0), _score("b", 3.0)], k=1)

    assert summary["passed"] is False


def test_a_healthy_run_passes():
    summary = summarise_scores([_score("a", 5.0), _score("b", 4.5)], k=1)

    assert summary["passed"] is True


def test_scores_are_reported_by_category_and_provenance():
    summary = summarise_scores([_score("a", 5.0), _score("b", 3.0, category="governance")], k=1)

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


def test_the_summary_keeps_what_each_case_actually_did():
    """The aggregate names a failing case and nothing else. Without the answer
    and the tool calls beside it, finding out why means re-running — slowly, and
    with no guarantee the assistant makes the same choices twice."""
    scores = [
        judge.CaseScore(
            case="revenue_by_region",
            category="semantic_routing",
            provenance="hand",
            negative=False,
            grounded=False,
            faithfulness=4.0,
            relevancy=5.0,
            reason="grounded",
            answer="Revenue by region is …",
            tools_called=("search_semantic", "query_metric"),
            doc_paths=("concepts/semantic-layer.md",),
        )
    ]

    outcome = judge.summarise_scores(scores, k=1)["outcomes"][0]

    assert outcome["case"] == "revenue_by_region"
    assert outcome["tools_called"] == ["search_semantic", "query_metric"]
    assert outcome["doc_paths"] == ["concepts/semantic-layer.md"]
    assert outcome["answer"].startswith("Revenue by region")


def test_the_faithfulness_mean_covers_only_grounded_cases():
    """Faithfulness is groundedness in retrieved context. Over half the case set
    retrieves none — they ask whether the assistant routed to the semantic layer
    or refused a write — and averaging those in mixes a real signal with a
    meaningless one."""
    scores = [_score("doc", 5.0), _score("behaviour", 1.0, grounded=False)]

    summary = judge.summarise_scores(scores, k=1)

    assert summary["faithfulness"] == 5.0
    assert summary["faithfulness_cases"] == 1
    assert summary["faithfulness_ungrounded"] == 1.0
    assert summary["faithfulness_ungrounded_cases"] == 1


def test_an_ungrounded_case_cannot_fail_the_faithfulness_threshold():
    """It is gated on the behaviour metrics and relevancy instead."""
    scores = [_score("doc", 5.0)] + [
        _score(f"behaviour{i}", 1.0, grounded=False) for i in range(20)
    ]

    assert judge.summarise_scores(scores, k=1)["passed"] is True


def test_confabulation_still_spans_every_negative_case():
    """Thirteen of nineteen negative cases name no documentation, and they are
    the ones most likely to invent a feature. Narrowing this check to grounded
    cases would take the safety net off exactly where it is needed."""
    scores = [_score("good", 5.0)] + [
        _score("invented", 1.0, negative=True, grounded=False, category="unanswerable")
    ]

    summary = judge.summarise_scores(scores, k=1)

    assert summary["confabulated_on_negative_cases"] == ["invented"]
    assert summary["passed"] is False


# ── pass^k: the reliability of a case, not its best sample ────────────────────


def test_pass_hat_k_matches_the_tau_bench_estimator():
    """C(c, k) / C(n, k), the draw-without-replacement estimate that all k
    samples pass. The paper's own example: 4 of 5 passing, k=3 -> 4/10."""
    assert judge.pass_hat_k([True] * 5, 1) == 1.0
    assert judge.pass_hat_k([True, True, True, True, False], 3) == pytest.approx(0.4)


def test_pass_hat_k_needs_at_least_k_samples():
    with pytest.raises(ValueError, match="at least 2 samples"):
        judge.pass_hat_k([True], 2)


def test_a_case_passes_only_when_every_sample_scored_well():
    """A user asks once and gets one answer; nobody resamples and keeps the
    best. One 3 in two is a case that is not reliably good."""
    reliable = [_score("reliable", 5.0, sample=0), _score("reliable", 4.0, sample=1)]
    unreliable = [_score("flaky", 5.0, sample=0), _score("flaky", 3.0, sample=1)]

    summary = judge.summarise_scores([*reliable, *unreliable])

    assert summary["pass_k"] == 0.5
    assert summary["failed_cases"] == ["flaky"]
    assert [o["passed"] for o in summary["outcomes"]] == [True, True, True, False]


def test_pass_k_below_the_bar_fails_the_run_even_with_a_good_mean():
    """This is the point of the metric: a run whose mean is healthy but whose
    answers are only usually good is not a passing run."""
    scores = [_score(f"good{i}", 5.0, sample=sample) for i in range(8) for sample in range(2)]
    scores += [_score("flaky_a", 5.0, sample=0), _score("flaky_a", 3.0, sample=1)]
    scores += [_score("flaky_b", 5.0, sample=0), _score("flaky_b", 3.0, sample=1)]

    summary = judge.summarise_scores(scores)

    assert summary["faithfulness"] >= judge.MIN_FAITHFULNESS
    assert summary["relevancy"] >= judge.MIN_RELEVANCY
    assert summary["pass_k"] == 0.8
    assert summary["passed"] is False


def test_the_pass_k_bar_is_the_gate_at_exactly_nine_in_ten():
    scores = [_score(f"good{i}", 5.0, sample=sample) for i in range(9) for sample in range(2)]
    scores += [_score("flaky", 5.0, sample=0), _score("flaky", 3.0, sample=1)]

    summary = judge.summarise_scores(scores)

    assert summary["pass_k"] == 0.9
    assert summary["passed"] is True


def test_an_ungrounded_sample_passes_on_relevancy_alone():
    """Faithfulness is groundedness in retrieved context. A case with no context
    is gated on relevancy and behaviour, never on having failed to be grounded
    in documentation that does not exist."""
    scores = [
        _score("no_docs", 1.0, relevancy=5.0, grounded=False, sample=sample) for sample in range(2)
    ]

    summary = judge.summarise_scores(scores)

    assert summary["pass_k"] == 1.0
    assert summary["faithfulness_ungrounded"] == 1.0


def test_a_grounded_sample_needs_both_dimensions():
    faithful_but_off_topic = [
        _score("off", 5.0, relevancy=2.0, sample=sample) for sample in range(2)
    ]

    summary = judge.summarise_scores(faithful_but_off_topic)

    assert summary["pass_k"] == 0.0


def test_one_confabulating_sample_fails_the_run():
    """Averaging across samples would hide the one sample that invented a
    feature; the hard gate is per sample on purpose."""
    scores = [_score("good", 5.0, sample=sample) for sample in range(2)]
    scores += [
        _score("invented", 5.0, sample=0, negative=True),
        _score("invented", 1.0, sample=1, negative=True),
    ]

    summary = judge.summarise_scores(scores)

    assert summary["confabulated_on_negative_cases"] == ["invented"]
    assert summary["passed"] is False


def test_the_means_do_not_count_a_sampled_case_twice():
    """Case-level means and pass^k are macro; a case sampled twice must weigh
    the same as one sampled once, or the sampled cases rewrite the headline."""
    scores = [
        _score("twice", 5.0, sample=0),
        _score("twice", 5.0, sample=1),
        _score("once", 1.0, sample=0, grounded=False),
    ]

    summary = judge.summarise_scores(scores, k=1)

    assert summary["cases"] == 2
    assert summary["faithfulness"] == 5.0


def test_the_report_keeps_every_sample_beside_its_case():
    """A case that failed one of two must be checkable: the report carries both
    scores, which sample produced them, and whether each passed."""
    scores = [_score("case_a", 5.0, sample=0), _score("case_a", 2.0, sample=1)]

    outcomes = judge.summarise_scores(scores)["outcomes"]

    assert [o["sample"] for o in outcomes] == [0, 1]
    assert [o["faithfulness"] for o in outcomes] == [5.0, 2.0]
    assert [o["passed"] for o in outcomes] == [True, False]
