from pathlib import Path

import pytest

from billauditor.data.verify import MANIFEST_COLUMNS, add_entry, read_manifest, verify_all
from billauditor.result import Err, ErrorCode, Ok


@pytest.fixture
def raw(tmp_path: Path) -> Path:
    raw_dir = tmp_path / "raw"
    (raw_dir / "cms").mkdir(parents=True)
    (raw_dir / "cms" / "ncci.csv").write_text("col1,col2\n99213,99214\n", encoding="utf-8")
    return raw_dir


def manifest(raw: Path) -> Path:
    return raw / "MANIFEST.tsv"


def test_add_then_verify_round_trip(raw: Path) -> None:
    added = add_entry(
        raw,
        manifest(raw),
        raw / "cms" / "ncci.csv",
        source="https://cms.gov/x",
        retrieved_on="2026-11-22",
    )
    assert isinstance(added, Ok)
    assert added.value.relative_path == "cms/ncci.csv"
    assert manifest(raw).read_text().splitlines()[0].split("\t") == list(MANIFEST_COLUMNS)

    outcome = verify_all(raw, manifest(raw))
    assert isinstance(outcome, Ok)
    assert [r.ok for r in outcome.value] == [True]


def test_changed_file_is_a_checksum_mismatch(raw: Path) -> None:
    add_entry(raw, manifest(raw), raw / "cms" / "ncci.csv", source="cms")
    (raw / "cms" / "ncci.csv").write_text("col1,col2\n99213,99215\n", encoding="utf-8")  # same size

    outcome = verify_all(raw, manifest(raw))
    assert isinstance(outcome, Ok)
    [result] = outcome.value
    assert isinstance(result, Err) and result.error.code is ErrorCode.CHECKSUM_MISMATCH
    assert "SHA-256" in result.error.message


def test_size_change_is_reported(raw: Path) -> None:
    add_entry(raw, manifest(raw), raw / "cms" / "ncci.csv", source="cms")
    (raw / "cms" / "ncci.csv").write_text("truncated", encoding="utf-8")
    [result] = verify_all(raw, manifest(raw)).value  # type: ignore[union-attr]
    assert isinstance(result, Err) and result.error.code is ErrorCode.CHECKSUM_MISMATCH


def test_missing_file_says_where_to_download_it(raw: Path) -> None:
    add_entry(raw, manifest(raw), raw / "cms" / "ncci.csv", source="https://cms.gov/ncci")
    (raw / "cms" / "ncci.csv").unlink()
    [result] = verify_all(raw, manifest(raw)).value  # type: ignore[union-attr]
    assert isinstance(result, Err) and result.error.code is ErrorCode.DATA_MISSING
    assert "https://cms.gov/ncci" in result.error.message


def test_missing_manifest(raw: Path) -> None:
    outcome = verify_all(raw, manifest(raw))
    assert isinstance(outcome, Err) and outcome.error.code is ErrorCode.DATA_MISSING


def test_wrong_header(raw: Path) -> None:
    manifest(raw).write_text("path\thash\n", encoding="utf-8")
    outcome = read_manifest(manifest(raw))
    assert isinstance(outcome, Err) and outcome.error.code is ErrorCode.SCHEMA_INVALID


def test_malformed_row(raw: Path) -> None:
    manifest(raw).write_text(
        "\t".join(MANIFEST_COLUMNS) + "\ncms/ncci.csv\tabc\tnot-a-number\tcms\t2026-11-22\n",
        encoding="utf-8",
    )
    outcome = read_manifest(manifest(raw))
    assert isinstance(outcome, Err) and outcome.error.code is ErrorCode.SCHEMA_INVALID
    assert outcome.error.details["line"] == 2


def test_path_outside_raw_dir_is_refused(raw: Path, tmp_path: Path) -> None:
    manifest(raw).write_text(
        "\t".join(MANIFEST_COLUMNS) + "\n../secret.txt\tabc\t3\tx\t2026-11-22\n", encoding="utf-8"
    )
    [result] = verify_all(raw, manifest(raw)).value  # type: ignore[union-attr]
    assert isinstance(result, Err) and result.error.code is ErrorCode.INVALID_INPUT

    outside = tmp_path / "outside.csv"
    outside.write_text("x", encoding="utf-8")
    added = add_entry(raw, manifest(raw), outside, source="x")
    assert isinstance(added, Err) and added.error.code is ErrorCode.INVALID_INPUT


def test_duplicate_and_blank_source_are_refused(raw: Path) -> None:
    file = raw / "cms" / "ncci.csv"
    assert isinstance(add_entry(raw, manifest(raw), file, source="   "), Err)
    assert isinstance(add_entry(raw, manifest(raw), file, source="cms"), Ok)
    dup = add_entry(raw, manifest(raw), file, source="cms")
    assert isinstance(dup, Err) and "already in the manifest" in dup.error.message


def test_empty_manifest_has_nothing_to_check(raw: Path) -> None:
    manifest(raw).write_text("\t".join(MANIFEST_COLUMNS) + "\n", encoding="utf-8")
    outcome = verify_all(raw, manifest(raw))
    assert isinstance(outcome, Ok) and outcome.value == []
