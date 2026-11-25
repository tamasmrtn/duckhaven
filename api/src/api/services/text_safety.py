"""Text that Postgres and UTF-8 can both carry.

Two kinds of character cannot be stored or sent, however a string came to hold
them. NUL is refused by Postgres in any text or JSONB value. A lone surrogate is
not a character at all: it cannot be encoded as UTF-8, so it fails at the
database driver, at the HTTP client, and when an error response echoes it back.

A string that holds one is not rejected here. Each becomes U+FFFD, the
replacement character, so the rest of the text is kept and the loss is visible.
"""

from __future__ import annotations

import re
from typing import Any

_UNCARRIABLE = re.compile("[\x00\ud800-\udfff]")
REPLACEMENT = "�"


def clean(text: str) -> str:
    """``text`` with NUL and lone surrogates replaced by U+FFFD."""
    return _UNCARRIABLE.sub(REPLACEMENT, text)


def clean_json(value: Any) -> Any:
    """``value`` with every string in it, keys included, passed through :func:`clean`.

    For what is about to be stored as JSON: model messages, tool arguments and
    tool results, whose strings come from data the platform does not control.
    """
    if isinstance(value, str):
        return clean(value)
    if isinstance(value, dict):
        return {clean_json(key): clean_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [clean_json(item) for item in value]
    return value
