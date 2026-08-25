"""Run directories with provenance: which data produced this number?

That question is unanswerable after the fact unless it was recorded during.
Every import creates a run directory whose manifest carries the source's
SHA-256, the mapping that interpreted it, and the tool version — so a result
can always be traced to exactly the bytes and the judgment that produced it,
and a re-import of the same file is recognisable as such.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import __version__

MANIFEST = "manifest.json"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class Run:
    directory: Path
    manifest: dict


def create_run(root: Path, source: Path, mapping: dict | None = None) -> Run:
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    directory = root / f"run_{stamp}"
    suffix = 1
    while directory.exists():
        # Two imports in one second is rare but real; never overwrite a run.
        suffix += 1
        directory = root / f"run_{stamp}_{suffix}"
    directory.mkdir(parents=True)
    manifest = {
        "source_name": source.name,
        "source_sha256": sha256_of(source),
        "source_bytes": source.stat().st_size,
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "supportkit_version": __version__,
        "mapping": mapping or {},
    }
    (directory / MANIFEST).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return Run(directory=directory, manifest=manifest)


def list_runs(root: Path) -> list[Run]:
    """Every valid run under root, oldest first. A directory without a
    readable manifest is not a run and is skipped, not guessed at."""
    runs = []
    if not root.exists():
        return runs
    for directory in sorted(root.iterdir()):
        candidate = directory / MANIFEST
        if not candidate.is_file():
            continue
        try:
            manifest = json.loads(candidate.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        runs.append(Run(directory=directory, manifest=manifest))
    return runs


def latest_run(root: Path) -> Run | None:
    runs = list_runs(root)
    return runs[-1] if runs else None


def previous_import_of(root: Path, source_sha256: str) -> Run | None:
    """The most recent prior run of the SAME bytes — a re-import, not new data."""
    matches = [r for r in list_runs(root) if r.manifest.get("source_sha256") == source_sha256]
    return matches[-1] if matches else None
