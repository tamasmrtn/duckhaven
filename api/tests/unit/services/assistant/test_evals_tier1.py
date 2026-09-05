"""Tier 1 of the eval harness: everything checkable without a model.

Runs in ``make test-api`` with no provider key and no Postgres. What it can
honestly check is the harness itself and the case set — the metrics compute what
they claim, the cases are well-formed and point at pages that exist, and an arm
genuinely changes the assistant's configuration rather than merely being labelled
differently.

What it deliberately does **not** do is script a model to call a tool and then
assert the tool was called. That tests the script, not the assistant. Behaviour
scoring needs a real model deciding for itself, which is tier 2 — see
``docs/developer/testing.md``. Retrieval scoring needs Postgres and lives in
``api/tests/integration/test_docs_search.py``.
"""

import pytest
from pydantic_ai import DeferredToolRequests
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from api.config import settings
from api.services.assistant.knowledge import generate
from api.services.assistant.knowledge.loader import load_index
from tests.evals import metrics
from tests.evals.harness import ArmConfig, deps_for, run_case

DOCS_DIR = generate._repo_root() / "docs"


@pytest.fixture(autouse=True)
def _docs_from_the_checkout(monkeypatch):
    monkeypatch.setattr(settings, "assistant_docs_dir", DOCS_DIR)
    load_index.cache_clear()
    yield
    load_index.cache_clear()


# ── The metrics compute what they claim ───────────────────────────────────────


@pytest.mark.parametrize(
    ("retrieved", "expected", "k", "want"),
    [
        (["a", "b", "c"], ("a",), 5, 1.0),
        (["a", "b", "c"], ("c",), 5, 1.0),
        (["a", "b", "c"], ("z",), 5, 0.0),
        (["a", "b", "c"], ("c",), 2, 0.0),  # outside k
        (["a"], ("a", "b"), 5, 1.0),  # any expected page counts
        ([], ("a",), 5, 0.0),
        (["a"], (), 5, 1.0),  # nothing expected, nothing to miss
    ],
)
def test_recall_at_k(retrieved, expected, k, want):
    assert metrics.recall_at_k(retrieved, expected, k) == want


@pytest.mark.parametrize(
    ("retrieved", "expected", "want"),
    [
        (["a", "b"], ("a",), 1.0),
        (["a", "b"], ("b",), 0.5),
        (["a", "b", "c", "d"], ("d",), 0.25),
        (["a"], ("z",), 0.0),
        (["b", "a"], ("a", "b"), 1.0),  # first hit wins
    ],
)
def test_reciprocal_rank(retrieved, expected, want):
    assert metrics.reciprocal_rank(retrieved, expected) == want


@pytest.mark.parametrize(
    ("answer", "want"),
    [
        ("DuckHaven does not expire snapshots.", True),
        ("I don't know — the documentation does not cover that.", True),
        ("This assistant is read-only.", True),
        ("There is no GraphQL endpoint.", True),
        ("Snapshot cleanup is a roadmap item.", True),
        ("I could not access that catalog.", True),
        # The false-positive direction matters more: a confident wrong answer
        # slipping through a negative case is the failure this feature risks.
        ("Set retention to 30 days in the table settings.", False),
        ("Revenue last month was 1.2M.", False),
        ("Use AT (TIMESTAMP => '2026-05-01') to read an earlier snapshot.", False),
        ("The orders table has 12 columns.", False),
    ],
)
def test_refusal_detection(answer, want):
    assert metrics.looks_like_refusal(answer) is want


def test_summarise_reports_per_group():
    assert metrics.summarise({"hand": [1.0, 0.0], "auto": [1.0], "empty": []}) == {
        "hand": 0.5,
        "auto": 1.0,
    }


# ── The case set is well-formed ───────────────────────────────────────────────


def test_cases_load_and_are_uniquely_named():
    cases = metrics.load_cases()

    assert len(cases) >= 25
    assert len({c.name for c in cases}) == len(cases)


def test_every_expected_page_is_one_the_tool_would_accept():
    """A case pointing at an unindexed path can never pass, and would look like
    a retrieval failure rather than the case-authoring bug it is."""
    indexed = set(load_index().paths)
    bad = [
        (c.name, source)
        for c in metrics.load_cases()
        for source in c.doc_sources
        if source not in indexed
    ]

    assert bad == []


def test_every_case_declares_a_category_and_provenance():
    allowed_categories = {
        "product_knowledge",
        "catalog_sql",
        "semantic_routing",
        "governance",
        "unanswerable",
    }
    cases = metrics.load_cases()

    assert all(c.category in allowed_categories for c in cases)
    assert all(c.provenance in {"hand", "auto"} for c in cases)


