from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from flotilla.config import ServiceConfig
from flotilla.service import ServiceError, Workspace, create_server


def thesis_payload(
    identifier: str,
    *,
    predicate: str,
    budget_cap: float = 3,
    treatment: list[float] | None = None,
    follow_up: bool = False,
) -> dict[str, object]:
    treatment = treatment or [0.75, 0.76, 0.74]
    nodes: list[dict[str, object]] = [
        {
            "id": "falsifier",
            "kind": "falsifier",
            "cost": 1,
            "params": {
                "seed": 7,
                "control": [0.70, 0.71, 0.69],
                "treatment": treatment,
            },
        }
    ]
    if follow_up:
        nodes.append(
            {
                "id": "replicate",
                "kind": "replicate",
                "cost": 2,
                "depends_on": ["falsifier"],
                "params": {
                    "seed": 8,
                    "control": [0.69, 0.70, 0.71],
                    "treatment": [0.76, 0.75, 0.77],
                },
            }
        )
    return {
        "id": identifier,
        "title": f"Thesis {identifier}",
        "prediction": "Treatment improves a registered paired score.",
        "kill_predicate": predicate,
        "budget_cap": budget_cap,
        "decision_deadline": "2026-08-01T17:00:00Z",
        "limitations": ["Three bundled pairs only."],
        "plan": {"version": 1, "nodes": nodes},
    }


class WorkspaceTests(unittest.TestCase):
    def test_installed_journey_persists_and_reallocation_is_reversible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = Workspace(root / "flotilla.sqlite", root / "reports")
            workspace.bootstrap(6, "bootstrap")
            workspace.register(
                thesis_payload(
                    "A-KILL",
                    predicate="ci_lower < 0.01",
                    treatment=[0.70, 0.71, 0.69],
                ),
                "register-a",
            )
            workspace.register(
                thesis_payload(
                    "B-GROW",
                    predicate="ci_lower < 0.01",
                    follow_up=True,
                ),
                "register-b",
            )
            workspace.approve("A-KILL", "research-lead", "approve-a")
            workspace.approve("B-GROW", "research-lead", "approve-b")

            first = workspace.step("step-1")
            self.assertEqual(first["run"]["thesis_id"], "A-KILL")
            self.assertEqual(first["thesis"]["status"], "PENDING_KILL")
            second = workspace.step("step-2")
            self.assertEqual(second["run"]["thesis_id"], "B-GROW")
            self.assertEqual(second["run"]["node_kind"], "falsifier")
            third = workspace.step("step-3")
            self.assertEqual(third["run"]["node_kind"], "replicate")
            self.assertEqual(third["thesis"]["status"], "SURVIVED")
            self.assertEqual(third["portfolio"]["remaining"], 2.0)

            killed = workspace.confirm_kill(
                "A-KILL", "independent-reviewer", "kill-a"
            )
            self.assertEqual(killed["status"], "KILLED")
            self.assertTrue((root / "reports/A-KILL-kill.md").exists())

            transfer = workspace.reallocate(
                "A-KILL",
                "B-GROW",
                1,
                "research-lead",
                "move unused cap to the surviving thesis",
                "transfer-1",
            )
            self.assertEqual(transfer["amount"], 1.0)
            reversal = workspace.reverse_reallocation(
                transfer["id"],
                "research-lead",
                "restore the previous caps",
                "reverse-1",
            )
            self.assertEqual(reversal["reversed_from"], transfer["id"])
            with self.assertRaises(ServiceError) as already_reversed:
                workspace.reverse_reallocation(
                    transfer["id"],
                    "research-lead",
                    "duplicate reversal must fail",
                    "reverse-2",
                )
            self.assertEqual(
                already_reversed.exception.code, "REALLOCATION_ALREADY_REVERSED"
            )

            restarted = Workspace(root / "flotilla.sqlite", root / "reports")
            state = restarted.overview()
            self.assertEqual(state["remaining"], 2.0)
            self.assertEqual(state["runs"], 3)
            self.assertEqual(
                {item["id"]: item["status"] for item in state["theses"]},
                {"A-KILL": "KILLED", "B-GROW": "SURVIVED"},
            )
            request_ids = {
                event["payload"].get("request_id")
                for event in restarted.events(limit=100)
            }
            self.assertIn("reverse-1", request_ids)

    def test_unapproved_dependency_and_undetermined_are_honest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = Workspace(root / "flotilla.sqlite", root / "reports")
            workspace.bootstrap(5, "bootstrap")
            workspace.register(
                thesis_payload(
                    "T-UNKNOWN",
                    predicate="missing_score < 0.01",
                    follow_up=True,
                ),
                "register",
            )
            with self.assertRaisesRegex(ServiceError, "unapproved"):
                workspace.execute("T-UNKNOWN", "falsifier", "run-before-approval")
            workspace.approve("T-UNKNOWN", "lead", "approve")
            with self.assertRaises(ServiceError) as blocked:
                workspace.execute("T-UNKNOWN", "replicate", "run-too-early")
            self.assertEqual(blocked.exception.code, "DEPENDENCY_NOT_COMPLETED")
            result = workspace.execute(
                "T-UNKNOWN", "falsifier", "run-undetermined"
            )
            self.assertEqual(result["thesis"]["status"], "UNDETERMINED")
            self.assertEqual(
                result["thesis"]["decisions"][-1]["verdict"], "UNDETERMINED"
            )
            with self.assertRaises(ServiceError) as no_guess:
                workspace.execute("T-UNKNOWN", "replicate", "run-after-undetermined")
            self.assertEqual(no_guess.exception.code, "THESIS_NOT_EXECUTABLE")

    def test_overturn_requires_actor_reason_and_preserves_pending_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = Workspace(root / "flotilla.sqlite", root / "reports")
            workspace.bootstrap(4, "bootstrap")
            workspace.register(
                thesis_payload(
                    "T-REVIEW",
                    predicate="ci_lower < 0.01",
                    treatment=[0.70, 0.71, 0.69],
                    follow_up=True,
                ),
                "register",
            )
            workspace.approve("T-REVIEW", "lead", "approve")
            workspace.execute("T-REVIEW", "falsifier", "run")
            reviewed = workspace.overturn(
                "T-REVIEW",
                "reviewer",
                "registered follow-up can discriminate this confound",
                "overturn",
            )
            self.assertEqual(reviewed["status"], "ACTIVE")
            self.assertEqual(
                [item["verdict"] for item in reviewed["decisions"]],
                ["PENDING_KILL", "OVERTURN"],
            )
            promoted = workspace.execute("T-REVIEW", "replicate", "follow-up")
            self.assertEqual(promoted["thesis"]["status"], "SURVIVED")


