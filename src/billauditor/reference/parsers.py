"""Parsers that turn raw CMS files into clean Polars tables.

Each parser reads straight from the downloaded zip, so the files on disk stay exactly as CMS
published them, matching their manifest checksums. A file whose layout doesn't match what a
parser expects raises ``ParseError``; the build turns that into a ``SCHEMA_INVALID`` error
instead of loading half-parsed data.

Licensing: AMA CPT descriptions are never kept. Where a CMS file includes them (the PFS
relative value file, for example), that column is dropped here, at parse time.
"""

from __future__ import annotations

import csv
import fnmatch
import io
import zipfile
from decimal import Decimal, InvalidOperation
from pathlib import Path

import polars as pl


class ParseError(Exception):
    """The file's layout isn't what this parser expects."""


def read_member(zip_path: Path, pattern: str) -> tuple[str, bytes]:
    """Read the single zip member whose name matches ``pattern`` (case-insensitive glob)."""
    with zipfile.ZipFile(zip_path) as zf:
        names = [n for n in zf.namelist() if fnmatch.fnmatch(n.lower(), pattern.lower())]
        if len(names) != 1:
            raise ParseError(
                f"{zip_path.name}: expected exactly one member matching {pattern!r}, found {names}"
            )
        return names[0], zf.read(names[0])


def _decimal(value: str, field: str) -> Decimal:
    text = value.strip()
    try:
        return Decimal(text) if text else Decimal("0")
    except InvalidOperation as exc:
        raise ParseError(f"{field}: {value!r} is not a number") from exc


def _csv_rows(data: bytes) -> list[list[str]]:
    # CMS CSVs are Windows-1252; the AMA notice contains a non-UTF-8 registered-trademark sign.
    return list(csv.reader(io.StringIO(data.decode("cp1252"))))


def _find_header(rows: list[list[str]], first_cell: str) -> int:
    for i, row in enumerate(rows):
        if row and row[0].replace("\n", "").strip().upper().startswith(first_cell):
            return i
    raise ParseError(f"no header row starting with {first_cell!r}")


# -- HCPCS Level II ----------------------------------------------------------------------------


def _field(line: str, start: int, end: int) -> str:
    """1-based, inclusive column positions, as in the CMS record layout."""
    return line[start - 1 : end].strip()


def parse_hcpcs(zip_path: Path) -> tuple[pl.DataFrame, pl.DataFrame]:
    """HCPCS fixed-width file -> (codes, modifiers).

    Record identification codes (column 11): 3 = first line of a procedure code, 4 = more of
    its long description, 7 = first line of a modifier, 8 = more of its long description.
    """
    _, data = read_member(zip_path, "hcpc*_anweb_*.txt")
    codes: list[dict[str, str]] = []
    modifiers: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line_no, raw in enumerate(data.decode("cp1252").splitlines(), start=1):
        if not raw.strip():
            continue
        line = raw.ljust(320)
        record_id = line[10]
        long_part = _field(line, 12, 91)
        if record_id == "3":
            current = {
                "code": _field(line, 1, 5),
                "long_description": long_part,
                "short_description": _field(line, 92, 119),
                "pricing_indicator": _field(line, 120, 121),
                "coverage_code": _field(line, 230, 230),
                "betos": _field(line, 257, 259),
                "type_of_service": _field(line, 261, 261),
                "added_date": _field(line, 269, 276),
                "effective_date": _field(line, 277, 284),
                "termination_date": _field(line, 285, 292),
                "action_code": _field(line, 293, 293),
            }
            codes.append(current)
        elif record_id == "7":
            current = {
                "modifier": _field(line, 4, 5),
                "long_description": long_part,
                "short_description": _field(line, 92, 119),
            }
            modifiers.append(current)
        elif record_id in ("4", "8"):
            if current is None:
                raise ParseError(f"line {line_no}: continuation line before any record")
            current["long_description"] = f"{current['long_description']} {long_part}".strip()
        else:
            raise ParseError(f"line {line_no}: unknown record id {record_id!r}")
    if not codes:
        raise ParseError("no HCPCS code records found")
    return pl.DataFrame(codes), pl.DataFrame(modifiers)


# -- ICD-10-CM ---------------------------------------------------------------------------------


def parse_icd10cm(zip_path: Path) -> pl.DataFrame:
    """``icd10cm_codes_YYYY.txt``: the code (no dot), padding, then the description."""
    _, data = read_member(zip_path, "*icd10cm_codes_20??.txt")
    rows = []
    for line_no, line in enumerate(data.decode("cp1252").splitlines(), start=1):
        if not line.strip():
            continue
        code, _, description = line.partition(" ")
        if not description.strip():
            raise ParseError(f"line {line_no}: no description for {code!r}")
        rows.append({"code": code.strip(), "description": description.strip()})
    return pl.DataFrame(rows)


# -- NCCI MUEs ---------------------------------------------------------------------------------


