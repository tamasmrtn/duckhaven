"""Whether a series' own colour can be seen and told apart."""

import pytest

from api.services.dashboards.contrast import colour_problems, contrast, delta_e, few


def test_contrast_runs_from_one_to_twenty_one():
    assert contrast("#ffffff", "#000000") == pytest.approx(21.0)
    assert contrast("#3671c6", "#3671c6") == pytest.approx(1.0)


def test_nearly_one_colour_is_close_and_two_hues_are_far():
    assert delta_e("#3671c6", "#3671c7") < 1
    assert delta_e("#3671c6", "#e8002d") > 30


def test_a_colour_too_faint_on_a_theme_is_named_with_its_ratio():
    notes = colour_problems({"GAS": ("#ffd700", "solid"), "VER": ("#3671c6", "solid")})

    assert notes == ["On the light theme, GAS (1.3:1) fall under the 3:1 a line needs to be seen."]


def test_a_colour_too_dark_for_the_dark_theme_is_named():
    notes = colour_problems({"SAU": ("#1e41ff", "solid")})

    assert notes == ["On the dark theme, SAU (2.7:1) fall under the 3:1 a line needs to be seen."]


def test_teammates_in_one_colour_are_told_apart_by_their_line():
    assert colour_problems({"VER": ("#3671c6", "solid"), "PER": ("#3671c6", "dotted")}) == []


def test_two_series_in_one_colour_and_line_read_as_one():
    notes = colour_problems({"VER": ("#3671c6", "solid"), "PER": ("#3672c6", "solid")})

    assert notes == [
        "PER and VER draw in nearly one colour and the same line style, so they read as one line."
    ]


def test_names_past_three_are_counted():
    assert few(["A", "B", "C"]) == "A, B and C"
    assert few(["A", "B", "C", "D", "E"], " pairs") == "A, B, C and 2 more pairs"
    assert few([""]) == "(none)"
