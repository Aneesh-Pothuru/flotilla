"""Command-line interface for the compact FLOTILLA runtime."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

from .config import ServiceConfig
from .core import Ledger, Portfolio
from .demo import run_demo
from .service import Workspace, serve


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

    init = commands.add_parser(
        "init", help="initialize a persistent installed-service portfolio"
    )
    init.add_argument("--ledger", default="data/flotilla.sqlite")
    init.add_argument("--reports", default="reports")
    init.add_argument("--budget", type=float, required=True)

    status = commands.add_parser(
        "status", help="print installed-service portfolio state"
    )
    status.add_argument("--ledger", default="data/flotilla.sqlite")
    status.add_argument("--reports", default="reports")

    service = commands.add_parser(
        "serve", help="run the persistent local HTTP control plane"
    )
    service.add_argument("--host")
    service.add_argument("--port", type=int)
    service.add_argument("--ledger")
    service.add_argument("--reports")
    service.add_argument(
        "--budget",
        type=float,
        help="initialize an empty portfolio idempotently at startup",
    )

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
    if args.command == "init":
        workspace = Workspace(args.ledger, args.reports)
        state = workspace.bootstrap(args.budget, "cli-init")
        print(json.dumps(state, indent=2, sort_keys=True))
        return 0
    if args.command == "status":
        workspace = Workspace(args.ledger, args.reports)
        print(json.dumps(workspace.overview(), indent=2, sort_keys=True))
        return 0
    if args.command == "serve":
        config = ServiceConfig.from_env()
        changes = {}
        if args.host is not None:
            changes["host"] = args.host
        if args.port is not None:
            changes["port"] = args.port
        if args.ledger is not None:
            changes["ledger_path"] = Path(args.ledger)
        if args.reports is not None:
            changes["reports_dir"] = Path(args.reports)
        config = replace(config, **changes)
        config.validate()
        serve(config, initial_budget=args.budget)
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
