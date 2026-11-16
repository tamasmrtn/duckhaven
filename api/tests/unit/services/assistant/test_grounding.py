"""Flagging the numbers in an answer that nothing in the conversation holds."""

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)

from api.services.assistant.grounding import unsupported_numbers


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
