"""Run the assistant under a named configuration, and record what it did.

The **arm** is the reusable idea here. An arm names a configuration — which
model, whether product knowledge is on, what the workspace has — and the runner
takes it as an input rather than branching on it. That is what lets one codebase
produce two arms for a comparison, and what makes this harness answer "is the
cheap model good enough?" later without being rewritten.

An arm configures the *real* assembly rather than reimplementing it. It patches
settings and constructs deps, then calls ``build_instructions`` and
``build_toolset`` exactly as the runner does. A harness that assembled its own
prompt would drift, and would then be measuring something the product does not
do — the one failure that makes an eval worse than no eval.

``runner.py`` is deliberately not involved: its SSE streaming, persistence and
identity minting are irrelevant to scoring an answer, and the pattern here is
the one ``test_semantic_tools.py`` already uses to drive the agent directly.
"""

from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic_ai import Agent
from pydantic_ai.messages import ToolCallPart

from api.config import settings
from api.services.assistant.agent import _build_model
from api.services.assistant.deps import AssistantDeps
from api.services.assistant.prompts import build_instructions
from api.services.assistant.tools import build_toolset

ARMS_PATH = Path(__file__).with_name("arms.yaml")


@dataclass(frozen=True)
class ArmConfig:
    """One named configuration of the assistant."""

    name: str
    # A provider string ("anthropic:claude-sonnet-5"), or a bare tag when
    # openai_base_url is set ("glm-5.1:cloud"). None means "whatever this
    # deployment is configured with".
    model: Any = None
    # An OpenAI-compatible endpoint — Ollama Cloud, a self-hosted Ollama, vLLM,
    # Azure. Set it and the arm runs through exactly the path a keyless or
    # self-hosted DuckHaven uses in production.
    openai_base_url: str | None = None
    # The real product switch, not a harness-only flag: drives the
    # product-knowledge block, the page index, and the two documentation tools.
    docs_enabled: bool = True
    # What the workspace has, as the runner would have resolved it.
    workspace: dict = field(default_factory=dict)

    @classmethod
    def load(cls, name: str, path: Path = ARMS_PATH) -> ArmConfig:
        raw = yaml.safe_load(path.read_text())
        if name not in raw:
            raise KeyError(f"unknown arm {name!r}; have {sorted(raw)}")
        spec = dict(raw[name])
        if parent := spec.pop("inherits", None):
            spec = {**dict(yaml.safe_load(path.read_text())[parent]), **spec}
            spec.pop("inherits", None)
        return cls(name=name, **spec)


@dataclass
class RunResult:
    """What one case produced, in the shape the metrics expect."""

    arm: str
    case: str
    answer: str
    tools_called: list[str]
    doc_paths: list[str]
    instructions: str

    @property
    def cited_paths(self) -> list[str]:
        """Pages this turn actually opened or found — the retrieval it did."""
        return self.doc_paths


# Everything an arm can change is a real deployment setting, so an arm can only
# describe a state the product can actually be in.
_ARM_SETTINGS = ("assistant_docs_enabled", "assistant_model", "assistant_openai_base_url")


@contextmanager
def _arm_settings(arm: ArmConfig):
    """Apply an arm's deployment configuration for the duration of a run."""
    previous = {name: getattr(settings, name) for name in _ARM_SETTINGS}
    settings.assistant_docs_enabled = arm.docs_enabled
    if arm.model:
        settings.assistant_model = arm.model
    if arm.openai_base_url:
        settings.assistant_openai_base_url = arm.openai_base_url
    try:
        yield
    finally:
        for name, value in previous.items():
            setattr(settings, name, value)


def deps_for(arm: ArmConfig, *, gateway: Any = None, docs_search: Any = None) -> AssistantDeps:
    """The dependencies the runner would have built for this arm's workspace."""
    return AssistantDeps(
        gateway=gateway,
        catalog="warehouse",
        can_write=arm.workspace.get("can_write", False),
        query_timeout_s=30.0,
        service_account_id=uuid.uuid4(),
        semantic_summary=arm.workspace.get("semantic_summary"),
        storage_kinds=tuple(arm.workspace.get("storage_kinds", ()) or ()) or None,
        elastic_enabled=arm.workspace.get("elastic_enabled", False),
        agent_count=arm.workspace.get("agent_count"),
        docs_search=docs_search,
    )


def build_agent(arm: ArmConfig, model: Any = None) -> Agent:
    """The production agent, configured for this arm.

    Instructions, tools **and the model** all come from the real functions, so an
    arm can only express configurations the product can actually be in. The model
    matters as much as the rest: ``_build_model`` is what turns an OpenAI-compatible
    base URL into a working client, and a harness that passed the model string
    straight through would silently ignore it — scoring an Ollama or vLLM
    deployment against Anthropic, or failing outright.

    ``model`` overrides everything and takes a constructed object, which is how
    tier 1 injects a ``FunctionModel`` without touching provider settings.
    """
    return Agent(
        model or _build_model(),
        deps_type=AssistantDeps,
        instructions=build_instructions,
        tools=build_toolset(),
    )


async def run_case(
    arm: ArmConfig,
    question: str,
    *,
    model: Any = None,
    gateway: Any = None,
    docs_search: Any = None,
    case_name: str = "",
) -> RunResult:
    """Run one question under one arm and record what the assistant did."""
    with _arm_settings(arm):
        deps = deps_for(arm, gateway=gateway, docs_search=docs_search)
        agent = build_agent(arm, model=model)
        instructions = build_instructions(_FakeCtx(deps))
        result = await agent.run(question, deps=deps)

    tools_called: list[str] = []
    doc_paths: list[str] = []
    for message in result.all_messages():
        for part in getattr(message, "parts", []):
            if not isinstance(part, ToolCallPart):
                continue
            tools_called.append(part.tool_name)
            args = part.args if isinstance(part.args, dict) else {}
            if part.tool_name == "read_doc_page" and args.get("path"):
                doc_paths.append(args["path"])

    return RunResult(
        arm=arm.name,
        case=case_name,
        answer=str(result.output),
        tools_called=tools_called,
        doc_paths=doc_paths,
        instructions=instructions,
    )


class _FakeCtx:
    """``build_instructions`` reads only ``deps``; this captures what the model saw."""

    def __init__(self, deps: AssistantDeps) -> None:
        self.deps = deps


@asynccontextmanager
async def docs_search_backend():
    """A working ``search_docs`` for a judged run, or a loud refusal to fake one.

    Without this the tool raises "not available" on every call, and a judged run
    would score an assistant whose search is permanently broken — penalising the
    arm that *has* documentation in precisely the comparison the feature exists
    to make. A quietly crippled arm is worse than no run at all.

    Loads the corpus as well as connecting, because an empty ``docs_pages`` fails
    the same way but silently: search returns nothing, and the assistant reports
    that the documentation does not cover the question.
    """
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is required for a judged run: without it search_docs cannot "
            "work, and the run would score an assistant that has documentation it "
            "cannot search. Point it at a Postgres with migrations applied."
        )

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from api.services.assistant.knowledge.search import search_pages
    from api.services.assistant.knowledge.sync import sync_corpus

    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            await sync_corpus(db)

        async def search(query: str, limit: int) -> list[dict]:
            async with factory() as db:
                return await search_pages(db, query, limit=limit)

        yield search
    finally:
        await engine.dispose()
