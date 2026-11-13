"""How a metric is shown: its format, unit, currency and good direction.

One rule set, applied wherever a definition enters — the REST API and both
importers — so a combination one path refuses cannot arrive through another.
"""

import pytest

from api.services.semantic.model import presentation_problem


@pytest.mark.parametrize(
    ("fmt", "unit", "currency", "polarity"),
    [
        (None, None, None, None),
        ("number", "orders", None, "up_good"),
        (None, "orders", None, None),
        ("percent", None, None, "down_good"),
        ("currency", None, "EUR", "neutral"),
        ("duration", None, None, None),
        ("bytes", None, None, None),
    ],
)
def test_sound_settings_pass(fmt, unit, currency, polarity):
    assert presentation_problem(fmt, unit, currency, polarity) is None


@pytest.mark.parametrize(
    ("fmt", "unit", "currency", "polarity", "fragment"),
    [
        ("money", None, None, None, "Unknown format"),
        (None, None, None, "higher", "Unknown polarity"),
        ("currency", None, None, None, "needs a currency"),
        ("number", None, "EUR", None, "only to a metric whose format is currency"),
        (None, None, "EUR", None, "only to a metric whose format is currency"),
        ("currency", None, "eur", None, "not an ISO 4217 code"),
        ("currency", None, "EURO", None, "not an ISO 4217 code"),
        ("percent", "points", None, None, "only to a plain number"),
        ("currency", "dollars", "USD", None, "only to a plain number"),
        ("number", "", None, None, "1 to 32 characters"),
        ("number", "x" * 33, None, None, "1 to 32 characters"),
    ],
)
def test_unsound_settings_say_what_is_wrong(fmt, unit, currency, polarity, fragment):
    assert fragment in presentation_problem(fmt, unit, currency, polarity)
