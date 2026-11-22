"""Builds ``data/processed/reference.duckdb`` from the pinned CMS files.

For each source, in order:
    1. find its files in data/raw (a missing source is reported, not guessed around)
    2. check each file against MANIFEST.tsv (size and SHA-256)
    3. parse it and validate every table with its pandera schema
Only then is anything written. The database is built in a temporary file and moved into place
at the end, so a failed build never leaves a half-written database behind.

A source that is present but wrong (bad checksum, wrong layout, failed validation) fails the
whole build. A source that is simply absent (for example, a license-gated file the user hasn't
downloaded yet) is recorded as missing, and lookups that need it return "could not check".
"""

from __future__ import annotations

import fnmatch
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pandera.polars as pa
import polars as pl

from billauditor.data.verify import read_manifest, verify_entry
from billauditor.reference import parsers, schemas
from billauditor.reference.sources import SOURCES, Source
from billauditor.result import Err, ErrorCode, Ok, Result, err
from billauditor.tracing import Tracer

Tables = list[tuple[str, pl.DataFrame, type[pa.DataFrameModel]]]
Loader = Callable[[list[Path]], Tables]


def _load_hcpcs(files: list[Path]) -> Tables:
    codes, modifiers = parsers.parse_hcpcs(files[0])
    return [
        ("hcpcs_codes", codes, schemas.HcpcsCodes),
        ("hcpcs_modifiers", modifiers, schemas.HcpcsModifiers),
    ]


def _load_icd10cm(files: list[Path]) -> Tables:
    return [("icd10cm", parsers.parse_icd10cm(files[0]), schemas.Icd10cm)]


def _load_mue(table: str) -> Loader:
    def load(files: list[Path]) -> Tables:
        return [(table, parsers.parse_mue(files[0]), schemas.Mue)]

    return load


def _load_pfs(files: list[Path]) -> Tables:
    return [
        ("pfs_rvu", parsers.parse_pfs_rvu(files[0]), schemas.PfsRvu),
        ("pfs_gpci", parsers.parse_pfs_gpci(files[0]), schemas.PfsGpci),
    ]


LOADERS: dict[str, Loader] = {
    "hcpcs": _load_hcpcs,
    "icd10cm": _load_icd10cm,
    "mue_outpatient_hospital": _load_mue("mue_outpatient_hospital"),
    "mue_practitioner": _load_mue("mue_practitioner"),
    "pfs": _load_pfs,
}


@dataclass
class SourceStatus:
    name: str
    version: str
    status: str  # loaded | missing | parser_pending
    message: str = ""
    files: list[str] = field(default_factory=list)
    rows: dict[str, int] = field(default_factory=dict)


@dataclass
class BuildReport:
    db_path: str
    sources: list[SourceStatus]

    @property
    def missing(self) -> list[str]:
        return [s.name for s in self.sources if s.status != "loaded"]


def find_files(raw_dir: Path, source: Source) -> list[Path]:
    """Files matching the source's pattern, case-insensitively (browsers vary in naming)."""
    folder = raw_dir / Path(source.pattern).parent
    if not folder.is_dir():
        return []
    name_pattern = Path(source.pattern).name.lower()
    return sorted(
        p for p in folder.iterdir() if p.is_file() and fnmatch.fnmatch(p.name.lower(), name_pattern)
    )


def _check_files(raw_dir: Path, manifest_path: Path, files: list[Path]) -> Result[None]:
    manifest = read_manifest(manifest_path)
    if isinstance(manifest, Err):
        return manifest
    entries = {e.relative_path: e for e in manifest.value}
    for path in files:
        relative = path.relative_to(raw_dir).as_posix()
        entry = entries.get(relative)
        if entry is None:
            return err(
                ErrorCode.DATA_MISSING,
                f"{relative} is not in MANIFEST.tsv; record it with "
                f"`billauditor add-data data/raw/{relative} --source <url>`",
            )
        checked = verify_entry(raw_dir, entry)
        if isinstance(checked, Err):
            return checked
    return Ok(None)


