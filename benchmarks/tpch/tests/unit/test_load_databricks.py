from unittest.mock import MagicMock, patch

import httpx

from tpch_bench.load.databricks import DatabricksLoader


def _loader() -> DatabricksLoader:
    return DatabricksLoader(
        server_hostname="dbc-1.cloud.databricks.com",
        client_id="cid",
        client_secret="csecret",
        catalog="test",
        schema="tpch_bench",
    )


@patch("tpch_bench.load.databricks.fetch_oauth_token", return_value="tok")
@patch("tpch_bench.load.databricks.httpx.Client")
def test_upload_puts_the_file_to_the_expected_volume_path(mock_client_cls, mock_token, tmp_path):
    local_path = tmp_path / "region.parquet"
    local_path.write_bytes(b"fake-parquet-bytes")
    mock_client = mock_client_cls.return_value.__enter__.return_value
    mock_client.put.return_value = httpx.Response(204, request=httpx.Request("PUT", "https://x"))

    volume_path = _loader().upload(local_path)

    assert volume_path == "/Volumes/test/tpch_bench/corpus/region.parquet"
    call = mock_client.put.call_args
    assert call.args[0].endswith("/api/2.0/fs/files/Volumes/test/tpch_bench/corpus/region.parquet")
    assert call.kwargs["headers"]["Authorization"] == "Bearer tok"
    assert call.kwargs["params"] == {"overwrite": "true"}


@patch("tpch_bench.load.databricks.fetch_oauth_token", return_value="tok")
@patch("tpch_bench.load.databricks.httpx.Client")
def test_upload_raises_on_a_failed_put(mock_client_cls, mock_token, tmp_path):
    local_path = tmp_path / "region.parquet"
    local_path.write_bytes(b"x")
    mock_client = mock_client_cls.return_value.__enter__.return_value
    mock_client.put.return_value = httpx.Response(
        500, request=httpx.Request("PUT", "https://x"), text="server error"
    )

    try:
        _loader().upload(local_path)
        raise AssertionError("expected an HTTPStatusError")
    except httpx.HTTPStatusError:
        pass


def test_ensure_volume_issues_the_create_volume_statement():
    conn = MagicMock()
    cursor = conn.cursor.return_value

    _loader().ensure_volume(conn)

    cursor.execute.assert_called_once_with("CREATE VOLUME IF NOT EXISTS test.tpch_bench.corpus")


@patch("tpch_bench.load.databricks.fetch_oauth_token", return_value="tok")
@patch("tpch_bench.load.databricks.httpx.Client")
def test_load_table_uploads_then_creates_the_table_from_read_files(
    mock_client_cls, mock_token, tmp_path
):
    local_path = tmp_path / "region.parquet"
    local_path.write_bytes(b"fake-parquet-bytes")
    mock_client = mock_client_cls.return_value.__enter__.return_value
    mock_client.put.return_value = httpx.Response(204, request=httpx.Request("PUT", "https://x"))

    conn = MagicMock()
    cursor = conn.cursor.return_value
    cursor.fetchall.return_value = [(5,)]

    result = _loader().load_table(conn, table="region", local_path=local_path)

    create_sql = cursor.execute.call_args_list[0].args[0]
    assert "CREATE TABLE region AS SELECT * FROM read_files(" in create_sql
    assert "/Volumes/test/tpch_bench/corpus/region.parquet" in create_sql
    assert result.table == "region"
    assert result.row_count == 5
    assert result.load_duration_ms >= 0


@patch("tpch_bench.load.databricks.fetch_oauth_token", return_value="tok")
@patch("tpch_bench.load.databricks.httpx.Client")
def test_load_corpus_ensures_the_volume_once_and_loads_every_table(
    mock_client_cls, mock_token, tmp_path
):
    for table in ("region", "nation"):
        (tmp_path / f"{table}.parquet").write_bytes(b"x")
    mock_client = mock_client_cls.return_value.__enter__.return_value
    mock_client.put.return_value = httpx.Response(204, request=httpx.Request("PUT", "https://x"))

    conn = MagicMock()
    conn.cursor.return_value.fetchall.return_value = [(1,)]

    results = _loader().load_corpus(conn, tmp_path, tables=("region", "nation"))

    assert [r.table for r in results] == ["region", "nation"]
    create_volume_calls = [
        c for c in conn.cursor.return_value.execute.call_args_list if "CREATE VOLUME" in c.args[0]
    ]
    assert len(create_volume_calls) == 1
