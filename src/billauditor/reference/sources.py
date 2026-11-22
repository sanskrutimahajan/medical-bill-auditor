"""Which CMS files make up the reference data, and which release of each.

Each source is pinned to one release (the Q4 2026 / October 2026 files that are in effect on
the project's reference date). A file has to be both present in ``data/raw`` and listed with a
matching checksum in ``MANIFEST.tsv`` before the build will use it.

``license_gated`` sources sit behind the AMA CPT license on cms.gov. The user accepts that
license and downloads them; the project never accepts it on their behalf or downloads around it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    name: str
    title: str
    version: str
    pattern: str  # case-insensitive glob, relative to data/raw
    expected_files: int
    page: str  # where to download it
    license_gated: bool = False


SOURCES: tuple[Source, ...] = (
    Source(
        name="hcpcs",
        title="HCPCS Level II codes (alpha-numeric)",
        version="2026-10",
        pattern="cms/hcpcs/*alpha-numeric-hcpcs-file.zip",
        expected_files=1,
        page="https://www.cms.gov/medicare/coding-billing/healthcare-common-procedure-system/quarterly-update",
    ),
    Source(
        name="icd10cm",
        title="ICD-10-CM diagnosis codes",
        version="FY2027",
        pattern="cms/icd10cm/*code-descriptions-tabular-order.zip",
        expected_files=1,
        page="https://www.cms.gov/medicare/coding-billing/icd-10-codes",
    ),
    Source(
        name="mue_outpatient_hospital",
        title="NCCI MUEs, outpatient hospital services",
        version="2026Q4",
        pattern="cms/mue/*facility-outpatient-hospital-services-mue-table.zip",
        expected_files=1,
        page="https://www.cms.gov/medicare/coding-billing/national-correct-coding-initiative-ncci-edits/medicare-ncci-medically-unlikely-edits",
    ),
    Source(
        name="mue_practitioner",
        title="NCCI MUEs, practitioner services",
        version="2026Q4",
        pattern="cms/mue/*practitioner-services-mue-table.zip",
        expected_files=1,
        page="https://www.cms.gov/medicare/coding-billing/national-correct-coding-initiative-ncci-edits/medicare-ncci-medically-unlikely-edits",
    ),
    Source(
        name="pfs",
        title="Medicare Physician Fee Schedule relative values and GPCIs",
        version="RVU26D",
        pattern="cms/pfs/rvu26d*.zip",
        expected_files=1,
        page="https://www.cms.gov/medicare/payment/fee-schedules/physician/pfs-relative-value-files/rvu26d",
    ),
    Source(
        name="ncci_ptp_hospital",
        title="NCCI procedure-to-procedure edits, hospital outpatient",
        version="2026Q4 v32.3",
        pattern="cms/*hospital-ptp-edits-ccioph-v323r0-f?.zip",
        expected_files=4,
        page="https://www.cms.gov/medicare/coding-billing/national-correct-coding-initiative-ncci-edits/medicare-ncci-procedure-procedure-ptp-edits",
        license_gated=True,
    ),
    Source(
        name="clfs",
        title="Clinical Laboratory Fee Schedule",
        version="26CLABQ4",
        pattern="cms/26clabq4.zip",
        expected_files=1,
        page="https://www.cms.gov/medicare/payment/fee-schedules/clinical-laboratory-fee-schedule-clfs/files/26clabq4",
        license_gated=True,
    ),
    Source(
        name="opps_addendum_b",
        title="Hospital outpatient (OPPS) Addendum B",
        version="2026-10",
        pattern="cms/october-2026-opps-addendum-b.zip",
        expected_files=1,
        page="https://www.cms.gov/medicare/payment/prospective-payment-systems/hospital-outpatient-pps/quarterly-addenda-updates/october-2026-addendum-b",
        license_gated=True,
    ),
)

SOURCES_BY_NAME = {s.name: s for s in SOURCES}
