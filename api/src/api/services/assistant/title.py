"""AI-generated conversation titles.

After the first turn of a conversation, a short title is generated from the user's
opening message using the configured model (a separate, tool-less agent), so the
conversation list shows something meaningful instead of "New conversation".
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_ai import Agent

from api.services.assistant.agent import _build_model, _instrumentation

DEFAULT_TITLE = "New conversation"

_TITLE_INSTRUCTIONS = (
    "You write a very short title for a data-analysis chat. Given the user's first "
    "message, reply with a concise, specific title of 3 to 6 words. No quotes, no "
    "trailing punctuation, no preamble — reply with only the title."
)


@lru_cache(maxsize=1)
def get_title_agent() -> Agent[None, str]:
    """Process-wide tool-less agent used only to name conversations."""
    return Agent(
        _build_model(),
        instructions=_TITLE_INSTRUCTIONS,
        capabilities=[_instrumentation()],
        model_settings={"max_tokens": 24},
        defer_model_check=True,
        name="duckhaven-assistant-title",
    )


def _clean(text: str) -> str:
    if not text or not text.strip():
        return ""
    first_line = text.strip().splitlines()[0].strip()
    return first_line.strip("\"'").strip()[:60]


async def generate_title(prompt: str) -> str:
    """Return a short title for a conversation opened with ``prompt`` (or "")."""
    result = await get_title_agent().run(prompt)
    return _clean(result.output if isinstance(result.output, str) else "")
