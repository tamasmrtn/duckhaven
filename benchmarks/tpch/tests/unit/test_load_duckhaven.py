from unittest.mock import MagicMock, patch

import httpx

from tpch_bench.load.duckhaven import load_corpus, load_table


def _staged(name: str, put_url: str, get_url: str):
    file = MagicMock(name=name, put_url=put_url, get_url=get_url)
    file.name = name
    file.put_url = put_url
    file.get_url = get_url
    staged = MagicMock()
    staged.files = [file]
    return staged


@patch("tpch_bench.load.duckhaven.httpx.put")
def test_load_table_stages_uploads_and_creates_the_table(mock_put, tmp_path):
    mock_put.return_value = httpx.Response(200, request=httpx.Request("PUT", "https://x"))
    local_path = tmp_path / "region.parquet"
    local_path.write_bytes(b"fake-parquet-bytes")

    conn = MagicMock()
    conn.stage_files.return_value = _staged(
        "region.parquet", "https://stage/put", "https://stage/get"
    )
    cursor = conn.cursor.return_value
    cursor.fetchone.return_value = (5,)

    result = load_table(conn, table="region", local_path=local_path)

    conn.stage_files.assert_called_once_with(["region.parquet"])
    mock_put.assert_called_once()
    assert mock_put.call_args.args[0] == "https://stage/put"
    create_sql = cursor.execute.call_args_list[0].args[0]
    assert "CREATE TABLE region AS SELECT * FROM read_parquet('https://stage/get')" == create_sql
    assert result.table == "region"
    assert result.row_count == 5
    assert result.load_duration_ms >= 0


@patch("tpch_bench.load.duckhaven.httpx.put")
def test_load_table_raises_on_a_failed_upload(mock_put, tmp_path):
    mock_put.return_value = httpx.Response(
        500, request=httpx.Request("PUT", "https://x"), text="server error"
    )
    local_path = tmp_path / "region.parquet"
    local_path.write_bytes(b"fake-parquet-bytes")

    conn = MagicMock()
    conn.stage_files.return_value = _staged(
        "region.parquet", "https://stage/put", "https://stage/get"
    )

    try:
        load_table(conn, table="region", local_path=local_path)
        raise AssertionError("expected an HTTPStatusError")
    except httpx.HTTPStatusError:
        pass

    conn.cursor.return_value.execute.assert_not_called()


@patch("tpch_bench.load.duckhaven.httpx.put")
def test_load_corpus_loads_every_table_in_order(mock_put, tmp_path):
    mock_put.return_value = httpx.Response(200, request=httpx.Request("PUT", "https://x"))
    for table in ("region", "nation"):
        (tmp_path / f"{table}.parquet").write_bytes(b"x")

    conn = MagicMock()
    conn.stage_files.side_effect = lambda names: _staged(
        names[0], f"https://stage/put/{names[0]}", f"https://stage/get/{names[0]}"
    )
    conn.cursor.return_value.fetchone.return_value = (1,)

    results = load_corpus(conn, tmp_path, tables=("region", "nation"))

    assert [r.table for r in results] == ["region", "nation"]
    assert [c.args[0] for c in conn.stage_files.call_args_list] == [
        ["region.parquet"],
        ["nation.parquet"],
    ]
