# medical-bill-auditor: Build Plan (draft v0.2)

**Goal:** take an itemized medical bill (PDF or photo), turn it into structured line items, and flag likely billing errors using public rules (Medicare NCCI edits and fee schedules) and the hospital's own published prices. Every flag cites its source. The output is a plain-language report plus a dispute letter the patient can send.

**Framing:** this tool helps patients ask questions about their bill. It is not legal, billing or medical advice. Findings are labeled either *likely error* or *worth asking about*.

## Working rules

- Build one phase at a time. Stop after each phase with a handoff summary, and only continue once it's approved.
- No paid services. LLM steps use a local model through Ollama. Any paid option has to be explicitly approved first.
- Nothing is committed or pushed unless the user explicitly asks.
- Results are never claimed without running them. The test split stays locked until the final evaluation.
- No copyrighted AMA CPT descriptions are shipped. Codes are stored as identifiers; descriptions come from free sources (HCPCS Level II, CMS files) or are written for the project.

## Engineering principles

These are the user's 12 rules for AI projects, applied to this project. Each one names where it is enforced. They are a checklist for every phase's handoff.

| # | Rule | How it applies here | Enforced in |
|---|---|---|---|
| 1 | Real evaluation, not 3–4 hand-picked demos | About 300 bills with planted errors and saved correct answers. Metrics for each stage: extraction accuracy per field, precision and recall per error type, false-flag rate, unsupported-claim rate in letters. Every phase ends with a measured score. | Phase 3 builds the set; Phases 4–6 measure; Phase 8 runs the locked test set |
| 2 | Clear tool descriptions | There are no agent tools, because the code calls each LLM step directly. Each step instead gets a specific prompt and an output schema (Pydantic) that spells out the job, the allowed inputs, what to do when unsure, and the exact output format. If tools are ever added, each gets a description of what it does, when to use it, when not to, its inputs and its side effects. | Phases 4 and 6 |
| 3 | Few focused tools | The LLM does only two jobs: (a) read messy bill layouts into line items, and (b) write plain-language explanations and the letter. Everything else is ordinary code. | Architecture |
| 4 | Error handling | Every stage returns either a result or a typed error (`ok: false`, `error_code`, `message`), never an exception that crashes the run and never a made-up value. Bad model output is retried once, then marked "needs review." Missing reference data or price files produce a clear "could not check" note, not a silent pass. Tests cover each failure path. | All phases; failure-path tests from Phase 1 on |
| 5 | No math or logic by the LLM | Totals, unit counts, price comparisons, rule checks, savings estimates and dates are all computed in code. The LLM only puts already-computed facts into words. The letter check confirms every number in it matches a computed value. | Phases 5 and 6 |
| 6 | Traces and logging | A structured trace for each run: every stage, LLM call (prompt version, input, output, tokens, latency), rule hit and error, saved as JSON lines under one run ID. A trace viewer page in the app. | Tracing is built in Phase 0 and used everywhere |
| 7 | Safety on risky actions | The tool never sends, submits or pays anything. It only produces a report and a draft letter that the patient sends themselves. It writes files only to its own output folder, makes no outside network calls during an audit, and has no write access to the reference data. | Architecture; Phase 7 |
| 8 | Prompt injection | Bills are untrusted documents: a PDF could carry hidden text like "ignore previous instructions." Bill text is passed to the LLM as clearly marked data. The LLM's output is limited to a fixed schema and has no tools. Findings come from code, so text can't create or remove a flag. The letter check rejects claims that don't match a finding. The test set includes bills with injected instructions, with an attack-success-rate metric. | Phases 3, 4 and 6 |
| 9 | Cost and latency | Track tokens and latency for every LLM call, and p50/p95 latency for each bill and each stage. Cost is $0 because the model runs locally, but token counts are reported anyway. Cache reference-data lookups and extraction results by file hash. Latency targets are in the section below. | Phase 0 tracing; reported in Phases 4–8 |
| 10 | Reproducible results | Pinned snapshots of the reference data and price files with checksums. Test bills generated from a fixed seed and frozen. LLM temperature 0 and a pinned model version. Each eval case runs 3 times, reporting the mean and spread. No live data during evals. | Phases 1–3 freeze; the eval runner repeats cases |
| 11 | A pipeline, not an agent | The steps are always the same (read, check codes, apply rules, compare prices, explain, write letter), so this is a plain linear pipeline. No agent framework and no multi-agent setup. This decision is recorded in an ADR. | Architecture (ADR-001) |
| 12 | README shows how well it works, not just what it does | Architecture diagram (SVG), design decisions with links to ADRs, a results table and charts, a documented-failures section (what broke, why, how it was fixed, before and after scores), cost and latency numbers, and known limitations. | Phase 8; failures logged in `docs/worklog.md` as they happen |

## Latency targets (provisional)

These are set from estimates and get confirmed or adjusted from measured Phase 4 data, recorded in an ADR.

| Target | p95 | Why |
|---|---|---|
| Findings shown, digital PDF | ≤ 5 s | No LLM involved: text extraction, rules and price lookups only |
| Findings shown, scanned or messy bill | ≤ 60 s | Includes OCR and LLM extraction; the app shows live progress for each stage |
| Letter fully written, after findings appear | ≤ 45 s | The letter appears word by word while the patient reads the findings |

**How it's measured:** on the M2 (24 GB), with the model already loaded (cold-start load time reported separately), on bills of 1–3 pages and up to 40 line items, one bill at a time, each case run 3 times. p50 and p95 are reported per stage from the traces.

**If a target is missed, try in order:** cache by file hash, send the LLM only the parts the parser couldn't read, use a shorter JSON format, process pages in parallel, and as a last resort use a smaller model with the accuracy cost measured. Each attempt is logged in `docs/worklog.md`.