def test_every_category_is_represented():
    """Categories are reported separately so a regression can be localised; an
    empty one silently removes that ability."""
    covered = {c.category for c in metrics.load_cases()}

    assert covered == {
        "product_knowledge",
        "catalog_sql",
        "semantic_routing",
        "governance",
        "unanswerable",
    }


def test_negative_cases_are_a_meaningful_share_of_the_set():
    """They guard the sharpest risk in this feature: an assistant that has read
    the docs and now confabulates fluently."""
    cases = metrics.load_cases()
    negative = [c for c in cases if c.negative]

    assert len(negative) / len(cases) >= 0.3


def test_refusal_cases_say_what_they_expect():
    """A negative case with no expectation recorded is a case nobody can score."""
    vague = [
        c.name
        for c in metrics.load_cases()
        if c.negative and not (c.expect_refusal or c.note or c.expected_sources)
    ]

    assert vague == []


def test_auto_cases_are_never_refusal_cases():
    """A synthesised refusal is a question the synthesiser invented an answer
    for — the one thing auto-synthesis cannot be trusted to produce."""
    bad = [c.name for c in metrics.load_cases() if c.provenance == "auto" and c.negative]

    assert bad == []


# ── An arm genuinely reconfigures the assistant ───────────────────────────────


def test_arms_load_from_the_file():
    arm = ArmConfig.load("with-docs")

    assert arm.docs_enabled is True
    assert arm.workspace["semantic_summary"]


def test_an_arm_can_inherit_and_override():
    cheap = ArmConfig.load("cheaper-model")
    docs = ArmConfig.load("with-docs")

    assert cheap.model != docs.model
    assert cheap.docs_enabled is True
    assert cheap.openai_base_url == docs.openai_base_url


def test_an_unknown_arm_fails_loudly():
    with pytest.raises(KeyError):
        ArmConfig.load("no-such-arm")


def test_the_two_comparison_arms_differ_only_in_knowledge():
    """If they differed in the workspace too, a win could not be attributed."""
    baseline = ArmConfig.load("baseline")
    with_docs = ArmConfig.load("with-docs")

    assert baseline.workspace == with_docs.workspace
    assert baseline.docs_enabled != with_docs.docs_enabled


def _echo_model(text: str = "ok", tool: tuple[str, dict] | None = None) -> FunctionModel:
    """Answers immediately, optionally calling one tool first."""
    calls = iter([tool, None] if tool else [None])

    def function(messages, info) -> ModelResponse:
        step = next(calls, None)
        if step is None:
            return ModelResponse(parts=[TextPart(text)])
        return ModelResponse(parts=[ToolCallPart(step[0], step[1])])

    return FunctionModel(function)


async def test_arms_produce_different_prompts_and_toolsets():
    """The arm must reach the real assembly, or a comparison measures nothing."""
    baseline = await run_case(ArmConfig.load("baseline"), "hi", model=_echo_model())
    with_docs = await run_case(ArmConfig.load("with-docs"), "hi", model=_echo_model())

    assert "About DuckHaven, the product you run inside" not in baseline.instructions
    assert "About DuckHaven, the product you run inside" in with_docs.instructions
    assert "reference/sql-support.md" in with_docs.instructions
    # Both still carry the workspace's semantic models: the arms differ in
    # knowledge alone, which is what makes the comparison attributable.
    assert "curated semantic models" in baseline.instructions
    assert "curated semantic models" in with_docs.instructions


def _tool_capturing_model(seen: list[list[str]]) -> FunctionModel:
    """Records the tool schemas the model was actually offered."""

    def function(messages, info) -> ModelResponse:
        seen.append(sorted(t.name for t in info.function_tools))
        return ModelResponse(parts=[TextPart("ok")])

    return FunctionModel(function)


async def test_an_arm_changes_which_tools_the_model_is_offered():
    """The precise check: not what the model tried, but what it was given. A
    tool absent from the schema is unreachable however the model is prompted."""
    baseline_tools: list[list[str]] = []
    docs_tools: list[list[str]] = []

    await run_case(ArmConfig.load("baseline"), "hi", model=_tool_capturing_model(baseline_tools))
    await run_case(ArmConfig.load("with-docs"), "hi", model=_tool_capturing_model(docs_tools))

    assert "read_doc_page" not in baseline_tools[0]
    assert "search_docs" not in baseline_tools[0]
    assert "read_doc_page" in docs_tools[0]
    assert "search_docs" in docs_tools[0]
    # Everything else is unchanged: the arms differ in knowledge, nothing else.
    assert set(docs_tools[0]) - set(baseline_tools[0]) == {"read_doc_page", "search_docs"}


