from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from flotilla.core import Ledger, Plan, PlanNode, Portfolio, Thesis
from flotilla.demo import run_demo


def simple_thesis(identifier: str = "T-X") -> tuple[Thesis, Plan]:
    thesis = Thesis(
        id=identifier,
        title="Simple",
        prediction="Treatment improves by one point.",
        kill_predicate="ci_lower < 0.01",
        budget_cap=3,
        decision_deadline="2026-07-25T17:00:00Z",
        limitations=("fixture only",),
    )
    node = PlanNode(
        id="falsifier",
        kind="falsifier",
        cost=1,
        params={"control": [0.7, 0.7], "treatment": [0.7, 0.7]},
    )
    return thesis, Plan(thesis_id=identifier, version=1, nodes=[node])


class PortfolioTests(unittest.TestCase):
    def test_unapproved_plan_never_runs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with Ledger(Path(directory) / "ledger.sqlite") as ledger:
                portfolio = Portfolio(ledger, 4, Path(directory) / "reports")
                thesis, plan = simple_thesis()
                portfolio.register(thesis, plan)
                with self.assertRaisesRegex(RuntimeError, "unapproved"):
                    portfolio.run(confirmer="operator")
                self.assertEqual(ledger.rows("runs"), [])

    def test_pending_kill_requires_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with Ledger(Path(directory) / "ledger.sqlite") as ledger:
                portfolio = Portfolio(ledger, 4, Path(directory) / "reports")
                thesis, plan = simple_thesis()
                portfolio.register(thesis, plan)
                portfolio.approve_plan(thesis.id, "lead")
                summary = portfolio.run()
                self.assertEqual(summary["statuses"], {"PENDING_KILL": 1})
                self.assertFalse((Path(directory) / "reports/T-X-kill.md").exists())

    def test_demo_lineage_kills_reports_notebook_and_dashboard(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary = run_demo(
                fixture_path=Path(__file__).parents[1] / "examples/five-theses.json",
                ledger_path=root / "ledger.sqlite",
                reports_dir=root / "reports",
                dashboard_path=root / "docs/demo/index.html",
                pages_index_path=root / "docs/index.html",
                summary_json=root / "summary.json",
            )
            self.assertEqual(summary["statuses"], {"KILLED": 2, "SURVIVED": 3})
            self.assertEqual(summary["runs"], 8)
            self.assertEqual(summary["spent"], 11.0)
            self.assertEqual(summary["undetermined_decisions"], 0)
            self.assertTrue((root / "reports/T-01-kill.md").exists())
            self.assertTrue((root / "reports/T-03-kill.md").exists())
            self.assertTrue((root / "reports/notebook-job.ipynb").exists())
            self.assertIn(
                "Five theses. One budget.",
                (root / "docs/demo/index.html").read_text(encoding="utf-8"),
            )
            self.assertEqual(json.loads((root / "summary.json").read_text()), summary)

    def test_kill_is_reversible_and_evidence_is_retained(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with Ledger(Path(directory) / "ledger.sqlite") as ledger:
                portfolio = Portfolio(ledger, 4, Path(directory) / "reports")
                thesis, plan = simple_thesis()
                portfolio.register(thesis, plan)
                portfolio.approve_plan(thesis.id, "lead")
                portfolio.run(confirmer="lead")
                portfolio.revive(thesis.id, "new registered arm", "lead")
                self.assertEqual(ledger.thesis_status(thesis.id), "REVIVED")
                verdicts = [row["verdict"] for row in ledger.rows("decisions")]
                self.assertIn("KILL", verdicts)
                self.assertIn("REVIVE", verdicts)


if __name__ == "__main__":
    unittest.main()

