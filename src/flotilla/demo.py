"""Deterministic five-thesis Journey 0 fixture."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .core import (
    Ledger,
    Plan,
    PlanNode,
    Portfolio,
    Thesis,
    emit_notebook_job,
    render_dashboard,
)


def load_fixture(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run_demo(
    *,
    fixture_path: str | Path = "examples/five-theses.json",
    ledger_path: str | Path = "reports/flotilla.sqlite",
    reports_dir: str | Path = "reports",
    dashboard_path: str | Path = "docs/demo/index.html",
    pages_index_path: str | Path = "docs/index.html",
    summary_json: str | Path | None = None,
) -> dict[str, Any]:
    ledger_target = Path(ledger_path)
    if ledger_target.exists():
        ledger_target.unlink()
    report_target = Path(reports_dir)
    report_target.mkdir(parents=True, exist_ok=True)
    for stale in report_target.glob("T-*-kill.md"):
        stale.unlink()

    fixture = load_fixture(fixture_path)
    with Ledger(ledger_target, dashboard_path=dashboard_path) as ledger:
        portfolio = Portfolio(
            ledger,
            float(fixture["portfolio_budget"]),
            report_target,
            code_commit=str(fixture["code_commit"]),
        )
        for item in fixture["theses"]:
            thesis = Thesis(
                id=item["id"],
                title=item["title"],
                prediction=item["prediction"],
                kill_predicate=item["kill_predicate"],
                budget_cap=float(item["budget_cap"]),
                decision_deadline=item["decision_deadline"],
                limitations=tuple(item["limitations"]),
            )
            nodes = [
                PlanNode(
                    id=node["id"],
                    kind=node["kind"],
                    cost=float(node["cost"]),
                    executor=node.get("executor", "local"),
                    params=node["params"],
                    depends_on=tuple(node.get("depends_on", [])),
                )
                for node in item["nodes"]
            ]
            plan = Plan(thesis_id=thesis.id, version=1, nodes=nodes)
            portfolio.register(thesis, plan)
            portfolio.approve_plan(thesis.id, "demo-research-lead")
        summary = portfolio.run(confirmer="demo-operator")
        notebook_node = next(
            node
            for plan in portfolio.plans.values()
            for node in plan.nodes
            if node.kind == "falsifier"
        )
        emit_notebook_job(notebook_node, report_target / "notebook-job.ipynb")
        render_dashboard(ledger, dashboard_path)

    pages_target = Path(pages_index_path)
    pages_target.parent.mkdir(parents=True, exist_ok=True)
    if not pages_target.exists():
        pages_target.write_text(
            """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="0;url=demo/">
<title>FLOTILLA demo</title></head>
<body><p><a href="demo/">Open the FLOTILLA Journey 0 dashboard</a>.</p></body>
</html>
""",
            encoding="utf-8",
        )
    if summary_json is not None:
        summary_target = Path(summary_json)
        summary_target.parent.mkdir(parents=True, exist_ok=True)
        summary_target.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return summary
