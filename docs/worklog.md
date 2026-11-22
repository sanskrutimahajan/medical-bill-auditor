# Worklog

In-progress notes, failures and fixes, newest first. Resolved design decisions go in `docs/decisions/` as ADRs. The README's "Documented failures" section is written from the failure entries here.

Entry format:

```
## YYYY-MM-DD: Phase N: short title
- What happened / what was tried
- Failure (if any): symptom, root cause, fix, before → after metric
```

## 2026-11-22: Phase 1: Reference data (free sources done; licensed sources pending)
- Downloaded 5 free CMS files (about 11.7 MB, with the user's approval) and recorded them in `MANIFEST.tsv`: HCPCS Level II 2026-10, ICD-10-CM FY2027, MUE outpatient hospital and practitioner 2026Q4, PFS RVU26D.
- Found that NCCI pair edits, CLFS 26CLABQ4 and OPPS Addendum B sit behind the AMA CPT license on cms.gov. The user accepts the license and downloads those; they're registered as `license_gated` sources and reported as "missing" until present (ADR-002).
- Built parsers, pandera schemas, an atomic DuckDB build, and lookups with citations. Fee-schedule amounts are exact `Decimal` math with Metro Boston GPCIs.
- Golden check against the real data: 99213 in MA-01 = (1.30×1.041 + 1.46×1.194 + 0.09×0.890) × 33.4009 = $106.10, which matches a hand calculation.
- **Failure 1: ICD-10-CM validation rejected FY2027.**
  - Symptom: pandera `str_matches` failed on 18 codes.
  - Root cause: FY2027 added "QA..." codes (genetic neurodevelopmental disorders) with a letter in the second position, and the pattern assumed a digit.
  - Fix: widened the pattern after confirming the codes are legitimate.
  - Result: 0 → 74,879 codes loaded.
  - Takeaway: this is why every release is validated before loading, rather than trusted.
- **Failure 2: first database write failed.**
  - Symptom: DuckDB needs `pyarrow` to read Polars frames.
  - Behavior: the build returned a clean `internal` error, as designed, but left a `reference.building` temp file behind.
  - Fix: added `pyarrow`, and the build now deletes its temp file on failure. A test covers "a failed build keeps the old database and leaves no temp file".
- **Failure 3: `lookup ZZZZZ` called a made-up code "cpt_or_other".**
  - Fix: a code is now `cpt` only if CMS's own files (PFS or MUE) list it; otherwise it's `unknown`, which is itself worth flagging on a bill.
- Build time: about 8.6 s for 5 sources (ICD and HCPCS parsing dominate). DB size: 6.3 MB.

## 2026-11-22: Phase 0: Foundations
- Set up the Python 3.12 package `billauditor` (uv, ruff, strict mypy, pytest) and GitHub Actions CI.
- Added the `Result` / `Error` types: every stage returns `Ok` or `Err` with a stable `ErrorCode`, and bugs inside a stage become `INTERNAL` errors with a traceback instead of crashing the run.
- Added the JSON-lines tracer: one file per run under `runs/`, with stage spans and latencies, LLM call records (tokens, latency, prompt version), skipped stages and a run summary. Events are flushed as they're written.
- Added the placeholder pipeline (`load_input` → `extract` → `audit` → `report`). `load_input` is real: it checks for a missing file, a directory, an unsupported type, an empty file, oversize files and a fake PDF, and computes a SHA-256.
- Added `verify-data` / `add-data` with a checksummed `data/raw/MANIFEST.tsv`.
- No failures to record yet.