def test_the_everything_arm_turns_every_conditional_block_on():
    """It exists so the budget ceiling is measured against the largest prompt the
    product can actually produce, not the smallest."""
    deps = deps_for(ArmConfig.load("everything"))

    assert deps.semantic_summary
    assert deps.storage_kinds == ("s3", "adls_gen2")
    assert deps.elastic_enabled is True
    assert deps.agent_count == 3


# ── The harness plumbing ──────────────────────────────────────────────────────


async def test_a_run_records_the_answer_and_the_tools_used():
    """A smoke test of the recording, not of the assistant: the model is scripted,
    so what is asserted is that the harness captures what happened."""
    result = await run_case(
        ArmConfig.load("with-docs"),
        "how does time travel work?",
        model=_echo_model(
            "Use AT (TIMESTAMP => ...)",
            tool=("read_doc_page", {"path": "reference/sql-support.md"}),
        ),
        case_name="smoke",
    )

    assert result.arm == "with-docs"
    assert result.case == "smoke"
    assert "AT (TIMESTAMP" in result.answer
    assert result.tools_called == ["read_doc_page"]
    assert result.doc_paths == ["reference/sql-support.md"]


async def test_an_arms_settings_are_restored_after_a_run():
    """Arms mutate process-wide settings; a leak would silently contaminate every
    later case and, worse, every other test in the suite."""
    before = settings.assistant_docs_enabled

    await run_case(ArmConfig.load("baseline"), "hi", model=_echo_model())

    assert settings.assistant_docs_enabled == before


# ── Citations ─────────────────────────────────────────────────────────────────


def test_citation_presence_rewards_a_real_path():
    indexed = set(load_index().paths)

    assert metrics.citation_presence("See reference/sql-support.md.", indexed) == 1.0


def test_an_invented_path_scores_zero_rather_than_being_ignored():
    """The user sees citations as links, so a path that does not exist is a
    broken link and a small confabulation of its own."""
    indexed = set(load_index().paths)

    assert metrics.citation_presence("See reference/made-up-page.md.", indexed) == 0.0


def test_an_answer_that_cites_nothing_is_unscored_rather_than_failed():
    """Not every product answer needs a citation, and scoring those zero would
    push the assistant towards citing something for the sake of it."""
    assert metrics.citation_presence("DuckHaven does not expire snapshots.", set()) is None


def test_cited_paths_finds_every_path_named():
    answer = "See reference/sql-support.md and guides/snapshots-time-travel.md."

    assert metrics.cited_paths(answer) == {
        "reference/sql-support.md",
        "guides/snapshots-time-travel.md",
    }


# ── An arm reaches the model the way production does ──────────────────────────


def test_an_arm_can_target_an_openai_compatible_endpoint():
    """Ollama, vLLM, Azure — DuckHaven's keyless path. The harness must build the
    model through the same function production uses, or an arm naming a base URL
    would silently score against whatever the default provider happened to be."""
    from tests.evals.harness import _arm_settings, build_agent

    arm = ArmConfig.load("with-docs")
    with _arm_settings(arm):
        model = build_agent(arm).model

    # Asserts the wiring, not the choice: which model an arm names is meant to
    # change, and a test that pins the string turns every model swap into a
    # failing suite.
    assert type(model).__name__ == "OpenAIChatModel"
    assert model.model_name == arm.model
    assert str(model.client.base_url).startswith(arm.openai_base_url)


def test_an_arm_can_override_only_the_model():
    """The model-selection use case: everything inherited, one thing changed."""
    from api.services.assistant.agent import _build_model
    from tests.evals.harness import _arm_settings

    with _arm_settings(ArmConfig.load("cheaper-model")):
        assert _build_model().model_name == "gpt-oss:120b-cloud"


def test_an_arm_restores_every_setting_it_touched():
    """Arms mutate process-wide settings. A leaked base URL would redirect every
    later case — and every other test in the suite — at the wrong endpoint."""
    from tests.evals.harness import _arm_settings

    before = (
        settings.assistant_docs_enabled,
        settings.assistant_model,
        settings.assistant_openai_base_url,
    )

    with _arm_settings(ArmConfig.load("baseline")):
        assert settings.assistant_openai_base_url == "https://ollama.com/v1"

    assert (
        settings.assistant_docs_enabled,
        settings.assistant_model,
        settings.assistant_openai_base_url,
    ) == before