## Phases

### Phase 0: Foundations
- Python project (`pyproject.toml`, uv), package `billauditor`, ruff and pytest, GitHub Actions CI.
- `CLAUDE.md` with the working rules and the engineering principles, `docs/decisions/` with ADR-001 (pipeline, not agent), `docs/worklog.md`.
- The shared `Result` / `Error` types (rule 4) and the trace logger (rules 6 and 9).
- `data/raw/` (gitignored) and a `verify_data` script that checks downloads against checksums.
- **Done when:** CI passes, and a placeholder pipeline run writes a trace file with run ID, stage timings and an error example.

### Phase 1: Reference data (in progress)
- Load the CMS datasets into DuckDB. Every file is pinned to the October 2026 / Q4 2026 release and checksummed in `MANIFEST.tsv`.
  - **Free downloads (loaded):** HCPCS Level II (2026-10), ICD-10-CM (FY2027), NCCI MUEs for outpatient hospital and practitioner (2026Q4), Physician Fee Schedule RVU26D with GPCIs (for Metro Boston locality pricing)
  - **Behind the AMA license, so the user downloads them:** NCCI pair edits for hospital outpatient (2026Q4 v32.3, 4 files), Clinical Lab Fee Schedule (26CLABQ4), OPPS Addendum B (2026-10)
  - **Not loaded:** revenue codes (NUBC-licensed) and practitioner NCCI pair edits (out of v1 scope). See ADR-002.
- Validate every table with pandera, and build the database atomically.
- Lookups return results with citations, and distinguish "found", "checked, no entry" and "could not check".
- **Done when:** one query can answer "is code pair A+B an NCCI edit, what's the MUE for X, and what does Medicare pay for X?", and the error-path tests pass (unknown code, missing table).

### Phase 2: Hospital price-transparency data
- Pick 3–5 hospitals. Download their machine-readable price files (CMS v2 JSON/CSV) once, then freeze them with checksums. Parse them piece by piece, without loading whole multi-GB files into memory.
- Normalize them into one price table: hospital, code, description, gross charge, cash price, payer, negotiated rate, min and max.
- Produce a data-quality report per hospital (missing fields, schema drift).
- First finding for the README: how much prices for the same service vary across payers and hospitals. All numbers are computed in code.
- **Done when:** price lookups work for the chosen hospitals and the data-quality report has been generated.

### Phase 3: Test bills with planted errors
- Build realistic encounters (ER visit, imaging, outpatient surgery, lab panel, maternity) from real code combinations, priced from Phase 2 data.
- Plant errors from a list of types:
  - duplicate charges
  - unbundling (NCCI pair edits)
  - units above the MUE limit
  - charges above the hospital's published price
  - mismatched E/M visit level
  - service dates outside the stay
  - totals above the Good Faith Estimate (self-pay patients)
- Include clean bills, so the false-flag rate can be measured.
- Include adversarial bills with hidden or visible injected instructions (rule 8).
- Render each bill to PDF in several layouts, plus "scanned" versions with noise. Save a ground-truth JSON for every bill.
- Generate everything from a fixed seed. Lock the dev/test split.
- **Done when:** there are about 300 bills with ground truth, test-set bills are hashed and frozen, and there is a report on how the cases are spread across error types.

### Phase 4: Extraction
- Turn a PDF or image into validated `LineItem` records (Pydantic).
  - pdfplumber for digital PDFs
  - OCR (Tesseract or ocrmypdf) for scans
  - the local LLM only for messy layouts, with a constrained output schema and code checks against Phase 1 tables
- Invalid output is retried once, then marked "needs review."
- **Metrics:** field-level accuracy (code, units, amount, date), schema-failure rate, injection-success rate, p50/p95 latency, tokens per bill. Each case runs 3 times.
- Confirm or adjust the latency targets from the measured baseline, and record the result in an ADR.

### Phase 5: Audit engine
- Use fixed rules only, with no LLM in this phase. Every finding carries a citation: the NCCI edit row, the MUE value, or the price-file entry.
- Label each finding: *likely error* or *worth asking about*.
- When reference data is missing, report "could not check" rather than passing silently.
- **Metrics:** precision and recall for each error type, plus the false-flag rate on clean bills.

### Phase 6: Explanation and dispute letter
- The LLM writes the report and the letter only from the computed findings.
- A groundedness check confirms that every claim and every number maps to a finding or a computed value. Anything that fails is removed or sent back for a rewrite.
- Add the No Surprises Act Good Faith Estimate dispute path for self-pay patients.
- **Metrics:** unsupported-claim rate, number-mismatch rate, injection-success rate, reading grade level, latency.

### Phase 7: App
- Upload, then report, then a downloadable dispute letter, plus a trace viewer.
- Redact personal details, make no outside network calls during an audit, store nothing by default, and show clear disclaimers.
- The app never sends anything on its own; the patient decides what to do with the letter.

### Phase 8: Final evaluation and release
- Run the locked test set once, with 3 repeats.
- Write a results table and charts, cost and latency figures, and a documented-failures section.
- Write a README (approved style) and an SVG architecture diagram. Record a demo video, and deploy with sample bills only. Make the repo public.

## Decisions (approved 2026-11-22)
1. **Region:** Boston-area hospitals. The 3–5 specific hospitals are picked in Phase 2, based on the quality of their published price files.
2. **App stack:** Streamlit.
3. **v1 scope:** self-pay patients with itemized bills only. Insurer EOBs are out of scope for v1.
4. **Local model:** qwen2.5:7b-instruct through Ollama (already installed). No paid APIs.
5. **Latency targets:** the provisional table above, confirmed in Phase 4.