def parse_mue(zip_path: Path) -> pl.DataFrame:
    """MUE CSV: an AMA notice, then ``HCPCS/CPT Code, MUE Values, MUE Adjudication Indicator,
    MUE Rationale``."""
    _, data = read_member(zip_path, "*.csv")
    rows = _csv_rows(data)
    header = _find_header(rows, "HCPCS")
    columns = [c.replace("\n", " ").strip().lower() for c in rows[header]]
    if len(columns) != 4 or "mue values" not in columns[1] or "adjudication" not in columns[2]:
        raise ParseError(f"unexpected MUE columns: {rows[header]}")
    out = []
    for row in rows[header + 1 :]:
        if not row or not row[0].strip():
            continue
        if len(row) != 4:
            raise ParseError(f"MUE row has {len(row)} columns: {row}")
        label = row[2].strip()
        indicator = label[:1]
        if not indicator.isdigit():
            raise ParseError(f"MUE adjudication indicator not numeric: {label!r}")
        try:
            max_units = int(row[1].strip())
        except ValueError as exc:
            raise ParseError(f"MUE value not an integer: {row}") from exc
        out.append(
            {
                "code": row[0].strip().upper(),
                "max_units": max_units,
                "adjudication_indicator": int(indicator),
                "adjudication_label": label,
                "rationale": row[3].strip(),
            }
        )
    return pl.DataFrame(out)


# -- Physician Fee Schedule --------------------------------------------------------------------

# Column positions in PPRRVU (after the HCPCS header row), checked against the header names.
_PFS_COLUMNS = {
    "code": (0, "HCPCS"),
    "modifier": (1, "MOD"),
    # 2 is DESCRIPTION: AMA CPT text, deliberately not read
    "status_code": (3, "CODE"),
    "work_rvu": (5, "RVU"),
    "nonfacility_pe_rvu": (6, "PE RVU"),
    "nonfacility_na": (7, "INDICATOR"),
    "facility_pe_rvu": (8, "PE RVU"),
    "facility_na": (9, "INDICATOR"),
    "mp_rvu": (10, "RVU"),
    "nonfacility_total_rvu": (11, "TOTAL"),
    "facility_total_rvu": (12, "TOTAL"),
    "pctc_indicator": (13, "IND"),
    "global_days": (14, "DAYS"),
    "conversion_factor": (25, "FACTOR"),
}
_PFS_DECIMALS = {
    "work_rvu",
    "nonfacility_pe_rvu",
    "facility_pe_rvu",
    "mp_rvu",
    "nonfacility_total_rvu",
    "facility_total_rvu",
    "conversion_factor",
}


def parse_pfs_rvu(zip_path: Path) -> pl.DataFrame:
    """PPRRVU (non-QPP) relative values. The CPT description column is dropped."""
    _, data = read_member(zip_path, "pprrvu*_nonqpp.csv")
    rows = _csv_rows(data)
    header = _find_header(rows, "HCPCS")
    names = [c.strip().upper() for c in rows[header]]
    for field, (idx, expected) in _PFS_COLUMNS.items():
        if idx >= len(names) or names[idx] != expected:
            raise ParseError(f"PFS column {idx} should be {expected!r} for {field}: {names}")
    out = []
    for row in rows[header + 1 :]:
        if not row or not row[0].strip():
            continue
        rec: dict[str, object] = {}
        for field, (idx, _) in _PFS_COLUMNS.items():
            value = row[idx].strip()
            if field in _PFS_DECIMALS:
                rec[field] = _decimal(value, field)
            elif field in ("nonfacility_na", "facility_na"):
                rec[field] = value.upper() == "NA"
            else:
                rec[field] = value.upper()
        out.append(rec)
    schema = {f: (pl.Decimal(12, 4) if f in _PFS_DECIMALS else None) for f in _PFS_COLUMNS}
    return pl.DataFrame(out, schema_overrides={k: v for k, v in schema.items() if v is not None})


def parse_pfs_gpci(zip_path: Path) -> pl.DataFrame:
    """GPCI by Medicare locality (used to adjust payment for Boston vs. elsewhere)."""
    _, data = read_member(zip_path, "gpci20??.csv")
    rows = _csv_rows(data)
    header = _find_header(rows, "MEDICARE ADMINISTRATIVE CONTRACTOR")
    seen: dict[tuple[str, str], dict[str, object]] = {}
    for row in rows[header + 1 :]:
        if len(row) < 7 or not row[0].strip().isdigit():
            continue  # footnotes
        rec: dict[str, object] = {
            "mac": row[0].strip(),
            "state": row[1].strip(),
            "locality": row[2].strip().zfill(2),
            "locality_name": row[3].strip().rstrip("*").strip(),
            "work_gpci": _decimal(row[4], "work_gpci"),
            "pe_gpci": _decimal(row[5], "pe_gpci"),
            "mp_gpci": _decimal(row[6], "mp_gpci"),
        }
        key = (str(rec["mac"]), str(rec["locality"]))
        if key in seen and seen[key] != rec:
            raise ParseError(f"conflicting GPCI rows for {key}")
        seen[key] = rec  # the file repeats some rows identically; keep one
    return pl.DataFrame(
        list(seen.values()),
        schema_overrides={c: pl.Decimal(6, 3) for c in ("work_gpci", "pe_gpci", "mp_gpci")},
    )
