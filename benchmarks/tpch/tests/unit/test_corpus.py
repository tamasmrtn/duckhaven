import hashlib
from unittest.mock import MagicMock, patch

import pytest

from tpch_bench.datagen.corpus import (
    CorpusVerificationError,
    FileManifestEntry,
    Manifest,
    build_manifest,
    download_verified,
    fetch_manifest_from_azure,
    generate_corpus,
    publish_to_azure,
    replicate_to_s3,
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_build_manifest_hashes_flat_and_partitioned_tables(tmp_path):
    (tmp_path / "region.parquet").write_bytes(b"region-bytes")
    lineitem_dir = tmp_path / "lineitem"
    lineitem_dir.mkdir()
    (lineitem_dir / "lineitem.1.parquet").write_bytes(b"li-1")
    (lineitem_dir / "lineitem.2.parquet").write_bytes(b"li-2")

    manifest = build_manifest(1.0, tmp_path)

    by_path = {f.relative_path: f for f in manifest.files}
    assert set(by_path) == {
        "region.parquet",
        "lineitem/lineitem.1.parquet",
        "lineitem/lineitem.2.parquet",
    }
    assert by_path["region.parquet"].sha256 == _sha256(b"region-bytes")
    assert by_path["region.parquet"].byte_size == len(b"region-bytes")
    assert by_path["region.parquet"].table == "region"
    assert by_path["lineitem/lineitem.1.parquet"].table == "lineitem"


def test_build_manifest_skips_tables_that_were_not_generated(tmp_path):
    (tmp_path / "region.parquet").write_bytes(b"only-region")

    manifest = build_manifest(1.0, tmp_path)

    assert [f.table for f in manifest.files] == ["region"]


def test_manifest_json_round_trips():
    manifest = Manifest(
        scale_factor=10.0,
        files=(
            FileManifestEntry(
                table="region", relative_path="region.parquet", sha256="abc", byte_size=3
            ),
        ),
    )

    restored = Manifest.from_json(manifest.to_json())

    assert restored == manifest


@patch("tpch_bench.datagen.corpus.generate")
def test_generate_corpus_hashes_whatever_generate_produced(mock_generate, tmp_path):
    (tmp_path / "region.parquet").write_bytes(b"region-bytes")

    manifest = generate_corpus(scale_factor=1.0, corpus_dir=tmp_path, tables=("region",))

    mock_generate.assert_called_once_with(
        scale_factor=1.0, output_dir=tmp_path, tables=("region",), parts=None
    )
    assert manifest.scale_factor == 1.0
    assert manifest.files[0].sha256 == _sha256(b"region-bytes")


def test_publish_to_azure_uploads_every_file_and_the_manifest_under_the_sf_prefix(tmp_path):
    (tmp_path / "region.parquet").write_bytes(b"region-bytes")
    manifest = build_manifest(1.0, tmp_path)
    container = MagicMock()

    publish_to_azure(container, tmp_path, manifest)

    uploaded_names = [call.args[0] for call in container.upload_blob.call_args_list]
    assert "sf1/region.parquet" in uploaded_names
    assert "sf1/manifest.json" in uploaded_names
    for call in container.upload_blob.call_args_list:
        assert call.kwargs["overwrite"] is True


def test_fetch_manifest_from_azure_parses_the_downloaded_json():
    manifest = Manifest(
        scale_factor=1.0,
        files=(
            FileManifestEntry(
                table="region", relative_path="region.parquet", sha256="abc", byte_size=3
            ),
        ),
    )
    container = MagicMock()
    container.download_blob.return_value.readall.return_value = manifest.to_json().encode("utf-8")

    result = fetch_manifest_from_azure(container, 1.0)

    container.download_blob.assert_called_once_with("sf1/manifest.json")
    assert result == manifest


def test_download_verified_writes_the_file_when_the_hash_matches(tmp_path):
    data = b"region-bytes"
    entry = FileManifestEntry(
        table="region", relative_path="region.parquet", sha256=_sha256(data), byte_size=len(data)
    )
    manifest = Manifest(scale_factor=1.0, files=(entry,))
    container = MagicMock()
    container.download_blob.return_value.readinto.side_effect = lambda f: f.write(data)
    dest = tmp_path / "out" / "region.parquet"

    download_verified(container, manifest, entry, dest)

    container.download_blob.assert_called_once_with("sf1/region.parquet")
    assert dest.read_bytes() == data


def test_download_verified_raises_and_cleans_up_on_a_hash_mismatch(tmp_path):
    entry = FileManifestEntry(
        table="region", relative_path="region.parquet", sha256="deadbeef", byte_size=4
    )
    manifest = Manifest(scale_factor=1.0, files=(entry,))
    container = MagicMock()
    container.download_blob.return_value.readinto.side_effect = lambda f: f.write(b"wrong-bytes")
    dest = tmp_path / "region.parquet"

    with pytest.raises(CorpusVerificationError):
        download_verified(container, manifest, entry, dest)

    assert not dest.exists()


def test_replicate_to_s3_uploads_every_verified_file_and_checks_content_length(tmp_path):
    data = b"region-bytes"
    entry = FileManifestEntry(
        table="region", relative_path="region.parquet", sha256=_sha256(data), byte_size=len(data)
    )
    manifest = Manifest(scale_factor=1.0, files=(entry,))
    container = MagicMock()
    container.download_blob.return_value.readinto.side_effect = lambda f: f.write(data)
    s3 = MagicMock()
    s3.head_object.return_value = {"ContentLength": len(data)}

    replicate_to_s3(container, s3, "my-bucket", manifest)

    s3.upload_file.assert_called_once()
    local_path, bucket, key = s3.upload_file.call_args.args
    assert bucket == "my-bucket"
    assert key == "sf1/region.parquet"
    s3.head_object.assert_called_once_with(Bucket="my-bucket", Key="sf1/region.parquet")


def test_replicate_to_s3_raises_when_the_uploaded_object_size_does_not_match(tmp_path):
    data = b"region-bytes"
    entry = FileManifestEntry(
        table="region", relative_path="region.parquet", sha256=_sha256(data), byte_size=len(data)
    )
    manifest = Manifest(scale_factor=1.0, files=(entry,))
    container = MagicMock()
    container.download_blob.return_value.readinto.side_effect = lambda f: f.write(data)
    s3 = MagicMock()
    s3.head_object.return_value = {"ContentLength": len(data) - 1}

    with pytest.raises(CorpusVerificationError):
        replicate_to_s3(container, s3, "my-bucket", manifest)
