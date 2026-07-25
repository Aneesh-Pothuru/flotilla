from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ProductSiteContractTests(unittest.TestCase):
    def test_landing_page_has_complete_product_story(self) -> None:
        page = (ROOT / "docs/index.html").read_text(encoding="utf-8")
        for required in (
            'id="problem"',
            'id="proof"',
            'id="system"',
            'id="about"',
            "Capital should follow disproof.",
            'href="demo/"',
            "assets/site.css",
        ):
            with self.subTest(required=required):
                self.assertIn(required, page)

    def test_demo_exposes_required_interactive_controls(self) -> None:
        page = (ROOT / "docs/demo/index.html").read_text(encoding="utf-8")
        for required in (
            "scenario-select",
            "thesis-select",
            "budget-control",
            "launch-button",
            "step-button",
            "kill-button",
            "challenge-button",
            "revive-button",
            "reallocate-button",
            "reset-button",
            "evidence-drawer",
            "interactive-lineage",
            "live-endpoint",
            "live-connect-button",
            "live-service-summary",
        ):
            with self.subTest(required=required):
                self.assertIn(required, page)

    def test_browser_simulator_is_local_first_and_covers_all_verdict_paths(self) -> None:
        script = (ROOT / "docs/assets/flotilla-demo.js").read_text(
            encoding="utf-8"
        )
        for required in (
            "KILL_CONFIRMED",
            "KILL_OVERTURNED",
            "REVIVE",
            "UNDETERMINED",
            "CAPITAL_REALLOCATED",
            "RUN_COMPLETED",
        ):
            with self.subTest(required=required):
                self.assertIn(required, script)
        self.assertIn('fetch(`${endpoint}/v1/portfolio`', script)
        self.assertIn("Live read-only workspace connected", script)
        self.assertNotIn("localStorage", script)

    def test_allocator_checks_available_capital_before_mutating_earmarks(self) -> None:
        script = (ROOT / "docs/assets/flotilla-demo.js").read_text(
            encoding="utf-8"
        )
        availability_check = "const available = thesis.earmarked + reserve();"
        earmark_mutation = "thesis.earmarked -= fromEarmark;"
        self.assertIn(availability_check, script)
        self.assertLess(
            script.index(availability_check),
            script.index(earmark_mutation),
        )

    def test_user_journeys_cover_four_roles_and_four_outcomes(self) -> None:
        journeys = (ROOT / "docs/USER_JOURNEYS.md").read_text(encoding="utf-8")
        for required in (
            "Research director",
            "Thesis owner",
            "Reviewer",
            "Operator",
            "Success state",
            "Kill state",
            "Revive state",
            "Undetermined state",
        ):
            with self.subTest(required=required):
                self.assertIn(required, journeys)


if __name__ == "__main__":
    unittest.main()
