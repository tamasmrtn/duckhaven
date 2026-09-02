"""How the per-run instructions are assembled.

Two properties are worth guarding here. The first is that a workspace without a
feature gets *no text about it* — not a sentence saying the feature is off — so a
deployment that uses none of them keeps the assistant it already had. The second
is that the always-resident blocks stay within budget: they compete with the
conversation history for the input window, and nothing else in the codebase would
notice them growing.
"""

import uuid
from types import SimpleNamespace

from api.config import settings
from api.services.assistant.deps import AssistantDeps
from api.services.assistant.prompts import (
    BASE_PROMPT,
    ELASTIC_PROMPT,
    FLEET_PROMPT,
    PRODUCT_PROMPT,
    SEMANTIC_PROMPT,
    STORAGE_PROMPT,
    SYSTEM_PROMPT,
    build_instructions,
)


def ctx(**kwargs) -> SimpleNamespace:
    """A RunContext stand-in. ``build_instructions`` reads only ``deps``."""
    return SimpleNamespace(
        deps=AssistantDeps(
            gateway=None,  # never touched while assembling instructions
            catalog="warehouse",
            can_write=False,
            query_timeout_s=30.0,
            service_account_id=uuid.uuid4(),
            **kwargs,
        )
    )


# ── The static prompt ─────────────────────────────────────────────────────────


def test_system_prompt_instructs_to_ask_clarifying_questions():
    assert "ask a short, specific clarifying question" in SYSTEM_PROMPT
    assert "guessing and running SQL" in SYSTEM_PROMPT


def test_the_product_block_carries_the_facts_that_change_behaviour():
    """Not a paraphrase check — these four decide what the assistant *does*."""
    assert "information_schema.columns" in PRODUCT_PROMPT
    assert "AT (VERSION =>" in PRODUCT_PROMPT
    assert "AT (TIMESTAMP =>" in PRODUCT_PROMPT
    assert "does not expire, roll back, or compact" in PRODUCT_PROMPT


# ── The baseline, and staying out of the way ──────────────────────────────────


def test_a_bare_workspace_gets_base_plus_product_and_nothing_else():
    """The anti-leak snapshot: equality fails if any injector fires uninvited."""
    assert build_instructions(ctx()) == BASE_PROMPT + "\n" + PRODUCT_PROMPT


def test_disabling_product_knowledge_restores_the_original_instructions(monkeypatch):
    """The complete revert, with no rollback: byte-for-byte the prior prompt."""
    monkeypatch.setattr(settings, "assistant_docs_enabled", False)

    assert build_instructions(ctx()) == BASE_PROMPT


def test_a_bare_workspace_is_told_nothing_about_features_it_lacks():
    instructions = build_instructions(ctx())

    assert "curated semantic models" not in instructions
    assert "external object storage" not in instructions
    assert "elastic compute" not in instructions
    assert "compute agents are available" not in instructions


# ── Each injector fires only on its own trigger ───────────────────────────────


def test_semantic_block_appears_only_with_published_models():
    summary = "  - sales (12 metrics) — Orders and revenue."

    assert SEMANTIC_PROMPT.format(models=summary) in build_instructions(
        ctx(semantic_summary=summary)
    )
    assert "search_semantic FIRST" not in build_instructions(ctx(semantic_summary=None))


def test_storage_block_names_only_the_external_backends():
    instructions = build_instructions(ctx(storage_kinds=("adls_gen2", "object_store", "s3")))

    assert STORAGE_PROMPT.format(kinds="adls_gen2, s3") in instructions
    assert "object_store" not in instructions


def test_the_bundled_object_store_alone_says_nothing():
    """The default backend is not a feature to explain."""
    assert "external object storage" not in build_instructions(ctx(storage_kinds=("object_store",)))


def test_elastic_block_follows_the_deployment_setting():
    assert ELASTIC_PROMPT in build_instructions(ctx(elastic_enabled=True))
    assert "elastic compute" not in build_instructions(ctx(elastic_enabled=False))


def test_fleet_block_appears_only_when_there_is_a_choice_to_make():
    assert FLEET_PROMPT.format(n=3) in build_instructions(ctx(agent_count=3))
    for count in (None, 0, 1):
        assert "compute agents are available" not in build_instructions(ctx(agent_count=count))


# ── Assembly ──────────────────────────────────────────────────────────────────


def test_blocks_keep_a_fixed_order_regardless_of_which_are_present():
    """A prompt that reshuffles between turns is needlessly hard to debug, and
    an unstable prefix defeats prompt caching."""
    instructions = build_instructions(
        ctx(
            semantic_summary="  - sales (12 metrics)",
            storage_kinds=("s3",),
            elastic_enabled=True,
            agent_count=4,
        )
    )
    positions = [
        instructions.index(marker)
        for marker in (
            "You are DuckHaven's data assistant",
            "About DuckHaven, the product you run inside",
            "This workspace has curated semantic models",
            "This workspace reaches external object storage",
            "This deployment has elastic compute",
            "compute agents are available",
        )
    ]

    assert positions == sorted(positions)


def test_every_block_is_separated_by_a_blank_line():
    instructions = build_instructions(ctx(elastic_enabled=True))

    assert "\n\nThis deployment has elastic compute" in instructions


# ── Budget ────────────────────────────────────────────────────────────────────
#
# Asserted in characters, not tokens: no tokenizer is a dependency of this repo
# and adding one to guard a prompt size is not worth it. Divide by four for a
# rough token count. Ceilings sit above today's sizes with room for an edit, so
# that growing a block past one is a deliberate act rather than an accident.


def test_each_resident_block_is_within_budget():
    assert len(BASE_PROMPT) <= 2_600
    assert len(PRODUCT_PROMPT) <= 2_800


def test_the_conditional_blocks_stay_small():
    assert len(SEMANTIC_PROMPT) <= 1_800
    assert len(STORAGE_PROMPT) + len(ELASTIC_PROMPT) + len(FLEET_PROMPT) <= 1_000


def test_the_assembled_instructions_are_within_budget():
    """~1,200 tokens for a bare workspace, ~1,800 with every feature on."""
    assert len(build_instructions(ctx())) <= 5_200

    everything = build_instructions(
        ctx(
            semantic_summary="  - sales (12 metrics) — Orders and revenue.",
            storage_kinds=("s3", "adls_gen2"),
            elastic_enabled=True,
            agent_count=3,
        )
    )

    assert len(everything) <= 7_600
