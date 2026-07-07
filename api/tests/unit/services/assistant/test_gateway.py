import httpx
import pytest
import respx

from api.services.assistant.gateway import Gateway, GatewayError, _translate


def _gateway(client=None, **kw) -> Gateway:
    kw.setdefault("row_cap", 100)
    kw.setdefault("byte_cap", 200)
    kw.setdefault("service_account_id", "sa-1")
    return Gateway(client=client, workspace_slug="ws", **kw)


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
    gw = _gateway(byte_cap=200)
    rows = [{"v": "x" * 100} for _ in range(10)]
    kept, truncated = gw._cap_bytes(rows)
    assert truncated is True
    assert 0 < len(kept) < len(rows)


def test_cap_bytes_keeps_small_samples():
    gw = _gateway(byte_cap=10_000)
    rows = [{"v": i} for i in range(5)]
    kept, truncated = gw._cap_bytes(rows)
    assert truncated is False
    assert kept == rows


@respx.mock
async def test_get_query_result_refuses_other_principals_query():
    # A query owned by a different user must not be readable via the assistant.
    respx.get("http://assistant.internal/queries/q-foreign").mock(
        return_value=httpx.Response(200, json={"id": "q-foreign", "user_id": "someone-else"})
    )
    async with httpx.AsyncClient(base_url="http://assistant.internal") as client:
        gw = _gateway(client=client, service_account_id="sa-1")
        with pytest.raises(GatewayError, match="only page results of queries I ran"):
            await gw.get_query_result("q-foreign", cursor=None, limit=100)


@respx.mock
async def test_get_query_result_allows_own_query():
    respx.get("http://assistant.internal/queries/q-mine").mock(
        return_value=httpx.Response(200, json={"id": "q-mine", "user_id": "sa-1"})
    )
    respx.get("http://assistant.internal/queries/q-mine/rows").mock(
        return_value=httpx.Response(
            200, json={"rows": [{"n": 1}], "columns": ["n"], "cursor": None, "total": 1}
        )
    )
    async with httpx.AsyncClient(base_url="http://assistant.internal") as client:
        gw = _gateway(client=client, service_account_id="sa-1")
        page = await gw.get_query_result("q-mine", cursor=None, limit=100)
        assert page["rows"] == [{"n": 1}]


@pytest.mark.parametrize("code,prefix", [(409, "Conflict"), (503, "Service unavailable")])
def test_translate_other_codes(code, prefix):
    err = _translate(_status_error(code, {"detail": "x"}))
    assert prefix in str(err)
