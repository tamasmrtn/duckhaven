#!/usr/bin/env python
"""Report drift between a DuckLake catalog and its object storage.

Read-only, so safe against a live deployment.

    DUCKLAKE_DATABASE_URL=... DATABASE_URL=... ./scripts/ducklake-check.py

DuckLake metadata (PostgreSQL) and data (object storage) can be restored to
different moments. Two kinds of drift:

- **missing** -- referenced by the catalog but absent from storage. Queries fail.
- **orphaned** -- in storage but referenced by no live row. Time travel produces
  these legitimately, so this is never a delete list.

Exits 1 when any file is missing, so it can gate a restore.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api" / "src"))

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from api.db.session import async_session_factory  # noqa: E402
from api.models.catalog import KIND_DUCKLAKE, Catalog  # noqa: E402
from api.services.catalog_backends.ducklake import dispose_engine, get_engine  # noqa: E402
from api.services.session_credentials import (  # noqa: E402
    build_storage_block,
    ducklake_data_path,
)


def _list_objects(block: dict, data_path: str) -> set[str]:
    """Every object key under the catalog's prefix, as stored paths."""
    parsed = urlparse(data_path)
    if block.get("type") == "azure":
        from azure.storage.blob import BlobServiceClient

        container = parsed.username or parsed.netloc.split("@")[0]
        prefix = parsed.path.lstrip("/")
        service = BlobServiceClient.from_connection_string(str(block["connection_string"]))
        client = service.get_container_client(container)
        return {b.name for b in client.list_blobs(name_starts_with=prefix)}

    import boto3
    from botocore.config import Config

    bucket, prefix = parsed.netloc, parsed.path.lstrip("/")
    endpoint = str(block.get("endpoint") or "")
    if endpoint and "://" not in endpoint:
        endpoint = f"{'https' if block.get('use_ssl') else 'http'}://{endpoint}"
    client = boto3.client(
        "s3",
        endpoint_url=endpoint or None,
        region_name=str(block.get("region") or "") or None,
        aws_access_key_id=str(block.get("key_id") or "") or None,
        aws_secret_access_key=str(block.get("secret") or "") or None,
        aws_session_token=str(block.get("session_token") or "") or None,
        config=Config(
            s3={"addressing_style": "path" if block.get("url_style") == "path" else "auto"}
        ),
    )
    keys: set[str] = set()
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        keys.update(obj["Key"] for obj in page.get("Contents", []))
    return keys


def _resolve(levels: list[tuple[str, bool]], root: str) -> str:
    """Resolve a data file's location from its nested, possibly-relative parts.

    File paths are relative to the table's, then the schema's, then
    ``data_path``; an absolute level stops the chain.
    """
    out = ""
    for value, is_relative in reversed(levels):
        out = value + out
        if not is_relative:
            return out
    return root + out


async def _catalog_files(schema: str) -> tuple[dict[str, str], int]:
    """Live data files as {resolved location: relative path}, and how many are
    absolute (and so would not move with ``data_path``).
    """
    async with get_engine().connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT s.path, s.path_is_relative, t.path, t.path_is_relative, "
                    "       f.path, f.path_is_relative "
                    f'FROM "{schema}".ducklake_data_file f '
                    f'JOIN "{schema}".ducklake_table t '
                    "  ON t.table_id = f.table_id AND t.end_snapshot IS NULL "
                    f'JOIN "{schema}".ducklake_schema s '
                    "  ON s.schema_id = t.schema_id AND s.end_snapshot IS NULL "
                    "WHERE f.end_snapshot IS NULL"
                )
            )
        ).all()

    resolved: dict[str, str] = {}
    absolute = 0
    for s_path, s_rel, t_path, t_rel, f_path, f_rel in rows:
        if not f_rel:
            absolute += 1
        levels = [(s_path or "", s_rel), (t_path or "", t_rel), (f_path, f_rel)]
        resolved[_resolve(levels, "")] = f_path
    return resolved, absolute


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", help="Check only this catalog slug.")
    args = parser.parse_args()

    failures = 0
    async with async_session_factory() as db:
        # Eager-loaded: a lazy load raises MissingGreenlet under asyncpg.
        stmt = (
            select(Catalog)
            .where(Catalog.kind == KIND_DUCKLAKE)
            .options(selectinload(Catalog.storage_backend))
        )
        if args.catalog:
            stmt = stmt.where(Catalog.slug == args.catalog)
        catalogs = (await db.execute(stmt)).scalars().all()
        if not catalogs:
            print("No DuckLake catalogs found.")
            return 0

        for catalog in catalogs:
            print(f"=== {catalog.slug} ===")
            try:
                referenced, absolute = await _catalog_files(catalog.metadata_schema or "")
            except Exception as exc:  # noqa: BLE001 - report, never abort the sweep
                print(f"  could not read metadata: {exc}")
                continue
            if not referenced:
                print("  no live data files (never written, or never attached)")
                continue

            data_path = ducklake_data_path(catalog)
            block = build_storage_block(catalog.storage_backend, data_path)
            stored = _list_objects(block, data_path)
            prefix = urlparse(data_path).path.lstrip("/")

            # `referenced` keys are already resolved through the schema and table
            # levels, relative to data_path; storage keys are bucket-relative.
            expected = {f"{prefix}{p}" for p in referenced}
            missing = expected - stored
            orphaned = stored - expected

            print(f"  data path:      {data_path}")
            print(f"  live files:     {len(referenced)}")
            if absolute:
                print(f"  absolute paths: {absolute} (do not move with the data path)")
            print(f"  missing:        {len(missing)}")
            print(f"  orphaned:       {len(orphaned)}  (time travel produces these legitimately)")
            for key in sorted(missing)[:10]:
                print(f"    missing: {key}")
            if len(missing) > 10:
                print(f"    ... and {len(missing) - 10} more")
            failures += len(missing)

    await dispose_engine()
    if failures:
        print(f"\n{failures} referenced file(s) are not in storage.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
