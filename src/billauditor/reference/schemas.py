"""pandera schemas: every reference table is validated before it's written to DuckDB.

A table that fails validation is never loaded. The build reports which checks failed, so a
format change in a new CMS release can't slip through as bad data.
"""

from __future__ import annotations

from typing import Annotated

import pandera.polars as pa
import polars as pl

# Exact decimals (never floats) for every value that feeds a dollar amount.
Rvu = Annotated[pl.Decimal, 12, 4]
Gpci = Annotated[pl.Decimal, 6, 3]

HCPCS_CODE = r"^[A-Z0-9]{4}[A-Z0-9]$"
LEVEL2_CODE = r"^[A-CE-VX-Z][0-9]{4}$"  # Level II: a letter then 4 digits (no D: dental is ADA's)
# Stored without the dot. The second character is usually a digit, but FY2027 added "QA..."
# codes (genetic neurodevelopmental disorders), so a letter is allowed there too.
ICD10CM_CODE = r"^[A-Z][0-9A-Z][0-9A-Z]{1,5}$"
MODIFIER = r"^[A-Z0-9]{2}$"
DATE = r"^(|\d{8})$"  # YYYYMMDD or blank


class HcpcsCodes(pa.DataFrameModel):
    code: str = pa.Field(str_matches=LEVEL2_CODE, unique=True)
    long_description: str = pa.Field(str_length={"min_value": 1})
    short_description: str
    pricing_indicator: str
    coverage_code: str
    betos: str
    type_of_service: str
    added_date: str = pa.Field(str_matches=DATE)
    effective_date: str = pa.Field(str_matches=DATE)
    termination_date: str = pa.Field(str_matches=DATE)
    action_code: str

    class Config:
        strict = True


class HcpcsModifiers(pa.DataFrameModel):
    modifier: str = pa.Field(str_matches=MODIFIER, unique=True)
    long_description: str = pa.Field(str_length={"min_value": 1})
    short_description: str

    class Config:
        strict = True


class Icd10cm(pa.DataFrameModel):
    code: str = pa.Field(str_matches=ICD10CM_CODE, unique=True)
    description: str = pa.Field(str_length={"min_value": 1})

    class Config:
        strict = True


class Mue(pa.DataFrameModel):
    code: str = pa.Field(str_matches=HCPCS_CODE, unique=True)
    max_units: int = pa.Field(ge=0)
    adjudication_indicator: int = pa.Field(isin=[1, 2, 3])
    adjudication_label: str
    rationale: str

    class Config:
        strict = True


class PfsRvu(pa.DataFrameModel):
    code: str = pa.Field(str_matches=HCPCS_CODE)
    modifier: str = pa.Field(str_matches=r"^(|[A-Z0-9]{2})$")
    status_code: str = pa.Field(str_matches=r"^[A-Z]$")
    work_rvu: Rvu = pa.Field(ge=0)
    nonfacility_pe_rvu: Rvu = pa.Field(ge=0)
    nonfacility_na: bool
    facility_pe_rvu: Rvu = pa.Field(ge=0)
    facility_na: bool
    mp_rvu: Rvu = pa.Field(ge=0)
    nonfacility_total_rvu: Rvu = pa.Field(ge=0)
    facility_total_rvu: Rvu = pa.Field(ge=0)
    pctc_indicator: str
    global_days: str
    conversion_factor: Rvu = pa.Field(gt=0)

    class Config:
        strict = True
        unique = ["code", "modifier"]


class PfsGpci(pa.DataFrameModel):
    mac: str = pa.Field(str_matches=r"^\d{5}$")
    state: str = pa.Field(str_matches=r"^[A-Z]{2}$")
    locality: str = pa.Field(str_matches=r"^\d{2}$")
    locality_name: str
    work_gpci: Gpci = pa.Field(gt=0)
    pe_gpci: Gpci = pa.Field(gt=0)
    mp_gpci: Gpci = pa.Field(gt=0)

    class Config:
        strict = True
        unique = ["mac", "locality"]
