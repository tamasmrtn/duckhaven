import subprocess
from unittest.mock import patch

import pytest

from tpch_bench.datagen.tpchgen_runner import (
    TpchgenGenerationError,
    TpchgenNotFoundError,
    generate,
)


@patch("tpch_bench.datagen.tpchgen_runner.shutil.which", return_value=None)
def test_generate_raises_when_the_binary_is_not_on_path(mock_which, tmp_path):
    with pytest.raises(TpchgenNotFoundError):
        generate(scale_factor=1, output_dir=tmp_path)


@patch("tpch_bench.datagen.tpchgen_runner.subprocess.run")
@patch("tpch_bench.datagen.tpchgen_runner.shutil.which", return_value="/usr/bin/tpchgen-cli")
def test_generate_builds_the_expected_cli_invocation(mock_which, mock_run, tmp_path):
    mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
    (tmp_path / "region.parquet").write_bytes(b"")
    (tmp_path / "nation.parquet").write_bytes(b"")

    generate(
        scale_factor=10,
        output_dir=tmp_path,
        tables=("region", "nation"),
        compression="ZSTD(1)",
        num_threads=4,
    )

    args = mock_run.call_args.args[0]
    assert args[0] == "/usr/bin/tpchgen-cli"
    assert "parquet" in args
    assert "-s" in args and args[args.index("-s") + 1] == "10"
    assert args[args.index("--tables") + 1] == "region,nation"
    assert args[args.index("--compression") + 1] == "ZSTD(1)"
    assert args[args.index("--num-threads") + 1] == "4"
    assert "--parts" not in args


@patch("tpch_bench.datagen.tpchgen_runner.subprocess.run")
@patch("tpch_bench.datagen.tpchgen_runner.shutil.which", return_value="/usr/bin/tpchgen-cli")
def test_generate_returns_unpartitioned_files_per_table(mock_which, mock_run, tmp_path):
    mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
    (tmp_path / "region.parquet").write_bytes(b"data")

    results = generate(scale_factor=1, output_dir=tmp_path, tables=("region",))

    assert len(results) == 1
    assert results[0].table == "region"
    assert results[0].files == [tmp_path / "region.parquet"]


@patch("tpch_bench.datagen.tpchgen_runner.subprocess.run")
@patch("tpch_bench.datagen.tpchgen_runner.shutil.which", return_value="/usr/bin/tpchgen-cli")
def test_generate_returns_partitioned_files_per_table(mock_which, mock_run, tmp_path):
    mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
    part_dir = tmp_path / "lineitem"
    part_dir.mkdir()
    (part_dir / "lineitem.1.parquet").write_bytes(b"a")
    (part_dir / "lineitem.2.parquet").write_bytes(b"b")

    results = generate(scale_factor=1, output_dir=tmp_path, tables=("lineitem",), parts=2)

    assert results[0].files == [
        part_dir / "lineitem.1.parquet",
        part_dir / "lineitem.2.parquet",
    ]


@patch("tpch_bench.datagen.tpchgen_runner.subprocess.run")
@patch("tpch_bench.datagen.tpchgen_runner.shutil.which", return_value="/usr/bin/tpchgen-cli")
def test_generate_raises_with_stderr_on_a_nonzero_exit(mock_which, mock_run, tmp_path):
    mock_run.side_effect = subprocess.CalledProcessError(
        returncode=2, cmd=["tpchgen-cli"], stderr="bad scale factor"
    )

    with pytest.raises(TpchgenGenerationError, match="bad scale factor"):
        generate(scale_factor=1, output_dir=tmp_path, tables=("region",))


@patch("tpch_bench.datagen.tpchgen_runner.subprocess.run")
@patch("tpch_bench.datagen.tpchgen_runner.shutil.which", return_value="/usr/bin/tpchgen-cli")
def test_generate_raises_when_an_expected_file_is_missing(mock_which, mock_run, tmp_path):
    mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
    # region.parquet deliberately not created, simulating a silent CLI gap.

    with pytest.raises(TpchgenGenerationError, match="did not produce"):
        generate(scale_factor=1, output_dir=tmp_path, tables=("region",))
