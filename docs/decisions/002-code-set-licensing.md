# ADR-002: Code-set licensing (CPT, CDT, revenue codes)

- **Status:** accepted
- **Date:** 2026-11-22

## Context

Medical bills use several code sets, and they have different owners:

| Code set | Owner | Can the project store descriptions? |
|---|---|---|
| CPT (HCPCS Level I, 5-digit codes such as 99213) | American Medical Association | **No.** The codes and descriptions are AMA-copyrighted; CMS distributes them only under an AMA license agreement. |
| CDT (dental "D" codes) | American Dental Association | **No.** ADA-copyrighted. |
| HCPCS Level II (A0428, J1100, G0378, ...) | CMS | Yes. Public. |
| ICD-10-CM diagnosis codes | CDC / CMS | Yes. Public. |
| Revenue codes (hospital department codes such as 0450) | National Uniform Billing Committee (NUBC) | **No.** Licensed through the NUBC's UB-04 manual; there is no free official list. |
| NCCI edits, MUE values, fee-schedule numbers | CMS | Yes. CMS publishes them; files that list CPT codes carry an AMA notice. |

Some CMS downloads that contain CPT data (NCCI pair edits, the lab fee schedule, OPPS Addendum B) are behind a click-through AMA license on cms.gov.

## Decision

1. **CPT and CDT descriptions are never stored.** Parsers drop description columns at read time (for example, the PFS relative value file). Tests check that no CPT text reaches the database. Codes are kept as identifiers, which is how they appear on a bill anyway.
2. **The project never accepts a license for the user, or downloads around one.** License-gated CMS files are downloaded by the user after they accept the AMA license on cms.gov, then recorded in `MANIFEST.tsv` like any other file. Raw files are gitignored and never redistributed.
3. **The reference database stays local.** `data/processed/` is gitignored; anyone running the project builds it from their own downloads.
4. **Plain-language descriptions for CPT codes on bills** (Phase 6) will be written for the project for the specific codes in the test encounters, not copied from the AMA. Where none exists, the report shows the code and says the official description is AMA-licensed.
5. **Revenue codes:** v1 doesn't load a revenue-code table. Bills show revenue codes next to their own department text (for example, "0450 EMERGENCY ROOM"), so the bill itself provides the label. If a check needs revenue-code logic, the categories used will be documented with their source in an ADR first.
6. **The HCPCS Level II schema rejects "D" codes,** so dental codes can't enter the database by accident.

## Consequences

- The app can explain Level II codes and diagnoses directly, but CPT codes only in the project's own words. This is a real limitation and will be listed in the README.
- A fresh clone can't reproduce results without the user downloading the gated files themselves. `make verify-data` and the build's "missing" messages say exactly what to download and from where.
- If the project ever needs CPT descriptions, that requires an AMA license, which is out of scope.
