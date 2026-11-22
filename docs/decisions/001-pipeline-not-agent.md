# ADR-001: A fixed pipeline, not an agent

- **Status:** accepted
- **Date:** 2026-11-22

## Context

Auditing a bill always goes through the same steps, in the same order:

1. Read the file.
2. Extract the line items.
3. Check each code against the reference data.
4. Apply the billing rules (NCCI pairs, MUE limits, duplicates, dates).
5. Compare prices with the hospital's published price file.
6. Explain the findings and draft a dispute letter.

At no point does a model need to decide which step comes next, or whether to call a tool. Agent frameworks (LangGraph, multi-agent setups, tool-calling loops) add failure points: a wrong tool choice, loops, invented tool inputs, and prompt injection steering the control flow. They're also slower and harder to debug.

## Decision

Build a plain, linear Python pipeline (`billauditor.pipeline`). Each stage is a function that returns a `Result` (`Ok` or `Err`), runs inside a trace span, and stops the run with a readable error when it fails. Later stages are then recorded as skipped.

The language model is used in only two places, each as a single direct call with a fixed output schema:

- **Extraction (Phase 4):** reading messy layouts that the PDF parser and OCR can't structure.
- **Explanation (Phase 6):** turning already-computed findings into plain language and a letter.

The model gets no tools. Every number, comparison and rule check is done in code.

## Consequences

- Each failure points to exactly one stage in the trace, so debugging is straightforward.
- Text inside a bill can't change what the pipeline does, because the model controls no step. This shrinks the prompt-injection risk to the content of the two model outputs, and Phases 4 and 6 validate both.
- Latency and cost are predictable: a fixed number of model calls per bill.
- **Trade-off:** if a future feature genuinely needs the model to choose actions (for example, asking the patient follow-up questions), it will need its own ADR. It won't be added to this pipeline quietly.
