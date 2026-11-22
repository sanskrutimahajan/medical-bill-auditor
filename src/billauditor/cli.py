"""Command line: ``run``, ``verify-data``, ``add-data``, ``ref-build`` and ``lookup``.

Every command prints JSON and exits 0 on success, 1 on failure.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from billauditor.config import Settings
from billauditor.data.verify import add_entry, verify_all
from billauditor.pipeline import run_pipeline
from billauditor.reference.build import build_reference
from billauditor.reference.lookup import Citation, ReferenceData
from billauditor.result import Err, Ok, Result
from billauditor.tracing import Tracer


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, default=str))


def cmd_run(args: argparse.Namespace, settings: Settings) -> int:
    tracer = Tracer(settings.runs_dir, max_chars=settings.trace_max_chars)
    run = run_pipeline(Path(args.bill), tracer)
    payload: dict[str, Any] = {"trace": str(run.trace_path), "summary": run.summary}
    if isinstance(run.result, Ok):
        payload["report"] = run.result.value.model_dump()
        _print(payload)
        return 0
    payload["error"] = run.result.error.to_dict()
    _print(payload)
    return 1


def cmd_verify_data(args: argparse.Namespace, settings: Settings) -> int:
    outcome = verify_all(settings.raw_dir, settings.manifest_path)
    if isinstance(outcome, Err):
        _print({"ok": False, "error": outcome.error.to_dict()})
        return 1
    files = [
        {"ok": True, "relative_path": r.value.relative_path}
        if isinstance(r, Ok)
        else {"ok": False, "error": r.error.to_dict()}
        for r in outcome.value
    ]
    failed = sum(1 for f in files if not f["ok"])
    _print({"ok": failed == 0, "checked": len(files), "failed": failed, "files": files})
    return 0 if failed == 0 else 1


def cmd_add_data(args: argparse.Namespace, settings: Settings) -> int:
    outcome = add_entry(
        settings.raw_dir,
        settings.manifest_path,
        Path(args.file),
        source=args.source,
        retrieved_on=args.retrieved_on,
    )
    if isinstance(outcome, Err):
        _print({"ok": False, "error": outcome.error.to_dict()})
        return 1
    _print({"ok": True, "added": vars(outcome.value)})
    return 0


def cmd_ref_build(args: argparse.Namespace, settings: Settings) -> int:
    tracer = Tracer(settings.runs_dir, max_chars=settings.trace_max_chars)
    outcome = build_reference(
        settings.raw_dir, settings.manifest_path, settings.reference_db, tracer
    )
    summary = tracer.close("ok" if isinstance(outcome, Ok) else "error")
    payload: dict[str, Any] = {"trace": str(tracer.path), "summary": summary}
    if isinstance(outcome, Err):
        payload["error"] = outcome.error.to_dict()
        _print(payload)
        return 1
    payload["db"] = outcome.value.db_path
    payload["sources"] = [vars(s) for s in outcome.value.sources]
    payload["not_loaded"] = outcome.value.missing
    _print(payload)
    return 0


def _as_json(result: Result[Any]) -> dict[str, Any]:
    """Lookup results as JSON: the value, "none" (checked, no entry) or the error."""
    if isinstance(result, Err):
        return result.error.to_dict()
    value = result.value
    if value is None:
        return {"found": False}
    out: dict[str, Any] = {}
    for key, item in vars(value).items():
        if isinstance(item, Decimal):
            out[key] = str(item)
        elif isinstance(item, Citation):
            out[key] = item.to_dict()
        else:
            out[key] = item
    return {"found": True, **out}


def cmd_lookup(args: argparse.Namespace, settings: Settings) -> int:
    opened = ReferenceData.open(settings.reference_db)
    if isinstance(opened, Err):
        _print({"ok": False, "error": opened.error.to_dict()})
        return 1
    ref = opened.value
    try:
        payload: dict[str, Any] = {
            "code": _as_json(ref.code(args.code)),
            "mue_outpatient_hospital": _as_json(ref.mue(args.code, "outpatient_hospital")),
            "mue_practitioner": _as_json(ref.mue(args.code, "practitioner")),
            "medicare_physician_rate": _as_json(
                ref.physician_rate(
                    args.code, modifier=args.modifier, setting=args.setting, locality=args.locality
                )
            ),
        }
        if args.pair:
            payload["ncci_pair"] = _as_json(ref.ptp_edit(args.code, args.pair))
        if args.diagnosis:
            payload["diagnosis"] = _as_json(ref.diagnosis(args.diagnosis))
    finally:
        ref.close()
    _print(payload)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="billauditor", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="audit one bill (PDF or image) and write a trace")
    run.add_argument("bill", help="path to the bill")
    run.set_defaults(func=cmd_run)

    verify = sub.add_parser("verify-data", help="check raw data against data/raw/MANIFEST.tsv")
    verify.set_defaults(func=cmd_verify_data)

    add = sub.add_parser("add-data", help="record a downloaded file in the manifest")
    add.add_argument("file", help="file inside data/raw/")
    add.add_argument("--source", required=True, help="where it was downloaded from")
    add.add_argument("--retrieved-on", help="YYYY-MM-DD (default: today)")
    add.set_defaults(func=cmd_add_data)

    ref_build = sub.add_parser("ref-build", help="build data/processed/reference.duckdb")
    ref_build.set_defaults(func=cmd_ref_build)

    lookup = sub.add_parser("lookup", help="look up a procedure code in the reference data")
    lookup.add_argument("code", help="5-character procedure code, e.g. 99213 or J1100")
    lookup.add_argument("--modifier", default="", help="e.g. 26 or TC")
    lookup.add_argument("--setting", choices=["nonfacility", "facility"], default="nonfacility")
    lookup.add_argument("--locality", help="Medicare locality, e.g. MA-01 (Metro Boston)")
    lookup.add_argument("--pair", help="second code, to check for an NCCI pair edit")
    lookup.add_argument("--diagnosis", help="ICD-10-CM code to look up as well")
    lookup.set_defaults(func=cmd_lookup)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    code: int = args.func(args, Settings())
    return code


if __name__ == "__main__":
    sys.exit(main())
