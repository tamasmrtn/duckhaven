"""Requests carrying text that cannot be stored or written back are answered, not crashed on.

NUL cannot be a Postgres lookup key and a lone surrogate cannot be written as UTF-8. Both
used to surface as a 500 from routes that did nothing wrong, the second from the
handler that builds the 422 itself.
"""

import pytest

JSON = {"content-type": "application/json"}


async def test_a_validation_error_echoing_a_lone_surrogate_is_still_a_422(client):
    """The 422 echoes the input it rejected; rendering that input used to raise."""
    resp = await client.post("/auth/login", content=b'{"email": ["\\ud800"]}', headers=JSON)

    assert resp.status_code == 422
    assert resp.json()["error"] == "validation_error"
    assert "\ufffd" in resp.text  # the input is echoed, with the unwritable character replaced


@pytest.mark.parametrize(
    "path",
    [
        "/workspaces/%00/assistant/status",
        "/workspaces/a%00b/assistant/conversations",
        "/workspaces/tpch-ice%00/catalogs",
        "/%00",
    ],
)
async def test_a_path_holding_nul_is_not_found_not_a_server_error(client, path):
    resp = await client.get(path)

    assert resp.status_code == 404
    assert resp.json() == {"error": "not_found", "message": "Not Found", "details": None}


async def test_the_rest_of_the_api_is_untouched_by_the_path_guard(client):
    assert (await client.get("/auth/methods")).status_code == 200
