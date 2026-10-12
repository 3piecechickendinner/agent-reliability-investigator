"""Small local CLI. Generated reports always come from real fixture executions."""

import argparse
import json
from pathlib import Path
import sys

from . import __version__
from .models import canonical_scenario
from .regression import check_regression, export_regression
from .runner import render_replay, render_report, replay_report, run_scenario


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _common(parser) -> None:
    parser.add_argument("--output-dir", type=Path, default=Path("runs"))
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Investigate local scripted completion claims; no model calls.")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("cases", help="List the six canonical fixtures and expected acceptance results")
    run = commands.add_parser("run", help="Execute one scripted fixture with fresh mailbox state")
    run.add_argument("--case", choices=list("ABCDEF"), required=True)
    run.add_argument("--corrected", action="store_true", help="Select the reviewed honest-response B fixture")
    _common(run)
    replay = commands.add_parser("replay", help="Execute a saved scenario again using fresh state")
    replay.add_argument("report", type=Path)
    replay.add_argument("--attempts", type=int, default=1, help="Fresh attempts (1–5)")
    replay.add_argument("--corrected", action="store_true")
    _common(replay)
    export = commands.add_parser("export", help="Export a confirmed false completion through a fixed regression template")
    export.add_argument("report", type=Path)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--format", choices=["markdown", "json"], default="markdown")
    check = commands.add_parser("check", help="Execute a JSON regression scenario and its fixed assertions")
    check.add_argument("regression", type=Path)
    check.add_argument("--corrected", action="store_true")
    _common(check)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "cases":
        print("Canonical scripted fixtures — expected acceptance results, not benchmark measurements.\n")
        print("| Case | Scenario | Claim verification | Task outcome |\n|---|---|---|---|")
        for row in [
            ("A", "Ordinary success", "PASS", "ACHIEVED"),
            ("B", "False completion after rejection", "FAIL", "NOT_ACHIEVED"),
            ("C", "Honest failure", "PASS", "NOT_ACHIEVED"),
            ("D", "Lost acknowledgement after commit", "PASS", "ACHIEVED"),
            ("E", "Unavailable independent evidence", "UNVERIFIED", "UNKNOWN"),
            ("F", "Wrong destination", "FAIL", "NOT_ACHIEVED"),
        ]:
            print("| " + " | ".join(row) + " |")
        return 0
    try:
        if args.command == "run":
            result = run_scenario(canonical_scenario(args.case, corrected=args.corrected), args.output_dir)
            rendered = render_report(result)
        elif args.command == "replay":
            result = replay_report(_read_json(args.report), args.output_dir, args.attempts, args.corrected)
            rendered = render_replay(result)
        elif args.command == "export":
            result = export_regression(_read_json(args.report), args.output)
            rendered = (
                f"Exported reviewed template {result['template']} to {args.output}.\n"
                "Assertions require PASS claim verification and NOT_ACHIEVED task outcome.\n"
                "Check against the broken target should fail; --corrected selects the reviewed B fixture.\n"
            )
        else:
            result = check_regression(args.regression, args.output_dir, args.corrected)
            rendered = f"Regression assertions: {result['status']}\n\n" + render_report(result["report"])
        if args.format == "json":
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(rendered, end="" if rendered.endswith("\n") else "\n")
        return 1 if args.command == "check" and result["status"] == "FAIL" else 0
    except (ValueError, TypeError, KeyError, OSError) as error:
        print(f"ari: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
