"""Copy one Iceberg table to a new location, rewriting absolute paths.

Iceberg metadata is a tree of files that reference each other (and the data
files) by **absolute** URI: ``vN.metadata.json`` → snapshot ``manifest-list``
(an Avro file) → ``manifest_path`` entries (Avro) → ``data_file.file_path``
entries (Avro) → Parquet data. A plain object copy to a new bucket leaves all of
those URIs pointing at the old location, so the copied tree is unreadable.

Relocation therefore copies every file and, for the metadata/Avro files, rewrites
every occurrence of the old catalog-base prefix to the new one before writing.
Data files (Parquet, stats, …) are copied byte-for-byte. The old and new
catalog-base locations are full URIs, so the substitution is unambiguous and also
handles cross-backend scheme changes (``s3://`` ↔ ``abfss://``).

These functions are synchronous (the cloud SDKs are); the engine runs them off
the event loop via ``asyncio.to_thread``.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from api.services.migration import storage_io
from api.services.migration.storage_io import StorageContext


@dataclass
class RelocateResult:
    target_metadata_location: str
    bytes_copied: int


def _is_metadata_json(name: str) -> bool:
    return name.endswith(".metadata.json")


def _is_avro(name: str) -> bool:
    return name.endswith(".avro")


def _rewrite_value(value: object, old: str, new: str) -> object:
    """Recursively replace the old prefix in every string within an Avro record.
    Bytes (column bounds, etc.) and numbers are left untouched — paths are only
    ever stored as strings."""
    if isinstance(value, str):
        return value.replace(old, new)
    if isinstance(value, dict):
        return {k: _rewrite_value(v, old, new) for k, v in value.items()}
    if isinstance(value, list):
        return [_rewrite_value(v, old, new) for v in value]
    return value


def rewrite_metadata_json(data: bytes, old: str, new: str) -> bytes:
    """Rewrite a ``*.metadata.json``: it is UTF-8 text, so a prefix substring
    replace is exact (the old catalog base only appears as path values)."""
    return data.decode("utf-8").replace(old, new).encode("utf-8")


def rewrite_avro(data: bytes, old: str, new: str) -> bytes:
    """Rewrite an Avro manifest-list / manifest file: decode with the embedded
    writer schema, replace the old prefix in every string field, and re-encode
    preserving the schema, codec, and custom header metadata.

    A naive byte replace would corrupt Avro's length-prefixed string framing when
    the prefixes differ in length, so a full decode/re-encode is required."""
    import fastavro

    reader = fastavro.reader(io.BytesIO(data))
    writer_schema = reader.writer_schema
    codec = getattr(reader, "codec", "null") or "null"
    # Iceberg stores its table schema / partition spec in the Avro file header;
    # carry those through (rewriting any strings), but let fastavro re-add the
    # avro.* keys (schema/codec) itself.
    header_meta = {
        k: (v.replace(old, new) if isinstance(v, str) else v)
        for k, v in (reader.metadata or {}).items()
        if not k.startswith("avro.")
    }
    records = [_rewrite_value(record, old, new) for record in reader]

    out = io.BytesIO()
    fastavro.writer(out, writer_schema, records, codec=codec, metadata=header_meta or None)
    return out.getvalue()


def relocate_table(
    *,
    source_location: str,
    source_metadata_location: str,
    old_prefix: str,
    new_prefix: str,
    src_ctx: StorageContext,
    dst_ctx: StorageContext,
) -> RelocateResult:
    """Copy every file under a table's location to the rewritten location.

    Metadata/Avro files are always re-copied (rewritten); immutable data files are
    skipped when already present at the destination with the same size, making the
    copy idempotent and safe to resume after a crash. Returns the rewritten root
    metadata location (for ``registerTable``) and the bytes transferred.
    """
    total = 0
    for uri, size in storage_io.list_objects(src_ctx, source_location):
        dst_uri = uri.replace(old_prefix, new_prefix)
        name = uri.rsplit("/", 1)[-1]
        if _is_avro(name):
            data = rewrite_avro(storage_io.get_object(src_ctx, uri), old_prefix, new_prefix)
        elif _is_metadata_json(name):
            data = rewrite_metadata_json(
                storage_io.get_object(src_ctx, uri), old_prefix, new_prefix
            )
        else:
            existing = storage_io.object_size(dst_ctx, dst_uri)
            if existing is not None and existing == size:
                total += size  # immutable data file already copied
                continue
            data = storage_io.get_object(src_ctx, uri)
        storage_io.put_object(dst_ctx, dst_uri, data)
        total += len(data)

    return RelocateResult(
        target_metadata_location=source_metadata_location.replace(old_prefix, new_prefix),
        bytes_copied=total,
    )
