"""Text Postgres and UTF-8 can carry: NUL and lone surrogates are replaced, not passed on."""

import json

import pytest

from api.services.text_safety import REPLACEMENT, clean, clean_json


@pytest.mark.parametrize(
    ("text", "kept"),
    [
        ("plain", "plain"),
        ("", ""),
        ("naïve café 🦆 日本語", "naïve café 🦆 日本語"),  # real characters, 1-4 bytes, are kept
        ("a\x00b", f"a{REPLACEMENT}b"),
        ("a\ud800b", f"a{REPLACEMENT}b"),  # a lone high surrogate
        ("a\udc00b", f"a{REPLACEMENT}b"),  # a lone low surrogate
        ("\x00\x00", REPLACEMENT * 2),  # each is replaced, so the length is kept
    ],
)
def test_clean_replaces_only_what_cannot_be_carried(text, kept):
    assert clean(text) == kept


def test_cleaned_text_encodes_as_utf8():
    clean("x\ud800y\x00z").encode("utf-8")


def test_clean_json_reaches_every_string_including_keys():
    value = {"k\x00ey": ["a\x00", {"n": 1, "ok": True, "none": None, "s": "b\ud800"}], "n": 1.5}

    assert clean_json(value) == {
        f"k{REPLACEMENT}ey": [
            f"a{REPLACEMENT}",
            {"n": 1, "ok": True, "none": None, "s": f"b{REPLACEMENT}"},
        ],
        "n": 1.5,
    }


def test_clean_json_leaves_what_has_no_strings_alone():
    assert clean_json(None) is None
    assert clean_json(7) == 7
    assert clean_json([]) == []


def test_what_clean_json_returns_has_no_nul_for_postgres():
    """JSONB refuses the NUL escape, which is how a NUL in a string is written."""
    text = json.dumps(clean_json({"cell": "a\x00b"}))

    assert "\\u0000" not in text
