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

import asyncio
import contextlib
import json
import os
import uuid
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.usage import UsageLimits

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
        # Every argument below is production's. build_agent previously reproduced
        # three of agent.py's eight, and each omission was silent: no output type
        # made the write-approval path unrepresentable, and no max_tokens meant
        # eval answers were not subject to the cap real users get.
        output_type=[str, DeferredToolRequests],
        deps_type=AssistantDeps,
        instructions=build_instructions,
        tools=build_toolset(),
        model_settings={"max_tokens": settings.assistant_max_output_tokens},
        defer_model_check=True,
    )


def _render_output(output: Any) -> str:
    """The answer as text, including the case where there is deliberately none.

    A write that needs human approval is not a failure and not an answer: the
    turn pauses and the UI asks. Rendering it as a marker lets a judge see that
    the assistant *paused* rather than refused or complied, which are three
    different behaviours that ``str()`` on the raw object would flatten.
    """
    if isinstance(output, DeferredToolRequests):
        pending = ", ".join(call.tool_name for call in output.approvals) or "a write"
        return f"[paused: {pending} awaiting the user's approval before it runs]"
    return str(output)


def _tool_args(part: ToolCallPart) -> dict:
    """A tool call's arguments, whichever form the provider sent them in.

    ``ToolCallPart.args`` is a dict for some providers and a JSON *string* for
    others — every OpenAI-compatible endpoint, which is how DuckHaven reaches
    Ollama and vLLM. Treating the string case as "no arguments" was silent and
    total: across a full 42-case run, ``read_doc_page`` was called 19 times and
    the page it opened was recorded 0 times.

    The judge then never saw the page, its context fell back to "no
    documentation covers this question", and a correct quotation from a real
    page was scored as a fabrication. The harness was penalising the assistant
    for doing the more rigorous thing.

    ``runner.py`` has always parsed the string form; this is the same handling,
    which is where it should have come from in the first place.
    """
    args = part.args
    if isinstance(args, str):
        with contextlib.suppress(json.JSONDecodeError):
            args = json.loads(args)
    return args if isinstance(args, dict) else {}


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
        try:
            result = await agent.run(
                question,
                deps=deps,
                # The same ceiling production enforces. Without it a model stuck
                # in a tool loop runs unbounded on somebody's quota; two cases in
                # a recent run already reached fourteen tool calls.
                usage_limits=UsageLimits(request_limit=settings.assistant_request_limit),
            )
        except UsageLimitExceeded:
            # Recorded as this case's answer rather than raised. One looping case
            # must not discard the other forty-one, and "it never finished" is
            # itself a result worth scoring - production surfaces the same thing
            # to the user.
            return RunResult(
                arm=arm.name,
                case=case_name,
                answer=(
                    "[no answer: the assistant reached its step limit of "
                    f"{settings.assistant_request_limit} model requests for this turn]"
                ),
                tools_called=[],
                doc_paths=[],
                instructions=instructions,
            )

    tools_called: list[str] = []
    doc_paths: list[str] = []
    for message in result.all_messages():
        for part in getattr(message, "parts", []):
            if not isinstance(part, ToolCallPart):
                continue
            tools_called.append(part.tool_name)
            args = _tool_args(part)
            if part.tool_name == "read_doc_page" and args.get("path"):
                doc_paths.append(args["path"])

    return RunResult(
        arm=arm.name,
        case=case_name,
        answer=_render_output(result.output),
        tools_called=tools_called,
        doc_paths=doc_paths,
        instructions=instructions,
    )


class _FakeCtx:
    """``build_instructions`` reads only ``deps``; this captures what the model saw."""

    def __init__(self, deps: AssistantDeps) -> None:
        self.deps = deps


async def retrying(call, *, attempts: int = 4, base_delay: float = 2.0):
    """Retry one model call through a transient provider failure.

    A judged run is 168 sequential calls over ten-odd minutes, and a single
    timeout anywhere in it used to discard the whole run — every completed case
    lost, and the quota spent on them with it. Transient network faults are
    normal at that duration; treating one as fatal is what is not.

    Retried on transport failures, and on structured output that does not
    validate. The second is the expected failure with a smaller open model, and
    resampling it is right — but every retry is printed, so a judge that is
    *systematically* malformed shows up in the log instead of being smoothed
    away into a clean-looking result.

    A rejected request — bad key, unknown model, refused content — is a real
    answer and surfaces immediately. Retrying until a provider happens to agree
    would hide exactly the problems worth seeing.
    """
    from pydantic_ai.exceptions import (
        ModelAPIError,
        ModelHTTPError,
        UnexpectedModelBehavior,
    )

    for attempt in range(attempts):
        try:
            return await call()
        except (ModelAPIError, ModelHTTPError, UnexpectedModelBehavior) as exc:
            transient = isinstance(exc, UnexpectedModelBehavior) or any(
                s in str(exc).lower()
                for s in ("timed out", "timeout", "connection", "502", "503", "504", "429")
            )
            if not transient or attempt == attempts - 1:
                raise
            delay = base_delay * (2**attempt)
            kind = type(exc).__name__
            print(f"    {kind}: {exc}; retrying in {delay:.0f}s", flush=True)
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")


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
