"""Guards for run provenance."""

from __future__ import annotations

from pathlib import Path

from supportkit.runs import create_run, latest_run, list_runs, previous_import_of


def _source(tmp_path: Path, body: bytes = b"a,b\n1,2\n") -> Path:
    src = tmp_path / "export.csv"
    src.write_bytes(body)
    return src


class TestManifests:
    def test_manifest_records_the_bytes_and_the_mapping(self, tmp_path: Path) -> None:
        run = create_run(tmp_path / "runs", _source(tmp_path), {"columns": {"date": "a"}})
        assert len(run.manifest["source_sha256"]) == 64
        assert run.manifest["mapping"]["columns"]["date"] == "a"
        assert (run.directory / "manifest.json").exists()

    def test_two_runs_in_one_second_do_not_collide(self, tmp_path: Path) -> None:
        src = _source(tmp_path)
        a = create_run(tmp_path / "runs", src)
        b = create_run(tmp_path / "runs", src)
        assert a.directory != b.directory

    def test_latest_run_and_listing(self, tmp_path: Path) -> None:
        src = _source(tmp_path)
        create_run(tmp_path / "runs", src)
        newest = create_run(tmp_path / "runs", src)
        assert latest_run(tmp_path / "runs").directory == newest.directory
        assert len(list_runs(tmp_path / "runs")) == 2

    def test_a_directory_without_a_manifest_is_not_a_run(self, tmp_path: Path) -> None:
        (tmp_path / "runs" / "not_a_run").mkdir(parents=True)
        assert list_runs(tmp_path / "runs") == []

    def test_reimport_of_identical_bytes_is_recognised(self, tmp_path: Path) -> None:
        src = _source(tmp_path)
        first = create_run(tmp_path / "runs", src)
        assert previous_import_of(tmp_path / "runs", first.manifest["source_sha256"]) is not None
        assert previous_import_of(tmp_path / "runs", "0" * 64) is None