def _validate(tables: Tables) -> Result[Tables]:
    validated: Tables = []
    for table, df, schema in tables:
        try:
            validated.append((table, schema.validate(df, lazy=True), schema))
        except pa.errors.SchemaErrors as exc:
            return err(
                ErrorCode.SCHEMA_INVALID,
                f"table {table} failed validation",
                table=table,
                failures=str(exc)[:3000],
            )
    return Ok(validated)


def _load_source(
    raw_dir: Path, manifest_path: Path, source: Source
) -> Result[tuple[SourceStatus, Tables]]:
    files = find_files(raw_dir, source)
    rel = [f.relative_to(raw_dir).as_posix() for f in files]
    if not files:
        how = (
            "accept the AMA license on cms.gov and download it"
            if source.license_gated
            else "download it"
        )
        status = SourceStatus(
            source.name, source.version, "missing", f"not in data/raw; {how} from {source.page}"
        )
        return Ok((status, []))
    if len(files) != source.expected_files:
        return err(
            ErrorCode.DATA_MISSING,
            f"{source.name}: expected {source.expected_files} file(s), found {len(files)}",
            found=rel,
        )
    checked = _check_files(raw_dir, manifest_path, files)
    if isinstance(checked, Err):
        return checked
    loader = LOADERS.get(source.name)
    if loader is None:
        status = SourceStatus(
            source.name,
            source.version,
            "parser_pending",
            "files present; parser not written yet",
            rel,
        )
        return Ok((status, []))
    try:
        tables = loader(files)
    except parsers.ParseError as exc:
        return err(ErrorCode.SCHEMA_INVALID, f"{source.name}: {exc}")
    validated = _validate(tables)
    if isinstance(validated, Err):
        return validated
    rows = {table: df.height for table, df, _ in validated.value}
    return Ok((SourceStatus(source.name, source.version, "loaded", "", rel, rows), validated.value))


def _write_db(db_path: Path, statuses: list[SourceStatus], tables: Tables) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(".building")
    tmp.unlink(missing_ok=True)
    con = duckdb.connect(str(tmp))
    ok = False
    try:
        for table, df, _ in tables:
            con.register("incoming", df)
            con.execute(f"CREATE TABLE {table} AS SELECT * FROM incoming")
            con.unregister("incoming")
        con.execute(
            "CREATE TABLE reference_sources (name VARCHAR PRIMARY KEY, title VARCHAR, "
            "version VARCHAR, status VARCHAR, message VARCHAR, files VARCHAR, row_counts VARCHAR, "
            "built_at VARCHAR)"
        )
        built_at = datetime.now(UTC).isoformat(timespec="seconds")
        titles = {s.name: s.title for s in SOURCES}
        for s in statuses:
            con.execute(
                "INSERT INTO reference_sources VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    s.name,
                    titles[s.name],
                    s.version,
                    s.status,
                    s.message,
                    json.dumps(s.files),
                    json.dumps(s.rows),
                    built_at,
                ],
            )
        ok = True
    finally:
        con.close()
        if not ok:
            tmp.unlink(missing_ok=True)  # never leave a half-built database behind
    tmp.replace(db_path)


def build_reference(
    raw_dir: Path, manifest_path: Path, db_path: Path, tracer: Tracer
) -> Result[BuildReport]:
    statuses: list[SourceStatus] = []
    all_tables: Tables = []
    for source in SOURCES:
        with tracer.stage(f"reference:{source.name}", version=source.version) as span:
            loaded = _load_source(raw_dir, manifest_path, source)
            if isinstance(loaded, Err):
                span.error = loaded.error
                return loaded
            status, tables = loaded.value
            span.attrs.update(status=status.status, rows=status.rows)
            statuses.append(status)
            all_tables.extend(tables)
    with tracer.stage("reference:write_db", tables=len(all_tables)) as span:
        try:
            _write_db(db_path, statuses, all_tables)
        except (duckdb.Error, OSError) as exc:
            failed = err(
                ErrorCode.INTERNAL,
                f"could not write {db_path}: {exc}",
                stage="reference:write_db",
            )
            span.error = failed.error
            return failed
    return Ok(BuildReport(db_path=str(db_path), sources=statuses))
