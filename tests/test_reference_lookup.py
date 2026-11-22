from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from cms_fakes import make_free_sources

from billauditor.config import Settings
from billauditor.reference.build import build_reference
from billauditor.reference.lookup import ReferenceData
from billauditor.result import Err, ErrorCode, Ok
from billauditor.tracing import Tracer


@pytest.fixture(scope="module")
def ref(tmp_path_factory: pytest.TempPathFactory) -> Iterator[ReferenceData]:
    root = tmp_path_factory.mktemp("ref")
    raw = root / "raw"
    make_free_sources(raw)
    tracer = Tracer(root / "runs", run_id="build")
    assert isinstance(build_reference(raw, raw / "MANIFEST.tsv", root / "ref.duckdb", tracer), Ok)
    tracer.close("ok")
    opened = ReferenceData.open(root / "ref.duckdb")
    assert isinstance(opened, Ok)
    yield opened.value
    opened.value.close()


def test_open_without_db_says_how_to_build(tmp_path: Path) -> None:
    result = ReferenceData.open(tmp_path / "none.duckdb")
    assert isinstance(result, Err) and result.error.code is ErrorCode.DATA_MISSING
    assert "make ref-build" in result.error.message


# -- codes --------------------------------------------------------------------------------------


def test_level2_code_has_description_and_citation(ref: ReferenceData) -> None:
    result = ref.code(" j1100 ")
    assert isinstance(result, Ok)
    info = result.value
    assert info.kind == "hcpcs_level2" and info.description is not None
    assert info.citation is not None and info.citation.version == "2026-10"


def test_cpt_code_is_recognized_without_description(ref: ReferenceData) -> None:
    result = ref.code("99213")
    assert isinstance(result, Ok)
    assert result.value.kind == "cpt" and result.value.description is None
    assert "AMA-licensed" in (result.value.description_note or "")


def test_code_in_no_cms_list_is_unknown(ref: ReferenceData) -> None:
    result = ref.code("ZZZZZ")
    assert isinstance(result, Ok) and result.value.kind == "unknown"


@pytest.mark.parametrize("bad", ["9921", "992133", "99-13", ""])
def test_malformed_code_is_invalid_input(ref: ReferenceData, bad: str) -> None:
    result = ref.code(bad)
    assert isinstance(result, Err) and result.error.code is ErrorCode.INVALID_INPUT


def test_diagnosis_accepts_dotted_codes(ref: ReferenceData) -> None:
    found = ref.diagnosis("E11.9")
    assert isinstance(found, Ok) and found.value is not None
    assert found.value.description.startswith("Type 2 diabetes")
    absent = ref.diagnosis("E11.8")
    assert isinstance(absent, Ok) and absent.value is None


# -- MUEs -----------------------------------------------------------------------------------


def test_mue_found_none_and_setting_specific(ref: ReferenceData) -> None:
    hit = ref.mue("J1100", "outpatient_hospital")
    assert isinstance(hit, Ok) and hit.value is not None
    assert hit.value.max_units == 120 and hit.value.adjudication_indicator == 3
    assert hit.value.citation.key == {"code": "J1100"}

    # Checked, but this setting's table has no entry: Ok(None), not an error.
    none = ref.mue("J1100", "practitioner")
    assert isinstance(none, Ok) and none.value is None


# -- NCCI pairs -----------------------------------------------------------------------------


def test_ncci_pair_without_data_is_could_not_check(ref: ReferenceData) -> None:
    result = ref.ptp_edit("J1100", "96374")
    assert isinstance(result, Err) and result.error.code is ErrorCode.DATA_MISSING
    assert result.error.message.startswith("could not check")


def test_ncci_pair_validates_codes_first(ref: ReferenceData) -> None:
    result = ref.ptp_edit("J1100", "bad")
    assert isinstance(result, Err) and result.error.code is ErrorCode.INVALID_INPUT


# -- physician fee schedule -----------------------------------------------------------------


def test_national_rate_is_total_rvu_times_cf(ref: ReferenceData) -> None:
    result = ref.physician_rate("99213")
    assert isinstance(result, Ok) and result.value is not None
    assert result.value.amount == Decimal("95.19")  # 2.85 x 33.4009 = 95.1926
    assert result.value.locality == "national"


def test_boston_rate_uses_gpcis_in_exact_decimals(ref: ReferenceData) -> None:
    result = ref.physician_rate("99213", locality="ma-01")
    assert isinstance(result, Ok) and result.value is not None
    # (1.30 x 1.041 + 1.46 x 1.194 + 0.09 x 0.890) x 33.4009 = 106.1027... -> 106.10
    assert result.value.amount == Decimal("106.10")
    assert result.value.locality == "MA-01 METROPOLITAN BOSTON"
    assert result.value.inputs["pe_gpci"] == "1.194"


def test_facility_setting_and_modifier(ref: ReferenceData) -> None:
    result = ref.physician_rate("71046", modifier="26", setting="facility")
    assert isinstance(result, Ok) and result.value is not None
    assert result.value.amount == Decimal("10.02")  # 0.30 x 33.4009


def test_na_indicator_means_no_amount(ref: ReferenceData) -> None:
    result = ref.physician_rate("99999", setting="nonfacility")
    assert isinstance(result, Ok) and result.value is not None
    assert result.value.amount is None and "NA indicator" in (result.value.reason or "")


def test_non_payable_status_means_no_amount(ref: ReferenceData) -> None:
    result = ref.physician_rate("80053")
    assert isinstance(result, Ok) and result.value is not None
    assert result.value.amount is None and "status code X" in (result.value.reason or "")


def test_missing_rate_and_bad_locality(ref: ReferenceData) -> None:
    absent = ref.physician_rate("99213", modifier="TC")
    assert isinstance(absent, Ok) and absent.value is None
    for bad in ("Boston", "ZZ-01"):
        result = ref.physician_rate("99213", locality=bad)
        assert isinstance(result, Err) and result.error.code is ErrorCode.INVALID_INPUT


# -- the real data, when it's present locally (skipped in CI) --------------------------------

REAL_DB = Settings().reference_db


@pytest.mark.skipif(not REAL_DB.exists(), reason="real reference DB not built (make ref-build)")
def test_real_data_golden_values() -> None:
    opened = ReferenceData.open(REAL_DB)
    assert isinstance(opened, Ok)
    ref = opened.value
    try:
        rate = ref.physician_rate("99213", locality="MA-01")
        assert isinstance(rate, Ok) and rate.value is not None
        assert rate.value.amount == Decimal("106.10")
        mue = ref.mue("J1100", "outpatient_hospital")
        assert isinstance(mue, Ok) and mue.value is not None and mue.value.max_units == 120
        dx = ref.diagnosis("E11.9")
        assert isinstance(dx, Ok) and dx.value is not None
    finally:
        ref.close()
