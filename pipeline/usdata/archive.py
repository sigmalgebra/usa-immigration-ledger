"""Sole owner of archive/: a content-addressed, append-only store of source files.

USCIS deletes superseded files, so the archive (not the publisher URL) is canonical.
Adding the same bytes twice is a no-op, which makes crawl and add safe to rerun.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict
from pathlib import Path

from .model import DATASET_IDS, DatasetId, SourceFile, now_utc

REPO = Path(__file__).resolve().parents[2]
ARCHIVE = REPO / "archive"
MANIFEST = "manifest.jsonl"


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_")


def load_manifest(root: Path = ARCHIVE) -> tuple[SourceFile, ...]:
    path = root / MANIFEST
    if not path.exists():
        return ()
    files = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            files.append(SourceFile(**json.loads(line)))
    return tuple(files)


def add(data: bytes, *, dataset: DatasetId, url: str, original_name: str,
        retrieved_by: str, via: str | None = None, root: Path = ARCHIVE) -> tuple[SourceFile, bool]:
    """Archive `data`. Returns (file, created). Never rewrites an existing blob or manifest line."""
    if dataset not in DATASET_IDS:
        raise ValueError(f"unknown dataset {dataset!r}")
    sha = hashlib.sha256(data).hexdigest()
    for f in load_manifest(root):
        if f.sha256 == sha:
            return f, False

    rel = Path("raw") / dataset / f"{sha[:12]}__{_safe_name(original_name)}"
    blob = root / rel
    blob.parent.mkdir(parents=True, exist_ok=True)
    tmp = blob.with_suffix(blob.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, blob)

    file = SourceFile(
        source_id=sha[:12], dataset=dataset, url=url, original_name=original_name, sha256=sha,
        retrieved_at=now_utc(), retrieved_by=retrieved_by,  # type: ignore[arg-type]
        archive_path=(Path("archive") / rel).as_posix(), via=via,
    )
    with (root / MANIFEST).open("a", encoding="utf-8", newline="\n") as fh:
        row = {k: v for k, v in asdict(file).items() if v is not None}  # files without `via` keep the old line shape
        fh.write(json.dumps(row, sort_keys=True) + "\n")
    return file, True


def read_bytes(f: SourceFile, repo: Path = REPO) -> bytes:
    data = (repo / f.archive_path).read_bytes()
    if hashlib.sha256(data).hexdigest() != f.sha256:
        raise RuntimeError(f"archived file {f.archive_path} does not match its sha256; the archive was edited")
    return data
