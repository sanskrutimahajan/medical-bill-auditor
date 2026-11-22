"""Small fake CMS files in the real layouts, so reference tests run without real downloads.

Values are made up, except where a test needs real-world numbers (the 99213 RVUs and the
Boston GPCIs), which are copied from the RVU26D release.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from billauditor.data.verify import add_entry

AMA_NOTICE = (
    '"Current Procedural Terminology (CPT) codes, descriptions and other data only are '
    'copyright 2025 American Medical Association.\n\nAll rights reserved."\n'
)


def hcpcs_line(
    code: str, record_id: str, long_desc: str, short_desc: str = "", modifier: str = ""
) -> str:
    line = [" "] * 320
    head = f"{code:<5}" if code else f"   {modifier:<2}"
    line[0:5] = head
    line[5:10] = "00100"
    line[10] = record_id
    line[11:91] = f"{long_desc:<80}"[:80]
    line[91:119] = f"{short_desc:<28}"[:28]
    line[268:276] = "20020101"
    line[276:284] = "20020101"
    line[292] = "N"
    return "".join(line).rstrip()


def hcpcs_text(extra_codes: list[str] | None = None) -> str:
    lines = [
        hcpcs_line("J1100", "3", "Injection, dexamethasone sodium phosphate, 1 mg", "Dexameth"),
        hcpcs_line("A0428", "3", "Ambulance service, basic life support, non-emergency", "Bls"),
        hcpcs_line("A0428", "4", "transport, (bls)"),
        hcpcs_line("", "7", "Professional component", "Professional", modifier="26"),
    ]
    for code in extra_codes or []:
        lines.append(hcpcs_line(code, "3", "Extra code", "Extra"))
    return "\r\n".join(lines) + "\r\n"


ICD_TEXT = (
    "E119    Type 2 diabetes mellitus without complications\r\n"
    "QA00101 SCN2A-related neurodevelopmental disorder\r\n"
)


def mue_csv(rows: list[str]) -> str:
    header = (
        '"HCPCS/\nCPT Code",Outpatient Hospital Services MUE Values,'
        "MUE Adjudication Indicator,MUE Rationale\n"
    )
    return AMA_NOTICE + header + "\n".join(rows) + "\n"


MUE_ROWS = [
    "99213,2,3 Date of Service Edit: Clinical,Clinical: Data",
    "J1100,120,3 Date of Service Edit: Clinical,Clinical: Data",
    "80053,1,2 Date of Service Edit: Policy,CMS Policy",
]

PFS_HEADER = (
    "HCPCS,MOD,DESCRIPTION,CODE,PAYMENT,RVU,PE RVU,INDICATOR,PE RVU,INDICATOR,RVU,TOTAL,TOTAL,"
    "IND,DAYS,OP,OP,OP,PROC,SURG,SURG,SURG,SURG,IND,BASE,FACTOR,PROCEDURES,FLAG,INDICATOR,"
    "AMOUNT,AMOUNT,AMOUNT"
)


def pfs_row(code: str, mod: str, status: str, work: str, nf_pe: str, nf_na: str, f_pe: str,
            f_na: str, mp: str, nf_total: str, f_total: str) -> str:  # fmt: skip
    return (
        f"{code},{mod},CPT TEXT MUST NOT BE KEPT,{status},,{work},{nf_pe},{nf_na},{f_pe},{f_na},"
        f"{mp},{nf_total},{f_total},0,XXX,0.00,0.00,0.00,0,0,0,0,0,9,,33.4009,09,0,99,0,0,0"
    )


PFS_CSV = (
    ",,2026 National Physician Fee Schedule Relative Value File,,\n"
    ",,CPT codes and descriptions only are copyright 2026 American Medical Association.,,\n"
    + PFS_HEADER
    + "\n"
    + "\n".join(
        [
            pfs_row("99213", "", "A", "1.30", "1.46", "", "0.33", "", "0.09", "2.85", "1.72"),
            pfs_row("71046", "26", "A", "0.21", "0.08", "", "0.08", "", "0.01", "0.30", "0.30"),
            pfs_row("99999", "", "A", "1.00", "0.00", "NA", "0.50", "", "0.10", "0.00", "1.60"),
            pfs_row("80053", "", "X", "0.00", "0.00", "", "0.00", "", "0.00", "0.00", "0.00"),
        ]
    )
    + "\n"
)

GPCI_CSV = (
    "ADDENDUM E. FINAL CY 2026 GPCIs,,,,,,\n,,,,,,\n"
    "Medicare Administrative Contractor (MAC),State,Locality Number,Locality Name,"
    "2026 PW GPCI (with 1.0 Floor)***,2026 PE GPCI,2026 MP GPCI\n"
    "14212,MA,01,METROPOLITAN BOSTON,1.041,1.194,0.890\n"
    "14212,MA,99,REST OF MASSACHUSETTS,1.016,1.053,0.797\n"
    "14212,MA,99,REST OF MASSACHUSETTS,1.016,1.053,0.797\n"  # CMS repeats some rows
    "* footnote,,,,,,\n"
)


def write_zip(path: Path, members: dict[str, str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for name, text in members.items():
            zf.writestr(name, text.encode("cp1252"))
    return path


def make_free_sources(raw: Path, *, hcpcs: str | None = None, register: bool = True) -> None:
    """Write fake versions of the five free sources into ``raw`` and register them."""
    files = [
        write_zip(
            raw / "cms/hcpcs/october-2026-alpha-numeric-hcpcs-file.zip",
            {"HCPC2026_OCT_ANWEB_09232026.txt": hcpcs or hcpcs_text()},
        ),
        write_zip(
            raw / "cms/icd10cm/2027-code-descriptions-tabular-order.zip",
            {"Code Descriptions/icd10cm_codes_2027.txt": ICD_TEXT},
        ),
        write_zip(
            raw
            / "cms/mue/medicare-ncci-2026-q4-facility-outpatient-hospital-services-mue-table.zip",
            {"MCR_MUE_OutpatientHospitalServices.csv": mue_csv(MUE_ROWS)},
        ),
        write_zip(
            raw / "cms/mue/medicare-ncci-2026-q4-practitioner-services-mue-table.zip",
            {"MCR_MUE_PractitionerServices.csv": mue_csv(MUE_ROWS[:1])},
        ),
        write_zip(
            raw / "cms/pfs/rvu26d-updated-08-26-2026.zip",
            {"PPRRVU2026_Oct_nonQPP.csv": PFS_CSV, "GPCI2026.csv": GPCI_CSV},
        ),
    ]
    if register:
        for f in files:
            add_entry(raw, raw / "MANIFEST.tsv", f, source="test", retrieved_on="2026-11-22")
