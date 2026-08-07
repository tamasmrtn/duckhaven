"""Cross-engine TPC-H corpus manager (plan §5).

Generates each scale factor's Parquet corpus once (via `tpchgen_runner`),
publishes it to a standalone Azure Storage Account that lives *outside*
the `deploy/terraform` module — so it survives every apply/destroy cycle
of the main environment, which deletes its own warehouse storage account
on destroy (plan §2 gotcha #6) — and replicates a checksummed copy to S3
for Databricks, whose confirmed-AWS trial workspace (plan §5) can't read
an ADLS location without cross-cloud federation nobody has set up.

A `Manifest` (one per scale factor, written as `manifest.json` alongside
the blobs) records every file's sha256 and byte size at generation time.
`download_verified` and `replicate_to_s3` both check bytes against it, so
silent truncation or corruption in either destination is caught rather
than quietly loaded — the whole point of generating a scale factor once
and reusing it is plan §4's fairness requirement that DuckHaven and
Databricks load from byte-identical source data.

Talks to Azure Blob Storage and S3 through the caller's own
`ContainerClient`/`boto3` S3 client (official SDKs, already project
dependencies) rather than hand-rolling either transport — this module's
own job is the generate-once/checksum/replicate orchestration around them.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tpch_bench.datagen.tpchgen_runner import TABLES, generate

if TYPE_CHECKING:
    from azure.storage.blob import ContainerClient

    # boto3's S3 client is dynamically generated (no importable stub package
    # in this project's dependencies); annotated as Any rather than adding
    # boto3-stubs solely for a type hint.
    S3Client = Any

_MANIFEST_BLOB_NAME = "manifest.json"
_HASH_CHUNK_BYTES = 1024 * 1024


class CorpusVerificationError(RuntimeError):
    """A downloaded/replicated file's bytes don't match its manifest entry."""


@dataclass(frozen=True)
class FileManifestEntry:
    table: str
    # Path relative to the scale factor's corpus root, e.g. "region.parquet"
    # or "lineitem/lineitem.1.parquet" for a partitioned table.
    relative_path: str
    sha256: str
    byte_size: int


@dataclass(frozen=True)
class Manifest:
    scale_factor: float
    files: tuple[FileManifestEntry, ...]

    def to_json(self) -> str:
        return json.dumps(
            {
                "scale_factor": self.scale_factor,
                "files": [asdict(f) for f in self.files],
            },
            indent=2,
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, text: str) -> Manifest:
        data = json.loads(text)
        return cls(
            scale_factor=data["scale_factor"],
            files=tuple(FileManifestEntry(**f) for f in data["files"]),
        )


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as f:
        while chunk := f.read(_HASH_CHUNK_BYTES):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def build_manifest(scale_factor: float, corpus_dir: Path) -> Manifest:
    """Hash every generated Parquet file under `corpus_dir` (as `generate()`
    laid it out: `<table>.parquet` or `<table>/<table>.<part>.parquet`)."""
    entries = []
    for table in TABLES:
        flat = corpus_dir / f"{table}.parquet"
        part_dir = corpus_dir / table
        if flat.exists():
            files = [flat]
        elif part_dir.is_dir():
            files = sorted(part_dir.glob(f"{table}.*.parquet"))
        else:
            continue
        for file in files:
            sha256, size = _sha256_file(file)
            entries.append(
                FileManifestEntry(
                    table=table,
                    relative_path=str(file.relative_to(corpus_dir)),
                    sha256=sha256,
                    byte_size=size,
                )
            )
    return Manifest(scale_factor=scale_factor, files=tuple(entries))


def generate_corpus(
    *,
    scale_factor: float,
    corpus_dir: Path,
    tables: tuple[str, ...] = TABLES,
    parts: int | None = None,
) -> Manifest:
    """Generate `scale_factor`'s corpus locally and hash it. Does not publish
    anywhere — see `publish_to_azure` for that half, kept separate so a
    caller can inspect/dry-run the manifest before any upload."""
    generate(scale_factor=scale_factor, output_dir=corpus_dir, tables=tables, parts=parts)
    return build_manifest(scale_factor, corpus_dir)


def _blob_prefix(scale_factor: float) -> str:
    return f"sf{scale_factor:g}"


def publish_to_azure(
    container_client: ContainerClient, corpus_dir: Path, manifest: Manifest
) -> None:
    """Upload every file in `manifest` plus the manifest itself, under a
    `sf<N>/` prefix. Overwrites existing blobs — republishing after a local
    regeneration is expected to replace, not accumulate stale copies."""
    prefix = _blob_prefix(manifest.scale_factor)
    for entry in manifest.files:
        local_path = corpus_dir / entry.relative_path
        with local_path.open("rb") as f:
            container_client.upload_blob(f"{prefix}/{entry.relative_path}", data=f, overwrite=True)
    container_client.upload_blob(
        f"{prefix}/{_MANIFEST_BLOB_NAME}", data=manifest.to_json(), overwrite=True
    )


def fetch_manifest_from_azure(container_client: ContainerClient, scale_factor: float) -> Manifest:
    prefix = _blob_prefix(scale_factor)
    downloader = container_client.download_blob(f"{prefix}/{_MANIFEST_BLOB_NAME}")
    return Manifest.from_json(downloader.readall().decode("utf-8"))


def download_verified(
    container_client: ContainerClient,
    manifest: Manifest,
    entry: FileManifestEntry,
    dest_path: Path,
) -> None:
    """Download one manifest entry from Azure and verify it against its
    recorded sha256/size before leaving it in place, so a truncated or
    corrupted transfer is caught here rather than surfacing later as a
    silently-wrong row count or query error at load time."""
    prefix = _blob_prefix(manifest.scale_factor)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    downloader = container_client.download_blob(f"{prefix}/{entry.relative_path}")
    with dest_path.open("wb") as f:
        downloader.readinto(f)
    sha256, size = _sha256_file(dest_path)
    if sha256 != entry.sha256 or size != entry.byte_size:
        dest_path.unlink(missing_ok=True)
        raise CorpusVerificationError(
            f"{entry.relative_path}: downloaded sha256/size "
            f"({sha256}/{size}) does not match manifest "
            f"({entry.sha256}/{entry.byte_size})"
        )


def replicate_to_s3(
    container_client: ContainerClient,
    s3_client: S3Client,
    bucket: str,
    manifest: Manifest,
    *,
    key_prefix: str = "",
) -> None:
    """Copy every manifest entry from the Azure corpus to `bucket`, so the
    confirmed-AWS Databricks trial (plan §5) reads a same-cloud,
    checksum-verified copy instead of crossing clouds per query. Each file
    is verified against the manifest on the way out of Azure (via
    `download_verified`) before it is uploaded, and the S3 object's
    reported size is checked against the manifest afterward — a full
    re-download-and-rehash from S3 would cost as much again as the
    original transfer and isn't needed to catch a truncated upload."""
    prefix = _blob_prefix(manifest.scale_factor)
    with tempfile.TemporaryDirectory(prefix="tpch-corpus-replicate-") as tmp:
        tmp_dir = Path(tmp)
        for entry in manifest.files:
            local_path = tmp_dir / entry.relative_path
            download_verified(container_client, manifest, entry, local_path)
            key = f"{key_prefix}{prefix}/{entry.relative_path}"
            s3_client.upload_file(str(local_path), bucket, key)
            head: dict[str, Any] = s3_client.head_object(Bucket=bucket, Key=key)
            if head["ContentLength"] != entry.byte_size:
                raise CorpusVerificationError(
                    f"{entry.relative_path}: s3://{bucket}/{key} has "
                    f"{head['ContentLength']} bytes, manifest says "
                    f"{entry.byte_size}"
                )
            local_path.unlink()
