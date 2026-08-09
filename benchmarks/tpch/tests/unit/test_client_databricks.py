from unittest.mock import MagicMock, patch

import httpx
from databricks import sql as dbsql

from tpch_bench.clients.databricks import DatabricksClient

_TOKEN_RESPONSE = httpx.Response(
    200, json={"access_token": "tok-1"}, request=httpx.Request("POST", "https://x/oidc/v1/token")
)


def _client(**overrides):
    kwargs = {
        "server_hostname": "dbc-1.cloud.databricks.com",
        "http_path": "/sql/1.0/warehouses/abc",
        "client_id": "cid",
        "client_secret": "csecret",
    }
    kwargs.update(overrides)
    return DatabricksClient(**kwargs)


@patch("tpch_bench.clients.databricks.dbsql.connect")
@patch("tpch_bench.clients.databricks.httpx.post")
def test_connect_mints_a_token_and_is_idempotent(mock_post, mock_connect):
    mock_post.return_value = _TOKEN_RESPONSE

    client = _client()
    client.connect()
    client.connect()

    mock_post.assert_called_once()
    mock_connect.assert_called_once_with(
        server_hostname="dbc-1.cloud.databricks.com",
        http_path="/sql/1.0/warehouses/abc",
        access_token="tok-1",
        catalog=None,
        schema=None,
        user_agent_entry="tpch-bench",
    )


@patch("tpch_bench.clients.databricks.dbsql.connect")
@patch("tpch_bench.clients.databricks.httpx.post")
def test_run_statement_fetches_rows_to_get_a_select_row_count(mock_post, mock_connect):
    mock_post.return_value = _TOKEN_RESPONSE
    conn = MagicMock()
    mock_connect.return_value = conn
    cursor = MagicMock()
    cursor.description = [("n", "int", None, None, None, None, None)]
    cursor.fetchall.return_value = [(1,), (2,), (3,)]
    cursor.query_id = "dbx-1"
    conn.cursor.return_value = cursor

    client = _client()
    result = client.run_statement("SELECT 1", timeout_s=30.0)

    assert result.engine_query_id == "dbx-1"
    assert result.row_count == 3
    assert result.error is None
    assert result.compute_ref == "/sql/1.0/warehouses/abc"
    cursor.close.assert_called_once()


@patch("tpch_bench.clients.databricks.dbsql.connect")
@patch("tpch_bench.clients.databricks.httpx.post")
def test_run_statement_uses_rowcount_for_dml_without_fetching(mock_post, mock_connect):
    mock_post.return_value = _TOKEN_RESPONSE
    conn = MagicMock()
    mock_connect.return_value = conn
    cursor = MagicMock()
    cursor.description = None
    cursor.rowcount = 42
    cursor.query_id = "dbx-2"
    conn.cursor.return_value = cursor

    client = _client()
    result = client.run_statement("DELETE FROM t", timeout_s=30.0)

    assert result.row_count == 42
    cursor.fetchall.assert_not_called()


@patch("tpch_bench.clients.databricks.dbsql.connect")
@patch("tpch_bench.clients.databricks.httpx.post")
def test_run_statement_maps_a_failed_execute_to_a_result_error_without_raising(
    mock_post, mock_connect
):
    mock_post.return_value = _TOKEN_RESPONSE
    conn = MagicMock()
    mock_connect.return_value = conn
    cursor = MagicMock()
    cursor.execute.side_effect = dbsql.Error("boom")
    conn.cursor.return_value = cursor

    client = _client()
    result = client.run_statement("SELECT bad", timeout_s=5.0)

    assert result.error is not None
    assert "boom" in result.error


@patch("tpch_bench.clients.databricks.dbsql.connect")
@patch("tpch_bench.clients.databricks.httpx.post")
def test_close_closes_the_connection_and_is_idempotent(mock_post, mock_connect):
    mock_post.return_value = _TOKEN_RESPONSE
    conn = MagicMock()
    mock_connect.return_value = conn

    client = _client()
    client.connect()
    client.close()

    conn.close.assert_called_once()
    assert client._conn is None
    client.close()
