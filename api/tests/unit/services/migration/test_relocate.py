"""Path-rewrite is the heart of lossless relocation: Iceberg references files by
absolute URI, so metadata.json (text) and Avro manifests (length-prefixed binary)
must have the old catalog-base prefix rewritten to the new one — while data files
are copied untouched."""

from __future__ import annotations

import io

import fastavro

from api.services.migration import relocate

OLD = "s3://oldbucket/warehouse/raw"
# Cross-backend target (scheme change) to prove the substitution is prefix-based.
NEW = "abfss://c@acct.dfs.core.windows.net/wh/raw__m1234abcd"


def test_rewrite_metadata_json_replaces_every_occurrence() -> None:
    meta = (
        f'{{"location":"{OLD}/analytics/t",'
        f'"snapshots":[{{"manifest-list":"{OLD}/analytics/t/metadata/snap-1.avro"}}]}}'
    ).encode()
    out = relocate.rewrite_metadata_json(meta, OLD, NEW).decode()
    assert OLD not in out
    assert out.count(NEW) == 2


def _manifest_bytes(paths: list[str]) -> bytes:
    schema = {
        "type": "record",
        "name": "manifest_entry",
        "fields": [
            {"name": "status", "type": "int"},
            {
                "name": "data_file",
                "type": {
                    "type": "record",
                    "name": "data_file",
                    "fields": [
                        {"name": "file_path", "type": "string"},
                        {"name": "record_count", "type": "long"},
                        {"name": "lower_bounds", "type": ["null", "bytes"], "default": None},
                    ],
                },
            },
        ],
    }
    records = [
        {"status": 1, "data_file": {"file_path": p, "record_count": 5, "lower_bounds": b"\x00\x01"}}
        for p in paths
    ]
    buf = io.BytesIO()
    fastavro.writer(buf, schema, records, codec="deflate", metadata={"iceberg.schema": "{}"})
    return buf.getvalue()


def test_rewrite_avro_rewrites_paths_preserves_data() -> None:
    data = _manifest_bytes(
        [f"{OLD}/analytics/t/data/f1.parquet", f"{OLD}/analytics/t/data/f2.parquet"]
    )
    out = relocate.rewrite_avro(data, OLD, NEW)
    got = list(fastavro.reader(io.BytesIO(out)))
    assert got[0]["data_file"]["file_path"] == f"{NEW}/analytics/t/data/f1.parquet"
    assert got[1]["data_file"]["file_path"] == f"{NEW}/analytics/t/data/f2.parquet"
    # Numbers and bytes (column bounds) must survive the round-trip untouched.
    assert got[0]["data_file"]["record_count"] == 5
    assert got[0]["data_file"]["lower_bounds"] == b"\x00\x01"


def test_rewrite_avro_is_idempotent() -> None:
    data = _manifest_bytes([f"{OLD}/analytics/t/data/f1.parquet"])
    once = relocate.rewrite_avro(data, OLD, NEW)
    twice = relocate.rewrite_avro(once, OLD, NEW)
    assert list(fastavro.reader(io.BytesIO(once))) == list(fastavro.reader(io.BytesIO(twice)))


def test_relocate_table_rewrites_metadata_skips_present_data(monkeypatch) -> None:
    """relocate_table copies/rewrites metadata + Avro every time but skips data
    files already at the destination with the same size (idempotent resume)."""
    src_store = {
        f"{OLD}/t/metadata/v1.metadata.json": (b'{"location":"%s/t"}' % OLD.encode(), None),
        f"{OLD}/t/metadata/snap-1.avro": (_manifest_bytes([f"{OLD}/t/data/f1.parquet"]), None),
        f"{OLD}/t/data/f1.parquet": (b"PARQUETDATA", None),
    }
    dst_store: dict[str, bytes] = {}
    # Pretend the data file was already copied in a prior (crashed) attempt.
    present = {f"{NEW}/t/data/f1.parquet": len(b"PARQUETDATA")}

    from api.services.migration import storage_io

    def fake_list(ctx, location):
        return [(uri, len(blob)) for uri, (blob, _) in src_store.items()]

    def fake_get(ctx, uri):
        return src_store[uri][0]

    def fake_put(ctx, uri, data):
        dst_store[uri] = data

    def fake_size(ctx, uri):
        return present.get(uri)

    monkeypatch.setattr(storage_io, "list_objects", fake_list)
    monkeypatch.setattr(storage_io, "get_object", fake_get)
    monkeypatch.setattr(storage_io, "put_object", fake_put)
    monkeypatch.setattr(storage_io, "object_size", fake_size)

    result = relocate.relocate_table(
        source_location=f"{OLD}/t",
        source_metadata_location=f"{OLD}/t/metadata/v1.metadata.json",
        old_prefix=OLD,
        new_prefix=NEW,
        src_ctx=storage_io.StorageContext("s3", {}, {}),
        dst_ctx=storage_io.StorageContext("adls_gen2", {}, {}),
    )
    assert result.target_metadata_location == f"{NEW}/t/metadata/v1.metadata.json"
    # metadata.json + avro were written (rewritten); the present data file was skipped.
    assert f"{NEW}/t/metadata/v1.metadata.json" in dst_store
    assert f"{NEW}/t/metadata/snap-1.avro" in dst_store
    assert f"{NEW}/t/data/f1.parquet" not in dst_store
    assert OLD.encode() not in dst_store[f"{NEW}/t/metadata/v1.metadata.json"]
