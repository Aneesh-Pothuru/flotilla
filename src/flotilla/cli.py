"""Command-line interface for the compact FLOTILLA runtime."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .core import Ledger, Portfolio
from .demo import run_demo


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="flotilla")
    commands = parser.add_subparsers(dest="command", required=True)

    demo = commands.add_parser("demo", help="run deterministic Journey 0")
    demo.add_argument("--fixture", default="examples/five-theses.json")
    demo.add_argument("--ledger", default="reports/flotilla.sqlite")
    demo.add_argument("--reports", default="reports")
    demo.add_argument("--dashboard", default="docs/demo/index.html")
    demo.add_argument("--pages-index", default="docs/index.html")
    demo.add_argument("--summary-json")

    revive = commands.add_parser("revive", help="challenge a confirmed kill")
    revive.add_argument("thesis_id")
    revive.add_argument("--ledger", default="reports/flotilla.sqlite")
    revive.add_argument("--reason", required=True)
    revive.add_argument("--actor", default="human-operator")

    commands.add_parser("clean", help="remove generated local artifacts")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "demo":
        summary = run_demo(
            fixture_path=args.fixture,
            ledger_path=args.ledger,
            reports_dir=args.reports,
            dashboard_path=args.dashboard,
            pages_index_path=args.pages_index,
            summary_json=args.summary_json,
        )
        print("FLOTILLA Journey 0 complete")
        print(json.dumps(summary, indent=2, sort_keys=True))
        print("Kill reports: reports/T-01-kill.md, reports/T-03-kill.md")
        print("Dashboard: docs/demo/index.html")
        return 0
    if args.command == "revive":
        with Ledger(args.ledger) as ledger:
            portfolio = Portfolio(ledger, 1.0, Path(args.ledger).parent)
            portfolio.revive(args.thesis_id, args.reason, args.actor)
        print(f"{args.thesis_id} revived; original kill evidence retained")
        return 0
    if args.command == "clean":
        explicit = [
            Path("reports/flotilla.sqlite"),
            Path("reports/demo-summary.json"),
            Path("reports/notebook-job.ipynb"),
        ]
        explicit.extend(Path("reports").glob("T-*-kill.md"))
        for target in explicit:
            if target.exists() and target.is_file():
                target.unlink()
        print("Removed generated FLOTILLA artifacts")
        return 0
    return 2

