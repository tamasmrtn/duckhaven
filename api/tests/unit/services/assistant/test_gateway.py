import httpx
import pytest

from api.services.assistant.gateway import Gateway, GatewayError, _translate


def _status_error(code: int, body) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "http://assistant.internal/x")
    if isinstance(body, dict):
        response = httpx.Response(code, json=body, request=request)
    else:
        response = httpx.Response(code, text=body, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


def test_translate_grant_denied_403_names_object():
    err = _translate(
        _status_error(403, {"error": "grant_denied", "detail": "Not authorized (reader) on c.s.t"})
    )
    assert isinstance(err, GatewayError)
    assert "Access denied" in str(err)
    assert "Not authorized (reader) on c.s.t" in str(err)


def test_translate_sql_not_allowed_422():
    err = _translate(_status_error(422, {"error": "sql_not_allowed", "detail": "ATTACH is denied"}))
    assert "Not allowed" in str(err)
    assert "ATTACH is denied" in str(err)


def test_translate_404_is_not_found():
    err = _translate(_status_error(404, {"detail": "gone"}))
    assert "Not found" in str(err)


def test_translate_plain_text_body():
    err = _translate(_status_error(500, "internal boom"))
    assert "internal boom" in str(err)


def test_cap_bytes_truncates_large_samples():
    gw = Gateway(client=None, workspace_slug="ws", row_cap=100, byte_cap=200)
    rows = [{"v": "x" * 100} for _ in range(10)]
    kept, truncated = gw._cap_bytes(rows)
    assert truncated is True
    assert 0 < len(kept) < len(rows)


def test_cap_bytes_keeps_small_samples():
    gw = Gateway(client=None, workspace_slug="ws", row_cap=100, byte_cap=10_000)
    rows = [{"v": i} for i in range(5)]
    kept, truncated = gw._cap_bytes(rows)
    assert truncated is False
    assert kept == rows


@pytest.mark.parametrize("code,prefix", [(409, "Conflict"), (503, "Service unavailable")])
def test_translate_other_codes(code, prefix):
    err = _translate(_status_error(code, {"detail": "x"}))
    assert prefix in str(err)
