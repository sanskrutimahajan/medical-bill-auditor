"""Read-only lookups against the reference database.

Every answer carries a ``Citation`` (dataset, release and the row it came from), so a later
finding can say exactly which rule or price it relied on.

The result types keep three situations apart:
    Ok(value)          the data was checked and here is the answer
    Ok(None)           the data was checked and has no entry (for example, no MUE published)
    Err(DATA_MISSING)  the data needed isn't loaded, so this could not be checked

All arithmetic (fee-schedule payment amounts) is exact ``Decimal`` math done here, in code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Literal

import duckdb

from billauditor.reference.sources import SOURCES_BY_NAME
from billauditor.result import Err, ErrorCode, Ok, Result, err

HCPCS_RE = re.compile(r"^[A-Z0-9]{5}$")
ICD_RE = re.compile(r"^[A-Z][0-9A-Z][0-9A-Z]{1,5}$")
LOCALITY_RE = re.compile(r"^([A-Z]{2})-(\d{2})$")
CENTS = Decimal("0.01")

MueSetting = Literal["outpatient_hospital", "practitioner"]
FeeSetting = Literal["nonfacility", "facility"]

# PFS status codes that have a fee-schedule amount (CMS RVU file documentation).
PAYABLE_STATUS = {"A", "R", "T"}


@dataclass(frozen=True)
class Citation:
    dataset: str
    version: str
    key: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {"dataset": self.dataset, "version": self.version, "key": self.key}


@dataclass(frozen=True)
class CodeInfo:
    code: str
    kind: str  # hcpcs_level2 | cpt | unknown
    description: str | None
    description_note: str | None
    terminated: bool
    citation: Citation | None


@dataclass(frozen=True)
class Diagnosis:
    code: str
    description: str
    citation: Citation


@dataclass(frozen=True)
class MueLimit:
    code: str
    setting: MueSetting
    max_units: int
    adjudication_indicator: int
    adjudication_label: str
    rationale: str
    citation: Citation


@dataclass(frozen=True)
class PhysicianRate:
    code: str
    modifier: str
    setting: FeeSetting
    locality: str
    status_code: str
    amount: Decimal | None
    reason: str | None
    formula: str
    inputs: dict[str, str]
    citation: Citation


def normalize_hcpcs(code: str) -> Result[str]:
    value = code.strip().upper()
    if not HCPCS_RE.match(value):
        return err(ErrorCode.INVALID_INPUT, f"{code!r} is not a 5-character procedure code")
    return Ok(value)


def normalize_icd10cm(code: str) -> Result[str]:
    value = code.strip().upper().replace(".", "")
    if not ICD_RE.match(value):
        return err(ErrorCode.INVALID_INPUT, f"{code!r} is not an ICD-10-CM code")
    return Ok(value)


class ReferenceData:
    """Lookups over ``reference.duckdb``. Open it with ``ReferenceData.open``."""

    def __init__(self, con: duckdb.DuckDBPyConnection, statuses: dict[str, str]) -> None:
        self._con = con
        self._status = statuses

    @classmethod
    def open(cls, db_path: Path) -> Result[ReferenceData]:
        if not db_path.exists():
            return err(
                ErrorCode.DATA_MISSING,
                f"no reference database at {db_path}; run `make ref-build`",
            )
        try:
            con = duckdb.connect(str(db_path), read_only=True)
            rows = con.execute("SELECT name, status FROM reference_sources").fetchall()
        except duckdb.Error as exc:
            return err(ErrorCode.DATA_MISSING, f"reference database unreadable: {exc}")
        return Ok(cls(con, {name: status for name, status in rows}))

    def close(self) -> None:
        self._con.close()

    def source_status(self) -> dict[str, str]:
        return dict(self._status)

    # -- helpers -----------------------------------------------------------------------------

    def _require(self, source: str, purpose: str) -> Err | None:
        if self._status.get(source) != "loaded":
            src = SOURCES_BY_NAME[source]
            return err(
                ErrorCode.DATA_MISSING,
                f"could not check {purpose}: {src.title} ({src.version}) is not loaded",
                source=source,
            )
        return None

    def _cite(self, source: str, **key: str) -> Citation:
        return Citation(dataset=source, version=SOURCES_BY_NAME[source].version, key=key)

    def _one(self, sql: str, params: list[Any]) -> dict[str, Any] | None:
        cursor = self._con.execute(sql, params)
        row = cursor.fetchone()
        if row is None:
            return None
        names = [d[0] for d in cursor.description]
        return dict(zip(names, row, strict=True))

    # -- lookups -----------------------------------------------------------------------------

    def code(self, code: str) -> Result[CodeInfo]:
        """What a procedure code is. Level II codes get their CMS description; CPT codes
        (Level I) never do, because those descriptions are AMA-licensed."""
        normalized = normalize_hcpcs(code)
        if isinstance(normalized, Err):
            return normalized
        value = normalized.value
        if missing := self._require("hcpcs", "the procedure code"):
            return missing
        row = self._one(
            "SELECT long_description, termination_date FROM hcpcs_codes WHERE code = ?", [value]
        )
        if row is not None:
            return Ok(
                CodeInfo(
                    code=value,
                    kind="hcpcs_level2",
                    description=row["long_description"],
                    description_note=None,
                    terminated=bool(row["termination_date"]),
                    citation=self._cite("hcpcs", code=value),
                )
            )
        # Not Level II. If CMS's own files list it, it's a CPT (Level I) code; if not, nobody
        # recognizes it, which is itself worth flagging on a bill.
        known_to: list[str] = []
        for source, table in (
            ("pfs", "pfs_rvu"),
            ("mue_outpatient_hospital", "mue_outpatient_hospital"),
            ("mue_practitioner", "mue_practitioner"),
        ):
            if self._status.get(source) == "loaded" and self._one(
                f"SELECT 1 AS hit FROM {table} WHERE code = ? LIMIT 1", [value]
            ):
                known_to.append(source)
        if known_to:
            return Ok(
                CodeInfo(
                    code=value,
                    kind="cpt",
                    description=None,
                    description_note="CPT descriptions are AMA-licensed and not included",
                    terminated=False,
                    citation=self._cite(known_to[0], code=value),
                )
            )
        return Ok(
            CodeInfo(
                code=value,
                kind="unknown",
                description=None,
                description_note="not found in any loaded CMS code list",
                terminated=False,
                citation=None,
            )
        )

    def diagnosis(self, code: str) -> Result[Diagnosis | None]:
        normalized = normalize_icd10cm(code)
        if isinstance(normalized, Err):
            return normalized
        if missing := self._require("icd10cm", "the diagnosis code"):
            return missing
        row = self._one("SELECT description FROM icd10cm WHERE code = ?", [normalized.value])
        if row is None:
            return Ok(None)
        return Ok(
            Diagnosis(
                code=normalized.value,
                description=row["description"],
                citation=self._cite("icd10cm", code=normalized.value),
            )
        )

    def mue(self, code: str, setting: MueSetting) -> Result[MueLimit | None]:
        """Most units of a code that Medicare allows per line or per day, in a setting."""
        normalized = normalize_hcpcs(code)
        if isinstance(normalized, Err):
            return normalized
        source = f"mue_{setting}"
        if missing := self._require(source, "the unit limit (MUE)"):
            return missing
        row = self._one(f"SELECT * FROM {source} WHERE code = ?", [normalized.value])
        if row is None:
            return Ok(None)
        return Ok(
            MueLimit(
                code=normalized.value,
                setting=setting,
                max_units=row["max_units"],
                adjudication_indicator=row["adjudication_indicator"],
                adjudication_label=row["adjudication_label"],
                rationale=row["rationale"],
                citation=self._cite(source, code=normalized.value),
            )
        )

    def ptp_edit(self, column1: str, column2: str) -> Result[None]:
        """Whether two codes form an NCCI pair edit (hospital outpatient).

        Needs the license-gated NCCI files; until they're loaded this always returns
        DATA_MISSING ("could not check"), never a guess.
        """
        for code in (column1, column2):
            normalized = normalize_hcpcs(code)
            if isinstance(normalized, Err):
                return normalized
        if missing := self._require("ncci_ptp_hospital", "the NCCI code pair"):
            return missing
        return err(ErrorCode.INTERNAL, "NCCI pair lookup is not implemented yet")

    def physician_rate(
        self,
        code: str,
        *,
        modifier: str = "",
        setting: FeeSetting = "nonfacility",
        locality: str | None = None,
    ) -> Result[PhysicianRate | None]:
        """Medicare physician fee-schedule amount, computed in exact decimals.

        National:  total RVU x conversion factor
        Locality:  (work RVU x work GPCI + PE RVU x PE GPCI + MP RVU x MP GPCI) x CF
        ``locality`` looks like "MA-01" (Metropolitan Boston). Rounded to the cent, half up.
        """
        normalized = normalize_hcpcs(code)
        if isinstance(normalized, Err):
            return normalized
        value = normalized.value
        mod = modifier.strip().upper()
        if missing := self._require("pfs", "the Medicare fee-schedule amount"):
            return missing
        row = self._one("SELECT * FROM pfs_rvu WHERE code = ? AND modifier = ?", [value, mod])
        if row is None:
            return Ok(None)

        pe_rvu: Decimal = row[f"{setting}_pe_rvu"]
        cf: Decimal = row["conversion_factor"]
        inputs = {
            "work_rvu": str(row["work_rvu"]),
            "pe_rvu": str(pe_rvu),
            "mp_rvu": str(row["mp_rvu"]),
            "conversion_factor": str(cf),
        }
        citation = self._cite("pfs", code=value, modifier=mod)
        if locality is None:
            locality_label = "national"
            formula = "total_rvu x conversion_factor"
            total: Decimal = row[f"{setting}_total_rvu"]
            inputs["total_rvu"] = str(total)
            raw_amount = total * cf
        else:
            match = LOCALITY_RE.match(locality.strip().upper())
            if not match:
                return err(ErrorCode.INVALID_INPUT, f"locality {locality!r} should look like MA-01")
            gpci = self._one(
                "SELECT * FROM pfs_gpci WHERE state = ? AND locality = ?",
                [match.group(1), match.group(2)],
            )
            if gpci is None:
                return err(ErrorCode.INVALID_INPUT, f"no Medicare locality {locality}")
            locality_label = f"{locality.upper()} {gpci['locality_name']}"
            formula = "(work_rvu x work_gpci + pe_rvu x pe_gpci + mp_rvu x mp_gpci) x cf"
            inputs.update(
                work_gpci=str(gpci["work_gpci"]),
                pe_gpci=str(gpci["pe_gpci"]),
                mp_gpci=str(gpci["mp_gpci"]),
            )
            raw_amount = (
                row["work_rvu"] * gpci["work_gpci"]
                + pe_rvu * gpci["pe_gpci"]
                + row["mp_rvu"] * gpci["mp_gpci"]
            ) * cf

        amount: Decimal | None = raw_amount.quantize(CENTS, rounding=ROUND_HALF_UP)
        reason = None
        if row["status_code"] not in PAYABLE_STATUS:
            amount, reason = None, f"status code {row['status_code']}: no fee-schedule amount"
        elif row[f"{setting}_na"]:
            amount, reason = None, f"not paid in the {setting} setting (NA indicator)"
        return Ok(
            PhysicianRate(
                code=value,
                modifier=mod,
                setting=setting,
                locality=locality_label,
                status_code=row["status_code"],
                amount=amount,
                reason=reason,
                formula=formula,
                inputs=inputs,
                citation=citation,
            )
        )
