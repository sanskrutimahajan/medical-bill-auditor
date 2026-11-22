"""Checks manually downloaded raw data against ``data/raw/MANIFEST.tsv``.

Every dataset (CMS files, hospital price files) is downloaded once, then frozen: the manifest
records its path, SHA-256, size, source URL and download date. Evaluations only run against
files that match the manifest, so a score can't change because the data quietly changed.

The manifest is tab-separated, with the header
``relative_path  sha256  bytes  source  retrieved_on``.
"""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from billauditor.result import Err, ErrorCode, Ok, Result, err

MANIFEST_COLUMNS = ("relative_path", "sha256", "bytes", "source", "retrieved_on")


@dataclass(frozen=True)
class ManifestEntry:
    relative_path: str
    sha256: str
    bytes: int
    source: str
    retrieved_on: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_inside(raw_dir: Path, relative_path: str) -> Result[Path]:
    """Refuse manifest paths that point outside the raw data folder."""
    root = raw_dir.resolve()
    target = (raw_dir / relative_path).resolve()
    if not target.is_relative_to(root):
        return err(
            ErrorCode.INVALID_INPUT,
            f"{relative_path!r} points outside {raw_dir}",
            relative_path=relative_path,
        )
    return Ok(target)


def read_manifest(manifest_path: Path) -> Result[list[ManifestEntry]]:
    if not manifest_path.exists():
        return err(ErrorCode.DATA_MISSING, f"no manifest at {manifest_path}")
    with manifest_path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        if tuple(reader.fieldnames or ()) != MANIFEST_COLUMNS:
            return err(
                ErrorCode.SCHEMA_INVALID,
                "manifest header is wrong",
                expected=list(MANIFEST_COLUMNS),
                found=list(reader.fieldnames or ()),
            )
        entries: list[ManifestEntry] = []
        for line_no, row in enumerate(reader, start=2):
            try:
                entries.append(
                    ManifestEntry(
                        relative_path=row["relative_path"],
                        sha256=row["sha256"].lower(),
                        bytes=int(row["bytes"]),
                        source=row["source"],
                        retrieved_on=row["retrieved_on"],
                    )
                )
            except (TypeError, ValueError, AttributeError) as exc:
                return err(
                    ErrorCode.SCHEMA_INVALID,
                    f"manifest line {line_no} is malformed: {exc}",
                    line=line_no,
                )
    return Ok(entries)


def verify_entry(raw_dir: Path, entry: ManifestEntry) -> Result[ManifestEntry]:
    resolved = _resolve_inside(raw_dir, entry.relative_path)
    if isinstance(resolved, Err):
        return resolved
    path = resolved.value
    if not path.is_file():
        return err(
            ErrorCode.DATA_MISSING,
            f"{entry.relative_path} is listed in the manifest but not on disk; "
            f"download it from {entry.source}",
            relative_path=entry.relative_path,
            source=entry.source,
        )
    size = path.stat().st_size
    if size != entry.bytes:
        return err(
            ErrorCode.CHECKSUM_MISMATCH,
            f"{entry.relative_path} is {size} bytes; the manifest says {entry.bytes}",
            relative_path=entry.relative_path,
        )
    actual = sha256_file(path)
    if actual != entry.sha256:
        return err(
            ErrorCode.CHECKSUM_MISMATCH,
            f"{entry.relative_path} has a different SHA-256 than the manifest",
            relative_path=entry.relative_path,
            expected=entry.sha256,
            actual=actual,
        )
    return Ok(entry)


def verify_all(raw_dir: Path, manifest_path: Path) -> Result[list[Result[ManifestEntry]]]:
    """Check every manifest entry. The outer ``Err`` means the manifest itself is unusable."""
    manifest = read_manifest(manifest_path)
    if isinstance(manifest, Err):
        return manifest
    return Ok([verify_entry(raw_dir, entry) for entry in manifest.value])


def add_entry(
    raw_dir: Path,
    manifest_path: Path,
    file: Path,
    *,
    source: str,
    retrieved_on: str | None = None,
) -> Result[ManifestEntry]:
    """Record a newly downloaded file in the manifest (it must already be inside ``raw_dir``)."""
    if not file.is_file():
        return err(ErrorCode.INPUT_NOT_FOUND, f"no file at {file}")
    root = raw_dir.resolve()
    target = file.resolve()
    if not target.is_relative_to(root):
        return err(ErrorCode.INVALID_INPUT, f"{file} must be inside {raw_dir}")
    if target == manifest_path.resolve():
        return err(ErrorCode.INVALID_INPUT, "the manifest can't list itself")
    if not source.strip():
        return err(ErrorCode.INVALID_INPUT, "a source URL or description is required")

    if not manifest_path.exists():
        manifest_path.write_text("\t".join(MANIFEST_COLUMNS) + "\n", encoding="utf-8")
    manifest = read_manifest(manifest_path)
    if isinstance(manifest, Err):
        return manifest
    relative = target.relative_to(root).as_posix()
    if any(entry.relative_path == relative for entry in manifest.value):
        return err(
            ErrorCode.INVALID_INPUT,
            f"{relative} is already in the manifest; remove its line first to replace it",
        )

    entry = ManifestEntry(
        relative_path=relative,
        sha256=sha256_file(target),
        bytes=target.stat().st_size,
        source=source.strip(),
        retrieved_on=retrieved_on or date.today().isoformat(),
    )
    with manifest_path.open("a", encoding="utf-8", newline="") as fh:
        csv.writer(fh, delimiter="\t", lineterminator="\n").writerow(
            [entry.relative_path, entry.sha256, entry.bytes, entry.source, entry.retrieved_on]
        )
    return Ok(entry)