class HTTPServiceTests(unittest.TestCase):
    def test_health_auth_request_id_and_body_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = ServiceConfig(
                host="127.0.0.1",
                port=0,
                ledger_path=root / "flotilla.sqlite",
                reports_dir=root / "reports",
                max_body_bytes=1_024,
                api_token="test-token-123456",
            )
            server = create_server(config, initial_budget=5)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = http.client.HTTPConnection(
                    "127.0.0.1", server.server_port, timeout=3
                )
                connection.request("GET", "/healthz")
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(json.loads(response.read()), {"status": "ok"})

                connection.request("GET", "/v1/portfolio")
                response = connection.getresponse()
                self.assertEqual(response.status, 401)
                self.assertEqual(json.loads(response.read())["error"]["code"], "UNAUTHORIZED")

                connection.request(
                    "GET",
                    "/v1/portfolio",
                    headers={
                        "Authorization": "Bearer test-token-123456",
                        "X-Request-ID": "http-test",
                    },
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.getheader("X-Request-ID"), "http-test")
                self.assertEqual(json.loads(response.read())["remaining"], 5.0)

                headers = {
                    "Authorization": "Bearer test-token-123456",
                    "Content-Type": "application/json",
                }
                registration = json.dumps(
                    thesis_payload(
                        "T-HTTP",
                        predicate="ci_lower < 0.01",
                        budget_cap=3,
                    ),
                    separators=(",", ":"),
                )
                connection.request(
                    "POST", "/v1/theses", body=registration, headers=headers
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 201)
                self.assertEqual(json.loads(response.read())["status"], "PLANNED")
                connection.request(
                    "POST",
                    "/v1/theses/T-HTTP/approve",
                    body='{"actor":"http-reviewer"}',
                    headers=headers,
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                response.read()
                connection.request(
                    "POST",
                    "/v1/portfolio/step",
                    body="{}",
                    headers=headers,
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(
                    json.loads(response.read())["run"]["thesis_id"], "T-HTTP"
                )

                oversized = json.dumps({"padding": "x" * 1_100})
                connection.request(
                    "POST",
                    "/v1/portfolio/step",
                    body=oversized,
                    headers={
                        "Authorization": "Bearer test-token-123456",
                        "Content-Type": "application/json",
                    },
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 413)
                self.assertEqual(
                    json.loads(response.read())["error"]["code"], "REQUEST_TOO_LARGE"
                )
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
