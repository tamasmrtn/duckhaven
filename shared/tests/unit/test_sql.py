"""quote_ident always yields exactly one quoted identifier."""

import pytest

from duckhaven_shared.sql import quote_ident


@pytest.mark.parametrize(
    ("name", "quoted"),
    [
        ("orders", '"orders"'),
        ("Order Lines", '"Order Lines"'),
        ('say "hi"', '"say ""hi"""'),
        ('"', '""""'),
        ('a"."b', '"a"".""b"'),
        ("", '""'),
    ],
)
def test_quote_ident_doubles_embedded_quotes(name: str, quoted: str) -> None:
    assert quote_ident(name) == quoted


@pytest.mark.parametrize("name", ['say "hi"', 'a"."b', '""', 'q"'])
def test_quoted_body_holds_no_lone_quote(name: str) -> None:
    body = quote_ident(name)[1:-1]
    assert body.replace('""', "").count('"') == 0
    assert body.replace('""', '"') == name
