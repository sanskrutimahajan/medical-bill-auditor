# medical-bill-auditor

## Project

This project checks itemized medical bills (PDF or photo) for likely billing errors. It uses public rules (Medicare NCCI edits and fee schedules) and the hospital's own published prices. Every finding cites its source. The output is a plain-language report and a draft dispute letter.

The full plan is in [docs/PLAN.md](docs/PLAN.md).

This is a portfolio project. Correct, honest, measured results matter more than feature count. The tool helps patients ask questions; it is never presented as legal, billing or medical advice.

## Hard rules

- **One phase at a time.** Stop after each phase with a handoff summary, and continue only once the user approves.
- **Nothing billable.** The LLM is local Ollama (`qwen2.5:7b-instruct`). Never add API keys or paid services unless the user explicitly asks at that time.
- **Never commit or push** unless the user explicitly says so. Approving a plan is not consent to commit.
- **Never claim a result without running it.** The Phase 3 test split stays locked until Phase 8.
- **No AMA CPT or ADA CDT descriptions, and no NUBC revenue-code tables** (ADR-002). Codes are stored as identifiers only. Never accept a license on cms.gov for the user; the user downloads license-gated files.
- **v1 scope:** self-pay patients with itemized bills. Insurer EOBs are out of scope.

## Engineering principles (checked at every phase handoff)

The full mapping is in docs/PLAN.md. In short:

1. **Measured evaluation**, on about 300 bills with known errors, not hand-picked demos.
2. **Precise prompts and output schemas** for each LLM step. If a tool is ever added, it gets a full description: what it does, when to use it, when not to, inputs and side effects.
3. **Only two LLM jobs:** extracting messy layouts, and explaining findings. Everything else is code.
4. **Every stage returns `Result`** (`Ok` or `Err` with an `ErrorCode`). Never crash and never make up a value. Test the failure paths.
5. **No math or logic by the LLM.** Totals, comparisons, rules and dates are computed in code; the LLM only puts them into words.
6. **Trace everything** through `billauditor.tracing.Tracer`: stages, LLM calls (prompt version, tokens, latency), errors.
7. **No outward actions.** The app never sends, submits or pays anything; it writes only to its own output folders.
8. **Bill text is untrusted data.** Wrap it as data in prompts, constrain outputs to a schema, and test against injected instructions.
9. **Track tokens and p50/p95 latency.** Cache by file hash. The latency targets are in docs/PLAN.md.
10. **Reproducible results:** checksummed data in `data/raw/MANIFEST.tsv`, a fixed seed, temperature 0, and 3 runs per eval case.
11. **A pipeline, not an agent** (ADR-001).
12. **The README shows how well it works:** diagram, decisions, eval results and documented failures (kept in `docs/worklog.md`).

## Stack

- Python 3.12, `uv`, pydantic, pydantic-settings, ruff, strict mypy, pytest, GitHub Actions
- DuckDB, Polars, pandera (reference data, Phase 1)
- Later phases: pdfplumber and Tesseract, Ollama, Streamlit

## Layout

```text
src/billauditor/
  result.py      # Ok / Err / Error / ErrorCode
  tracing.py     # JSON-lines tracer (runs/<run_id>.jsonl)
  pipeline.py    # load_input -> extract -> audit -> report
  config.py      # Settings (BILLAUDITOR_* env vars)
  cli.py         # billauditor run | verify-data | add-data | ref-build | lookup
  data/verify.py # checksummed raw-data manifest
  reference/     # sources.py (pinned CMS releases), parsers.py, schemas.py (pandera),
                 # build.py (-> data/processed/reference.duckdb), lookup.py (cited lookups)
tests/
docs/            # PLAN.md, worklog.md, decisions/
data/raw/        # gitignored, except MANIFEST.tsv
data/processed/  # gitignored: reference.duckdb
runs/            # traces; gitignored (they can contain patient information)
```

## Commands

- `make install`: `uv sync`
- `make check`: lint (ruff, format check, mypy) and tests
- `make test` / `make lint` / `make format`
- `make demo` (or `make demo BILL=path.pdf`): run the pipeline and print the report and trace path
- `make verify-data`: check raw data against the manifest
- `make ref-build`: build the reference database (fails on any checksum or validation error)
- `make lookup CODE=99213 ARGS="--locality MA-01 --pair 96374"`: look up a code
- `uv run billauditor add-data data/raw/<file> --source <url>`: record a new download
