from pathlib import Path

import duckdb
import pytest
from cms_fakes import hcpcs_text, make_free_sources, write_zip

from billauditor.reference import parsers
from billauditor.reference.build import build_reference
from billauditor.result import Err, ErrorCode, Ok
from billauditor.tracing import Tracer, read_trace


@pytest.fixture
def raw(tmp_path: Path) -> Path:
    raw_dir = tmp_path / "raw"
    make_free_sources(raw_dir)
    return raw_dir


def build(raw: Path, tmp_path: Path, run_id: str = "build"):  # type: ignore[no-untyped-def]
    tracer = Tracer(tmp_path / "runs", run_id=run_id)
    result = build_reference(raw, raw / "MANIFEST.tsv", tmp_path / "ref.duckdb", tracer)
    tracer.close("ok" if isinstance(result, Ok) else "error")
    return result, tracer.path


def test_builds_free_sources_and_reports_gated_ones_missing(raw: Path, tmp_path: Path) -> None:
    result, trace = build(raw, tmp_path)

    assert isinstance(result, Ok)
    status = {s.name: s.status for s in result.value.sources}
    assert status == {
        "hcpcs": "loaded",
        "icd10cm": "loaded",
        "mue_outpatient_hospital": "loaded",
        "mue_practitioner": "loaded",
        "pfs": "loaded",
        "ncci_ptp_hospital": "missing",
        "clfs": "missing",
        "opps_addendum_b": "missing",
    }
    gated = next(s for s in result.value.sources if s.name == "ncci_ptp_hospital")
    assert "accept the AMA license" in gated.message

    con = duckdb.connect(str(tmp_path / "ref.duckdb"), read_only=True)
    assert con.execute("SELECT count(*) FROM hcpcs_codes").fetchone() == (2,)
    assert con.execute("SELECT count(*) FROM pfs_gpci").fetchone() == (2,)  # duplicate dropped
    stages = {e["stage"] for e in read_trace(trace) if e["kind"] == "stage_end"}
    assert "reference:pfs" in stages and "reference:write_db" in stages


def test_cpt_descriptions_are_never_stored(raw: Path, tmp_path: Path) -> None:
    build(raw, tmp_path)
    con = duckdb.connect(str(tmp_path / "ref.duckdb"), read_only=True)
    columns = [r[0] for r in con.execute("DESCRIBE pfs_rvu").fetchall()]
    assert "description" not in columns
    dump = str(con.execute("SELECT * FROM pfs_rvu").fetchall())
    assert "CPT TEXT MUST NOT BE KEPT" not in dump


def test_hcpcs_continuation_lines_join_long_description(raw: Path) -> None:
    codes, modifiers = parsers.parse_hcpcs(
        raw / "cms/hcpcs/october-2026-alpha-numeric-hcpcs-file.zip"
    )
    a0428 = codes.filter(codes["code"] == "A0428").to_dicts()[0]
    assert a0428["long_description"].endswith("non-emergency transport, (bls)")
    assert modifiers.to_dicts()[0]["modifier"] == "26"


def test_changed_file_fails_build_and_keeps_old_db(raw: Path, tmp_path: Path) -> None:
    assert isinstance(build(raw, tmp_path)[0], Ok)
    before = (tmp_path / "ref.duckdb").stat().st_mtime_ns

    # Re-zip ICD with different content: same path, different checksum.
    write_zip(
        raw / "cms/icd10cm/2027-code-descriptions-tabular-order.zip",
        {"Code Descriptions/icd10cm_codes_2027.txt": "A000    Changed\r\n"},
    )
    result, _ = build(raw, tmp_path, run_id="second")
    assert isinstance(result, Err) and result.error.code is ErrorCode.CHECKSUM_MISMATCH
    assert (tmp_path / "ref.duckdb").stat().st_mtime_ns == before
    assert not (tmp_path / "ref.building").exists()


def test_unregistered_file_is_refused(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    make_free_sources(raw, register=False)
    (raw / "MANIFEST.tsv").write_text(
        "relative_path\tsha256\tbytes\tsource\tretrieved_on\n", encoding="utf-8"
    )
    result, _ = build(raw, tmp_path)
    assert isinstance(result, Err) and result.error.code is ErrorCode.DATA_MISSING
    assert "add-data" in result.error.message


def test_dental_code_fails_validation(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    make_free_sources(raw, hcpcs=hcpcs_text(extra_codes=["D0120"]))
    result, trace = build(raw, tmp_path)
    assert isinstance(result, Err) and result.error.code is ErrorCode.SCHEMA_INVALID
    assert result.error.details["table"] == "hcpcs_codes"
    failed = [e for e in read_trace(trace) if e["kind"] == "stage_end" and e["status"] == "error"]
    assert [e["stage"] for e in failed] == ["reference:hcpcs"]


def test_layout_change_is_a_parse_error(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    make_free_sources(raw, hcpcs="XXXXX00100Zbroken record\r\n")
    result, _ = build(raw, tmp_path)
    assert isinstance(result, Err) and result.error.code is ErrorCode.SCHEMA_INVALID
    assert "unknown record id" in result.error.message


def test_partial_gated_download_is_an_error(raw: Path, tmp_path: Path) -> None:
    # Only 1 of the 4 NCCI files is present: refuse rather than load a partial rule set.
    write_zip(
        raw / "cms/medicare-ncci-2026-q4-hospital-ptp-edits-ccioph-v323r0-f1.zip", {"a.txt": "x"}
    )
    result, _ = build(raw, tmp_path)
    assert isinstance(result, Err) and result.error.code is ErrorCode.DATA_MISSING
    assert "expected 4" in result.error.message
