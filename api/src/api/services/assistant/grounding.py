"""Numbers in an answer that nothing the conversation returned or said holds.

A model that has read a result can still write a number it did not read: a total
it added up, a percentage it worked out wrongly, a figure it half remembers. This
finds the numbers in an answer that appear in no tool result and in nothing the
person wrote, allowing for how numbers are written: rounding, thousands
separators, percentages, and K, M and B for thousands, millions and billions.

A difference of two numbers the answer gives, such as one row's lead over
another, is not flagged: the reader can check it against the two beside it. Nor
are the days of a month or a window's length, which say when, not how much.

It flags and never edits. A number it flags may be right, worked out from ones a
tool returned; the flag says only that no tool returned it, so a reader knows
which figures to check.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
from itertools import combinations

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
# included: "15 Oct – 13 Nov 2026" holds 15, 13 and 2026. Exponent notation is read
# only where it stands as a token of its own, with the few digits a double needs:
# inside "4c8f7a6b5d4e3689204" it is a hash fragment, not 4 × 10^3689204. Digits
# inside a word or identifier are still read as plain numbers.
_ANY = re.compile(
    r"(?<![\w.])-?\d+(?:\.\d+)?[eE][-+]?\d{1,3}(?!\w)"
    r"|-?\d+(?:\.\d+)?"
)
#: A figure further from 1 than this, in powers of ten, is text that parsed as a
#: number. Decimal raises Overflow on arithmetic past its exponent limit (999,999),
#: and no result a tool returns, DOUBLE included, comes near this.
_MAX_MAGNITUDE = 400
_MONTH = (
    r"(?:january|february|march|april|may|june|july|august|september|october|november"
    r"|december|jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec)\b\.?"
)
_DAY = r"\d{1,2}(?:st|nd|rd|th)?(?:\s?[-–]\s?\d{1,2}(?:st|nd|rd|th)?)?"
_PERIOD = r"(?:hour|day|week|month|quarter|year)"
#: A date written with its month's name, with its days and year if it has them:
#: "1–31 October 2026", "Oct 3".
DATES = rf"(?:\b{_DAY}\s+)?\b{_MONTH}(?:\s+{_DAY}\b)?(?:,?\s+\d{{4}}\b)?"
# Numbers that say when rather than how much: dates, ordinals such as "the 31st",
# and a window's length, "the last 90 days" or "a 28-day window".
_WHEN = re.compile(
    rf"{DATES}|\b\d{{1,2}}(?:st|nd|rd|th)\b"
    rf"|\b(?:last|past|previous|prior|next|trailing|rolling)\s+\d+\s+{_PERIOD}s?\b"
    rf"|\b\d+-{_PERIOD}\b",
    re.IGNORECASE,
)
_MAX_FLAGGED = 10


def unsupported_numbers(answer: str, messages: Iterable[ModelMessage], *extra: str) -> list[str]:
    """The numbers in ``answer``, as written, that no tool result or prompt holds.

    ``messages`` is the conversation the answer ends; ``extra`` is anything else
    the model was shown, such as the page's parameter values.
    """
    known = _known(messages, extra)
    unheld: list[tuple[str, list[tuple[Decimal, Decimal]]]] = []
    given: list[Decimal] = []
    for match in _WRITTEN.finditer(_WHEN.sub(" ", _CODE.sub(" ", answer))):
        digits, suffix = match.group(1).rstrip(","), (match.group(2) or "").strip().lower()
        try:
            value = Decimal(digits.replace(",", ""))
        except InvalidOperation:
            continue
        if not suffix and value <= _TRIVIAL:
            continue
        readings = _readings(value, digits, suffix)
        if not _held(readings, known):
            unheld.append((match.group(0).strip(), readings))
        elif suffix != "%":
            given.append(readings[0][0])
    differences = [abs(a - b) for a, b in combinations(given, 2)]
    flagged: list[str] = []
    for written, readings in unheld:
        if written in flagged or _held(readings, differences):
            continue
        flagged.append(written)
        if len(flagged) == _MAX_FLAGGED:
            break
    return flagged


def _held(readings: list[tuple[Decimal, Decimal]], known: list[Decimal]) -> bool:
    """Whether a known number, rounded as the answer wrote it, gives a reading."""
    # Rounding to the step shown, with slack for a model that rounded half the
    # other way. A drop written as a positive number is still the same number.
    return any(abs(abs(n) - r) <= s for r, s in readings for n in known)


def _readings(value: Decimal, digits: str, suffix: str) -> list[tuple[Decimal, Decimal]]:
    """What a number written as ``digits`` and ``suffix`` may stand for, each with
    the step it was rounded to."""
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
    return readings


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
                number = Decimal(found)
            except InvalidOperation:
                continue
            if abs(number.adjusted()) <= _MAX_MAGNITUDE:
                numbers.add(number)
    return list(numbers)
