from unittest.mock import MagicMock, patch

from snowflake.connector.errors import Error as SnowflakeError

from tpch_bench.clients.snowflake import SnowflakeClient


def _client(**overrides):
    kwargs = {
        "account": "acct",
        "user": "u",
        "password": "p",
        "warehouse": "wh",
        "database": "db",
    }
    kwargs.update(overrides)
    return SnowflakeClient(**kwargs)


def _cursor_side_effect(exec_cursor, history_cursor):
    def side_effect(*args, **kwargs):
        return history_cursor if args else exec_cursor

    return side_effect


@patch("tpch_bench.clients.snowflake.snowflake.connector.connect")
def test_connect_is_idempotent(mock_connect):
    client = _client()
    client.connect()
    client.connect()

    mock_connect.assert_called_once()
    assert client._conn is mock_connect.return_value


@patch("tpch_bench.clients.snowflake.snowflake.connector.connect")
def test_run_statement_returns_metadata_from_query_history(mock_connect):
    conn = MagicMock()
    mock_connect.return_value = conn

    exec_cursor = MagicMock()
    exec_cursor.sfqid = "sf-1"
    exec_cursor.rowcount = 3

    history_cursor = MagicMock()
    history_cursor.fetchone.return_value = {
        "total_elapsed_time": 100.0,
        "execution_time": 80.0,
        "queued_provisioning_time": 1.0,
        "queued_repair_time": 0.0,
        "queued_overload_time": None,
        "bytes_scanned": 2048,
        "bytes_spilled_to_local_storage": 10,
        "bytes_spilled_to_remote_storage": None,
    }
    conn.cursor.side_effect = _cursor_side_effect(exec_cursor, history_cursor)

    client = _client()
    result = client.run_statement("SELECT 1", timeout_s=30.0)

    exec_cursor.execute.assert_called_once_with("SELECT 1", timeout=30)
    assert result.engine_query_id == "sf-1"
    assert result.server_duration_ms == 100.0
    assert result.execution_ms == 80.0
    assert result.queued_ms == 1.0
    assert result.row_count == 3
    assert result.bytes_scanned == 2048
    assert result.spill_bytes == 10
    assert result.compute_ref == "wh"
    assert result.error is None


@patch("tpch_bench.clients.snowflake.snowflake.connector.connect")
def test_run_statement_maps_a_failed_query_to_a_result_error_without_raising(mock_connect):
    conn = MagicMock()
    mock_connect.return_value = conn
    cursor = MagicMock()
    cursor.execute.side_effect = SnowflakeError("boom")
    conn.cursor.return_value = cursor

    client = _client()
    result = client.run_statement("SELECT bad", timeout_s=5.0)

    assert result.error is not None
    assert "boom" in result.error


@patch("tpch_bench.clients.snowflake.snowflake.connector.connect")
def test_run_statement_tolerates_a_query_history_lookup_failure(mock_connect):
    conn = MagicMock()
    mock_connect.return_value = conn

    exec_cursor = MagicMock()
    exec_cursor.sfqid = "sf-2"
    exec_cursor.rowcount = 0

    history_cursor = MagicMock()
    history_cursor.execute.side_effect = SnowflakeError("history unavailable")
    conn.cursor.side_effect = _cursor_side_effect(exec_cursor, history_cursor)

    client = _client()
    result = client.run_statement("CREATE TABLE t (a INT)", timeout_s=5.0)

    assert result.error is None
    assert result.server_duration_ms is None


@patch("tpch_bench.clients.snowflake.snowflake.connector.connect")
def test_close_closes_the_connection_and_is_idempotent(mock_connect):
    conn = MagicMock()
    mock_connect.return_value = conn

    client = _client()
    client.connect()
    client.close()

    conn.close.assert_called_once()
    assert client._conn is None
    client.close()
