"""Build the Pydantic AI agent (model-agnostic) and its governed toolset.

Nothing here assumes a specific provider: the model comes from configuration as a
``provider:model`` string, or — for OpenAI-compatible endpoints (Ollama, vLLM,
Azure) — an OpenAI model pointed at a base URL. The agent is built once with the
model check deferred, so it is constructible without provider keys (tests override
the model; a disabled assistant never runs).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.models import Model

from api.config import settings
from api.services.assistant.deps import AssistantDeps
from api.services.assistant.governance import build_governance
from api.services.assistant.prompts import SYSTEM_PROMPT
from api.services.assistant.tools import ALL_TOOLS


def _build_model() -> Model | str:
    """Construct the configured model. Returns a string for provider-inferred
    models (keys from the standard provider env vars) or an explicit model for the
    OpenAI-compatible base-URL path."""
    if settings.assistant_openai_base_url:
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider

        # Strip only a leading "openai:" provider prefix — the remaining name may
        # itself contain a colon (e.g. an Ollama tag like "kimi-k2.7-code:cloud").
        model_name = settings.assistant_model
        if model_name.startswith("openai:"):
            model_name = model_name.split(":", 1)[1]
        return OpenAIChatModel(
            model_name,
            provider=OpenAIProvider(
                base_url=settings.assistant_openai_base_url,
                api_key=settings.assistant_api_key or "not-required",
            ),
        )
    return settings.assistant_model


@lru_cache(maxsize=1)
def get_agent() -> Agent[AssistantDeps, str]:
    """Return the process-wide assistant agent (built lazily, cached)."""
    return Agent(
        _build_model(),
        # str for a normal answer; DeferredToolRequests when a write awaits approval.
        output_type=[str, DeferredToolRequests],
        deps_type=AssistantDeps,
        instructions=SYSTEM_PROMPT,
        tools=ALL_TOOLS,
        capabilities=[build_governance()],
        model_settings={"max_tokens": settings.assistant_max_output_tokens},
        defer_model_check=True,
        name="duckhaven-assistant",
    )
