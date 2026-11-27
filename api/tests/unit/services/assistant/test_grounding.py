"""Flagging the numbers in an answer that nothing in the conversation holds."""

from decimal import Decimal

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)

from api.services.assistant.grounding import _known, unsupported_numbers


def _turn(*contents: object, prompt: str = "How did revenue do?") -> list:
    parts = [UserPromptPart(content=prompt)]
    parts += [
        ToolReturnPart(tool_name="run_widget", content=c, tool_call_id=f"t{i}")
        for i, c in enumerate(contents)
    ]
    return [ModelRequest(parts=parts)]


WIDGET = {
    "rows": [["510234.5"]],
    "compare": {"current": "510234.5", "previous": 560000, "delta": -49765.5, "delta_pct": -0.0889},
    "window": "Last 30 days: 15 Oct – 13 Nov 2026",
}


@pytest.mark.parametrize(
    "written",
    [
        "€510K",  # thousands, rounded
        "€510,234.50",  # in full, with separators
        "0.51 million",
        "8.9%",  # a share held as a fraction
        "€49.8K",  # a fall, written without its sign
        "560,000",
        "15 Oct – 13 Nov 2026",  # the window's dates
        "2026-11-01",  # a date is not a figure
        "30 days",  # said by the person
    ],
)
def test_a_number_a_tool_returned_is_not_flagged_however_it_is_written(written):
    assert (
        unsupported_numbers(f"Revenue: {written}.", _turn(WIDGET, prompt="the last 30 days")) == []
    )


def test_a_number_no_tool_returned_is_flagged_as_written():
    answer = "Revenue was €510K, up 40% on a base of 12,345 orders."
    assert unsupported_numbers(answer, _turn(WIDGET)) == ["40%", "12,345"]


def test_small_counts_code_and_identifiers_are_left_alone():
    answer = "The top 3 rows of Q4 are in `SELECT 99999 FROM t`:\n\n```sql\nSELECT 77777\n```"
    assert unsupported_numbers(answer, _turn(WIDGET)) == []


def test_numbers_from_an_earlier_turn_and_the_page_count():
    earlier = _turn({"total": 4242})
    later = [*earlier, ModelResponse(parts=[TextPart("ok")]), *_turn(prompt="and now?")]
    assert unsupported_numbers("Still 4,242, as filtered to 777.", later, '{"store": 777}') == []


def test_each_number_is_flagged_once():
    assert unsupported_numbers("12,345 and again 12,345.", _turn()) == ["12,345"]


CHANNELS = {
    "columns": ["channel", "revenue"],
    "rows": [["Partner", "178504.19"], ["Web", "174764.62"], ["Retail", "169818.02"]],
}


def test_the_difference_of_two_numbers_the_answer_gives_is_not_flagged():
    """A reader sees both numbers beside it, so a gap worked out from them can be
    checked there: the lead of one row over another, or the spread of them all."""
    answer = (
        "Partner €178,504.19, Web €174,764.62, Retail €169,818.02. Partner leads by roughly "
        "€3.7k over Web, and the spread across all three is only about €8.7k."
    )
    assert unsupported_numbers(answer, _turn(CHANNELS)) == []


def test_a_difference_of_numbers_the_answer_does_not_give_is_flagged():
    """Only numbers in the answer count: a tool's many numbers have a difference
    near almost anything."""
    answer = "Partner leads Web by roughly €3.7k, and Retail trails by €4.9k."
    assert unsupported_numbers(answer, _turn(CHANNELS)) == ["3.7k", "4.9k"]


@pytest.mark.parametrize(
    "written",
    [
        "1–31 October 2026",
        "October 1st to the 31st",
        "Oct 31",
        "a trailing 30 days",
        "the last 90 days",
        "a 28-day window",
    ],
)
def test_days_of_a_month_and_window_lengths_are_not_findings(written):
    assert unsupported_numbers(f"Over {written}, Partner led.", _turn(CHANNELS, prompt="")) == []


def test_a_duration_that_is_a_finding_is_still_flagged():
    answer = "Orders took 45 days to ship on average."
    assert unsupported_numbers(answer, _turn(CHANNELS, prompt="")) == ["45"]


# Text that reads as 4 × 10^3689204123456789 to a number parser but is a piece of a
# key or an identifier: an md5 surrogate key, a dlt/dbt hash, a UUID with a prefix,
# and the ids a person types into a question.
LOOKS_LIKE_A_HUGE_NUMBER = [
    "4c8f7a6b5d4e3689204123456789abcde",
    "3c59dc048e8850243be8079a5c74d079",  # md5("21")
    "healthkit:0F8A9C3B-1234-4D2E-9A11-513E7211603E",
    "ORDER-7E1234567",
    "AB12e3456789",
]
UNHELD = "Revenue was €12,345 across 4,071 orders."


@pytest.mark.parametrize("key", LOOKS_LIKE_A_HUGE_NUMBER)
def test_a_key_in_a_tool_result_is_not_a_number_and_cannot_fail_the_check(key):
    """The bug this guards: reading the key's exponent made the check raise
    decimal.Overflow, which ended the turn after the answer was written."""
    assert unsupported_numbers(UNHELD, _turn({"key": key, "rows": 3})) == ["12,345", "4,071"]


@pytest.mark.parametrize("key", LOOKS_LIKE_A_HUGE_NUMBER)
def test_a_key_the_person_typed_is_not_a_number_either(key):
    assert unsupported_numbers(UNHELD, _turn(WIDGET, prompt=f"why is {key} odd?")) == [
        "12,345",
        "4,071",
    ]


@pytest.mark.parametrize("key", LOOKS_LIKE_A_HUGE_NUMBER)
def test_a_key_on_the_page_is_not_a_number_either(key):
    answer = unsupported_numbers(UNHELD, _turn(WIDGET), f"SELECT * FROM t WHERE k = '{key}'")

    assert answer == ["12,345", "4,071"]


@pytest.mark.parametrize("key", LOOKS_LIKE_A_HUGE_NUMBER)
def test_a_figure_a_tool_returned_is_still_held_beside_such_a_key(key):
    assert unsupported_numbers("There were 4,071 orders.", _turn({"key": key, "n": 4071})) == []


def test_exponent_notation_standing_alone_still_counts_as_the_number_it_writes():
    assert unsupported_numbers("About 1.5M units.", _turn({"units": "1.5e6"})) == []


def test_digits_inside_an_identifier_still_ground_a_figure():
    assert unsupported_numbers("Order 4,071 shipped.", _turn({"id": "ORD4071"})) == []


def test_a_run_of_digits_too_long_to_be_a_figure_is_not_one():
    """No result a tool returns reaches this size, and Decimal fails on arithmetic past
    its exponent limit, so it is left out rather than compared."""
    known = _known(_turn({"id": "9" * 500, "n": 4071}), ())

    assert Decimal(4071) in known
    assert Decimal("9" * 500) not in known
