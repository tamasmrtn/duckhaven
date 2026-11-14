"""Numbers in an answer that nothing the conversation returned or said holds.

A model that has read a result can still write a number it did not read: a total
it added up, a percentage it worked out wrongly, a figure it half remembers. This
finds the numbers in an answer that appear in no tool result and in nothing the
person wrote, allowing for how numbers are written: rounding, thousands
separators, percentages, and K, M and B for thousands, millions and billions.

It flags and never edits. A number it flags may be right, worked out from ones a
tool returned; the flag says only that no tool returned it, so a reader knows
which figures to check.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation

from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart, UserPromptPart

#: Small whole numbers are counts and list items more often than findings.
_TRIVIAL = Decimal(10)
_SCALES = {
    "k": Decimal(1_000),
    "thousand": Decimal(1_000),
    "m": Decimal(1_000_000),
    "million": Decimal(1_000_000),
    "b": Decimal(1_000_000_000),
    "bn": Decimal(1_000_000_000),
    "billion": Decimal(1_000_000_000),
}
_CODE = re.compile(r"```.*?```|`[^`\n]*`", re.S)
# A number standing on its own: not inside a word or an identifier such as Q3 or
# v2, nor part of a date or a time, with its scale or percent sign if it has one.
_WRITTEN = re.compile(
    r"(?<![\w.:/-])(\d[\d,]*(?:\.\d+)?)(?!\d)(?![.:/-]\d)"
    r"(\s?(?:%|thousand\b|million\b|billion\b|bn\b|[kKmMbB]\b))?"
)
# Any number inside text a tool returned or the person wrote, dates and windows
# included: "15 Oct – 13 Nov 2026" holds 15, 13 and 2026.
_ANY = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
_MAX_FLAGGED = 10


def unsupported_numbers(answer: str, messages: Iterable[ModelMessage], *extra: str) -> list[str]:
    """The numbers in ``answer``, as written, that no tool result or prompt holds.

    ``messages`` is the conversation the answer ends; ``extra`` is anything else
    the model was shown, such as the page's parameter values.
    """
    known = _known(messages, extra)
    flagged: list[str] = []
    for match in _WRITTEN.finditer(_CODE.sub(" ", answer)):
        digits, suffix = match.group(1).rstrip(","), (match.group(2) or "").strip().lower()
        try:
            value = Decimal(digits.replace(",", ""))
        except InvalidOperation:
            continue
        if not suffix and value <= _TRIVIAL:
            continue
        written = match.group(0).strip()
        if written in flagged or _held(value, digits, suffix, known):
            continue
        flagged.append(written)
        if len(flagged) == _MAX_FLAGGED:
            break
    return flagged


def _held(value: Decimal, digits: str, suffix: str, known: list[Decimal]) -> bool:
    """Whether a known number, rounded as the answer wrote it, gives ``value``."""
    decimals = len(digits.partition(".")[2])
    step = Decimal(1).scaleb(-decimals)
    readings = []
    if suffix == "%":
        # 12.5% is a share of 12.5 or of 0.125, as a tool may hold either.
        readings += [(value, step), (value / 100, step / 100)]
    elif suffix:
        scale = _SCALES[suffix]
        readings.append((value * scale, step * scale))
    else:
        readings.append((value, step))
    # Rounding to the step shown, with slack for a model that rounded half the
    # other way. A drop written as a positive number is still the same number.
    return any(abs(abs(n) - r) <= s for r, s in readings for n in known)


def _known(messages: Iterable[ModelMessage], extra: Iterable[str]) -> list[Decimal]:
    texts: list[str] = list(extra)
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if isinstance(part, ToolReturnPart):
                texts.append(json.dumps(part.content, default=str))
            elif isinstance(part, UserPromptPart) and isinstance(part.content, str):
                texts.append(part.content)
    numbers: set[Decimal] = set()
    for text in texts:
        for found in _ANY.findall(text):
            try:
                numbers.add(Decimal(found))
            except InvalidOperation:
                continue
    return list(numbers)
