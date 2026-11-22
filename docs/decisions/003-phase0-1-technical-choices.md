# ADR-003: Technical choices in Phases 0 and 1

- **Status:** accepted
- **Date:** 2026-11-22

This record explains the choices made while building the foundations and the reference data. For each one: what was chosen, what the alternatives were and what would have happened with them, why this option fits *this* project, the trade-off, and when to revisit it. Where a choice was mostly convenience, it says so.

---

## 1. DuckDB for the reference database

**Chosen:** one embedded DuckDB file (`data/processed/reference.duckdb`), opened read-only during an audit.

| Alternative | What would happen |
|---|---|
| SQLite | Works just as well for Phase 1, where every query is a single-code lookup. It would struggle in Phase 2: scanning and aggregating multi-GB hospital price files is analytical work, which SQLite's row storage handles slowly. It also has no true decimal type, so money would be stored as text or floats. |
| PostgreSQL | Needs a running server or Docker for a single-user local tool. That's more setup and more to break, with no benefit at this scale. |
| Parquet files only | Fast and simple, but every lookup would need its own scan code, and there'd be no single place for indexes, joins and the metadata table. |
| Python dicts in memory | Fine for small tables. It fails for multi-GB price files, and nothing persists between runs. |

**Why DuckDB:** it's embedded (no server, which fits the "runs on your machine" promise). It's built for analytical queries over large tables, which is the Phase 2 workload. It reads CSV and Parquet directly. It has an exact `DECIMAL` type. And it supports read-only connections, so an audit can't modify the rules.

**Honest note:** Phase 1 alone doesn't need DuckDB. SQLite would have been fine. The choice is made for Phase 2's price files.

**Trade-off:** only one process can write at a time, so it isn't suited to a hosted, multi-user web service.
**Revisit if:** the app becomes a hosted service with concurrent users (then use Postgres, with DuckDB or Parquet for the analytical side).

## 2. Polars for parsing, pandera for validation

**Chosen:** parsers build Polars DataFrames, and each table is validated against a pandera schema before loading.

| Alternative | What would happen |
|---|---|
| pandas | Fine at these sizes (the largest table is about 75k rows). But decimals become Python objects in an `object` column, so type checking is weak and exact money handling is awkward. |
| Pydantic model per row | Checks each row well, but can't express table rules like "codes are unique" or "(code, modifier) is unique" without extra code. It's also slower on large tables. |
| Great Expectations | Powerful, but heavy configuration for a few schemas. |
| No validation | A new CMS release with a changed layout would load quietly as wrong data. That's exactly the FY2027 ICD-10 case, which the schema caught (see the worklog). |

**Why:** pandera states table-level rules in one place (patterns, uniqueness, ranges, exact decimal types), and with `lazy=True` it reports every failure at once. Polars has strict types and native decimals.

**Honest note:** Polars was also chosen to match the AML Copilot project. At Phase 1 sizes, speed wasn't the deciding factor; strict typing and decimal support were.

**Trade-off:** two libraries to learn, and DuckDB needs `pyarrow` to read Polars frames.
**Revisit if:** never likely; these tools fit the job.

## 3. Exact `Decimal` arithmetic for every dollar amount

**Chosen:** relative values, GPCIs, conversion factors and payment amounts are `Decimal` throughout: in Polars (`Decimal(12,4)`), in DuckDB (`DECIMAL`), and in Python. Rounding is to the cent, half up, done once at the end.

**Alternative, floats:** binary floating point can't represent values like 0.1 exactly. Across many line items, rounding errors add up, so a computed overcharge could be off by a cent. A dispute letter quoting "$106.11" when the correct figure is "$106.10" damages credibility, and the letter check (Phase 6) compares numbers exactly.

**Trade-off:** slightly more verbose code, and every layer must keep the decimal type (the schemas enforce this).

## 4. `Ok` / `Err` results instead of exceptions for expected failures

**Chosen:** every stage and lookup returns `Ok(value)` or `Err(error)`, with an `ErrorCode`. Real bugs still raise exceptions, but a stage boundary turns them into an `INTERNAL` error with the traceback.

| Alternative | What would happen |
|---|---|
| Exceptions everywhere | An expected situation like "NCCI data isn't loaded" could escape as a crash, or be caught too broadly and quietly ignored. That's how "could not check" ends up being reported as "no problems". |
| Return `None` on failure | Loses the reason. The report couldn't say *why* a check didn't run. |

**Why:** in this project, "we could not check this" is a result the patient needs to see, not an exceptional event. A typed `Err` is serialized into the trace and the report, and mypy forces callers to handle it (`isinstance` narrowing).

**Trade-off:** more verbose call sites, and Python doesn't enforce handling the way Rust does; strict mypy is the safety net.

## 5. Three possible outcomes for a lookup: found, checked with no entry, could not check

**Chosen:** `Ok(value)`, `Ok(None)`, and `Err(DATA_MISSING)` are kept separate.

**What would happen otherwise:** if "no MUE published for this code" and "the MUE table isn't loaded" looked the same, a bill could be reported as clean because the rules were never loaded. That's the worst failure for this tool: false reassurance.

## 6. Pinned CMS releases with a SHA-256 manifest

**Chosen:** each source is pinned to one release (Q4 2026). Every file must match `MANIFEST.tsv` (size and SHA-256) before the build uses it.