# ── Tool arguments arrive in two shapes ───────────────────────────────────────


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        # Anthropic and friends hand back a dict.
        ({"path": "reference/sql-support.md"}, {"path": "reference/sql-support.md"}),
        # Every OpenAI-compatible endpoint — Ollama, vLLM, Azure — sends a string.
        ('{"path": "reference/sql-support.md"}', {"path": "reference/sql-support.md"}),
        ("", {}),
        ("{not json", {}),
        ({}, {}),
    ],
)
def test_tool_args_are_read_whichever_form_the_provider_sends(args, expected):
    """The string form was treated as "no arguments", silently and totally: over a
    full run read_doc_page was called 19 times and the page recorded 0 times. The
    judge then never saw what the assistant had read, and scored correct citations
    as fabrications."""
    from pydantic_ai.messages import ToolCallPart

    from tests.evals.harness import _tool_args

    assert _tool_args(ToolCallPart("read_doc_page", args)) == expected


async def test_a_read_page_is_recorded_from_a_string_argument():
    """End to end through run_case, because the unit above would still pass if
    run_case stopped calling it."""

    def model_reading_a_page() -> FunctionModel:
        steps = iter(
            [
                ModelResponse(
                    parts=[
                        ToolCallPart(
                            "read_doc_page",
                            '{"path": "reference/sql-support.md"}',
                        )
                    ]
                ),
                ModelResponse(parts=[TextPart("done")]),
            ]
        )

        def function(messages, info) -> ModelResponse:
            return next(steps)

        return FunctionModel(function)

    result = await run_case(
        ArmConfig.load("with-docs"), "what statements are allowed?", model=model_reading_a_page()
    )

    assert result.tools_called == ["read_doc_page"]
    assert result.doc_paths == ["reference/sql-support.md"]


# ── The harness matches production's agent, not a subset of it ────────────────


def test_the_eval_agent_carries_productions_settings():
    """build_agent reproduced three of agent.py's eight arguments, and every
    omission was silent. This pins the ones that changed behaviour."""
    from pydantic_ai import DeferredToolRequests

    from tests.evals.harness import build_agent

    agent = build_agent(ArmConfig.load("with-docs"), model=_echo_model())

    assert DeferredToolRequests in agent.output_type
    assert agent.model_settings["max_tokens"] == settings.assistant_max_output_tokens


async def test_a_looping_case_is_recorded_not_raised():
    """One case that never terminates must not discard the other forty-one, and
    "it never finished" is itself a result worth scoring."""

    def endless_tool_caller() -> FunctionModel:
        def function(messages, info) -> ModelResponse:
            return ModelResponse(parts=[ToolCallPart("list_catalogs", {})])

        return FunctionModel(function)

    from tests.evals.fixtures import EvalGateway

    result = await run_case(
        ArmConfig.load("with-docs"),
        "loop forever",
        model=endless_tool_caller(),
        gateway=EvalGateway(),
        case_name="looping",
    )

    assert "step limit" in result.answer
    assert result.case == "looping"


def test_a_paused_write_reads_as_paused_rather_than_as_an_answer():
    """Refusing, complying and pausing for approval are three different
    behaviours; str() on the raw object would flatten them into one."""
    from types import SimpleNamespace

    from tests.evals.harness import _render_output

    deferred = DeferredToolRequests(
        approvals=[SimpleNamespace(tool_name="run_sql", tool_call_id="1", args={})]
    )

    rendered = _render_output(deferred)

    assert "paused" in rendered and "run_sql" in rendered
    assert _render_output("an ordinary answer") == "an ordinary answer"


def test_the_judge_client_is_built_once():
    """It was rebuilt per call: 168 calls, 168 connection pools, dozens of idle
    TLS connections against the provider."""
    from tests.evals.judge import _graded_agent, _pairwise_agent, judge_model

    assert judge_model() is judge_model()
    assert _graded_agent() is _graded_agent()
    assert _graded_agent() is not _pairwise_agent()


async def test_malformed_structured_output_is_resampled_not_fatal():
    """The expected Ollama failure: a smaller model returning output that does
    not validate. One bad sample must not end a forty-minute run."""
    from pydantic_ai.exceptions import UnexpectedModelBehavior

    from tests.evals.harness import retrying

    attempts = {"n": 0}

    async def flaky():
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise UnexpectedModelBehavior("Exceeded maximum retries for result validation")
        return "ok"

    assert await retrying(flaky, base_delay=0.01) == "ok"
    assert attempts["n"] == 2