**Alternative, download the latest at build time:** CMS updates these files every quarter. Evaluation scores would change with no code change, and an improvement couldn't be told apart from a data change. Results also couldn't be reproduced later.

**Trade-off:** the data goes stale until someone updates it on purpose: download, add to the manifest, rebuild, re-run the evaluation.
**Revisit when:** Phase 5 needs rules matched to each bill's date of service (see section 10).

## 7. All-or-nothing database build

**Chosen:** build into a temporary file and rename it into place only when every source has loaded. A failed build deletes the temporary file and leaves the previous database untouched.

**Alternative, update the live database table by table:** a crash halfway would leave a database with some rule tables loaded and others missing, and audits would quietly miss errors. A full rebuild takes about 9 seconds, so there's nothing to gain from incremental updates.

## 8. Absent source versus broken source

**Chosen:** a source that is **absent** (for example, a license-gated file not downloaded yet) is recorded as "missing", and the build continues. A source that is **present but wrong** (checksum mismatch, unknown layout, failed validation) stops the whole build.

**Why the difference:** absent is a known gap. Lookups return "could not check", and the report says so. Present-but-wrong is an unknown state: loading it could mean wrong rules, and skipping it silently would hide a problem the user should fix.

Related rule: a partial download (for example, 1 of the 4 NCCI files) counts as wrong, because a partial rule set would miss pairs without anyone noticing.

## 9. Parse directly from the downloaded zip files

**Chosen:** parsers read members inside the zip; nothing is unzipped to disk.

**Why:** the checksummed file *is* the input, so there are no extracted copies that could drift from the manifest or get edited by hand.
**Trade-off:** slightly slower parsing, which doesn't matter at these sizes.

## 10. One reference release, Q4 2026

**Chosen:** the October 2026 / Q4 2026 files, which are in effect on the project's reference date. The January 2027 HCPCS file was already published, but it isn't in effect yet.

**What a real audit needs:** NCCI edits and fee schedules change every quarter, so the correct rule for a line depends on its **date of service**. With one release, a bill from an earlier quarter could be checked against rules that weren't in effect yet.

**v1 decision:** the synthetic test bills will be dated inside Q4 2026, so one release is correct for the evaluation. The report will state which release was used.
**Revisit in Phase 5:** either load several releases keyed by effective date, or flag lines whose date of service falls outside the loaded release.

## 11. Which Medicare payment schedule to compare against

**Chosen:** load the Physician Fee Schedule (PFS) now, and the OPPS (hospital outpatient) and lab schedules when the licensed files arrive.

**Why it matters:** a hospital bill contains different kinds of charges. Comparing them with the wrong schedule would be a basic mistake.

| Charge type | Correct Medicare benchmark |
|---|---|
| Hospital facility charge (outpatient department, ED room, imaging technical part) | OPPS Addendum B |
| Professional component (the physician's work, for example a radiologist reading) | PFS, facility rate |
| Lab tests (for example, 80053) | Clinical Lab Fee Schedule (the PFS gives these status "X": no amount) |

The audit engine (Phase 5) must pick the schedule by charge type. It must never compare a hospital facility charge with a physician rate.

**Also:** Medicare rates are a reference point, not the price a self-pay patient owes. For self-pay bills, the primary comparison is the hospital's **own published cash price** (Phase 2). Medicare amounts give context ("about N times the Medicare rate"); they are not evidence of an error on their own.

## 12. Locality-adjusted rates (Metro Boston)

**Chosen:** compute rates with the GPCIs for the locality where care was given (MA-01, Metro Boston), with the national rate available too.

**Alternative, national rates only:** for 99213, the national rate is $95.19 and the Metro Boston rate is $106.10, mostly because of Boston's higher practice-expense index (1.194). National rates would understate the local Medicare amount and make charges look more inflated than they are.

## 13. JSON-lines trace files instead of a tracing service

**Chosen:** one append-only `.jsonl` file per run under `runs/`, flushed after every event.

| Alternative | What would happen |
|---|---|
| LangSmith or a similar hosted service | Bill contents (patient information) would leave the machine, it may cost money, and it needs an account. All three conflict with the project's rules. |
| OpenTelemetry plus a collector | The right choice for distributed services, but heavy setup for one local process. |
| Plain `logging` | Unstructured text that can't easily be loaded into the evaluation. |

**Why:** it's free and local, survives a crash (each line is flushed), is easy to search, and loads straight into the evaluation and latency reports.
**Trade-off:** no UI yet; Phase 7 adds a trace viewer.
**Revisit if:** the app is hosted, in which case use OpenTelemetry with a self-hosted backend.

## 14. Tooling: Python 3.12, uv, strict mypy, ruff

- **Python 3.12:** the newer generics syntax (`class Ok[T]`, `type Result[T] = ...`) keeps the result types readable. AML Copilot uses 3.11; nothing here depends on 3.12 beyond that syntax.
- **uv:** a lockfile (`uv.lock`) gives identical installs locally and in CI (`uv sync --locked`), and it's fast.
- **Strict mypy:** the result types only protect anything if every `Err` is handled; strict mode catches missed narrowing.
- **ruff:** one fast tool for linting and formatting.

**Honest note:** these are mainstream defaults for a modern Python project, chosen for reproducibility and type safety rather than after a long comparison.
