"""Persistent local HTTP control plane for FLOTILLA.

The hosted product demo deliberately replays fixture data. This module is the
installed product path: requests execute the real safe predicate, scheduler,
budget, evidence, and SQLite lineage implementation.
"""

from __future__ import annotations

import hmac
import json
import math
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qs, unquote, urlsplit

from .config import ServiceConfig
from .core import Ledger, LocalExecutor, PlanNode, Thesis
from .predicate import PredicateError, PredicateUndetermined, SafePredicate


IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,63}$")
RUN_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,191}$")
ACTOR_MAX_LENGTH = 128
TEXT_MAX_LENGTH = 4_096


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class ServiceError(Exception):
    """An expected client-visible error with an HTTP status and stable code."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details


def _required_text(
    value: Any, field: str, *, maximum: int = TEXT_MAX_LENGTH
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ServiceError(422, "INVALID_INPUT", f"{field} must be non-empty text")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise ServiceError(
            422, "INVALID_INPUT", f"{field} exceeds {maximum} characters"
        )
    return normalized


def _identifier(value: Any, field: str) -> str:
    identifier = _required_text(value, field, maximum=64)
    if not IDENTIFIER.fullmatch(identifier):
        raise ServiceError(
            422,
            "INVALID_INPUT",
            f"{field} must match {IDENTIFIER.pattern}",
        )
    return identifier


def _run_identifier(value: Any, field: str) -> str:
    identifier = _required_text(value, field, maximum=192)
    if not RUN_IDENTIFIER.fullmatch(identifier):
        raise ServiceError(
            422,
            "INVALID_INPUT",
            f"{field} contains unsupported characters or is too long",
        )
    return identifier


def _positive_number(value: Any, field: str, *, maximum: float) -> float:
    if isinstance(value, bool):
        raise ServiceError(422, "INVALID_INPUT", f"{field} must be a number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ServiceError(
            422, "INVALID_INPUT", f"{field} must be a number"
        ) from exc
    if not math.isfinite(number) or number <= 0 or number > maximum:
        raise ServiceError(
            422,
            "INVALID_INPUT",
            f"{field} must be greater than zero and at most {maximum:g}",
        )
    return number


def _append_event(
    connection: sqlite3.Connection,
    *,
    kind: str,
    thesis_id: str | None,
    payload: dict[str, Any],
    request_id: str,
    logical_time: str | None = None,
) -> int:
    body = dict(payload)
    body["request_id"] = request_id
    cursor = connection.execute(
        "INSERT INTO events(logical_time,kind,thesis_id,payload) VALUES(?,?,?,?)",
        (
            logical_time or utc_now(),
            kind,
            thesis_id,
            json.dumps(body, sort_keys=True, separators=(",", ":")),
        ),
    )
    return int(cursor.lastrowid)


def _append_decision(
    connection: sqlite3.Connection,
    *,
    thesis_id: str,
    run_id: str | None,
    verdict: str,
    predicate: str,
    evidence: dict[str, Any],
    request_id: str,
    confirmer: str | None = None,
    reason: str | None = None,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO decisions(
          thesis_id,run_id,verdict,predicate,evidence,confirmer,reason
        ) VALUES(?,?,?,?,?,?,?)
        """,
        (
            thesis_id,
            run_id,
            verdict,
            predicate,
            json.dumps(evidence, sort_keys=True, separators=(",", ":")),
            confirmer,
            reason,
        ),
    )
    decision_id = int(cursor.lastrowid)
    _append_event(
        connection,
        kind="DECISION",
        thesis_id=thesis_id,
        payload={
            "decision_id": decision_id,
            "run_id": run_id,
            "verdict": verdict,
            "confirmer": confirmer,
            "reason": reason,
        },
        request_id=request_id,
    )
    return decision_id


def _json_value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


class Workspace:
    """Atomic application operations over one persistent SQLite workspace."""

    def __init__(
        self,
        ledger_path: str | Path,
        reports_dir: str | Path,
        *,
        max_budget: float = 1_000_000.0,
    ):
        self.ledger_path = Path(ledger_path)
        self.reports_dir = Path(reports_dir)
        self.max_budget = max_budget
        self._write_lock = threading.RLock()

    @contextmanager
    def _ledger(self) -> Iterator[Ledger]:
        with Ledger(self.ledger_path) as ledger:
            yield ledger

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._write_lock:
            with self._ledger() as ledger:
                connection = ledger.connection
                connection.execute("BEGIN IMMEDIATE")
                try:
                    yield connection
                except Exception:
                    connection.rollback()
                    raise
                else:
                    connection.commit()

    def bootstrap(self, total_budget: Any, request_id: str) -> dict[str, Any]:
        budget = _positive_number(
            total_budget, "total_budget", maximum=self.max_budget
        )
        now = utc_now()
        with self._write() as connection:
            row = connection.execute(
                "SELECT total_budget,remaining FROM portfolio_state WHERE id=1"
            ).fetchone()
            if row is not None:
                if abs(float(row["total_budget"]) - budget) > 1e-9:
                    raise ServiceError(
                        409,
                        "PORTFOLIO_ALREADY_INITIALIZED",
                        "portfolio already exists with a different budget",
                        details={
                            "total_budget": float(row["total_budget"]),
                            "remaining": float(row["remaining"]),
                        },
                    )
            else:
                connection.execute(
                    """
                    INSERT INTO portfolio_state(
                      id,total_budget,remaining,created_at,updated_at
                    ) VALUES(1,?,?,?,?)
                    """,
                    (budget, budget, now, now),
                )
                _append_event(
                    connection,
                    kind="PORTFOLIO_INITIALIZED",
                    thesis_id=None,
                    payload={"total_budget": budget},
                    request_id=request_id,
                    logical_time=now,
                )
        return self.overview()

    def readiness(self) -> dict[str, Any]:
        try:
            with self._ledger() as ledger:
                ledger.connection.execute("SELECT 1").fetchone()
                initialized = (
                    ledger.connection.execute(
                        "SELECT 1 FROM portfolio_state WHERE id=1"
                    ).fetchone()
                    is not None
                )
        except sqlite3.Error as exc:
            return {"ready": False, "database": "unavailable", "error": str(exc)}
        return {
            "ready": initialized,
            "database": "available",
            "portfolio_initialized": initialized,
        }

    def _require_portfolio(self, connection: sqlite3.Connection) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM portfolio_state WHERE id=1"
        ).fetchone()
        if row is None:
            raise ServiceError(
                409,
                "PORTFOLIO_NOT_INITIALIZED",
                "initialize the portfolio before mutating it",
            )
        return row

    def overview(self) -> dict[str, Any]:
        with self._ledger() as ledger:
            connection = ledger.connection
            state = self._require_portfolio(connection)
            thesis_rows = connection.execute(
                """
                SELECT id,title,prediction,kill_predicate,budget_cap,deadline,
                       limitations,status,spent,version
                FROM theses ORDER BY id
                """
            ).fetchall()
            statuses: dict[str, int] = {}
            theses = []
            for row in thesis_rows:
                status = str(row["status"])
                statuses[status] = statuses.get(status, 0) + 1
                theses.append(self._thesis_summary(row))
            return {
                "total_budget": float(state["total_budget"]),
                "remaining": float(state["remaining"]),
                "spent": float(state["total_budget"]) - float(state["remaining"]),
                "statuses": statuses,
                "theses": theses,
                "runs": int(
                    connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
                ),
                "events": int(
                    connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
                ),
                "updated_at": str(state["updated_at"]),
            }

    @staticmethod
    def _thesis_summary(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "version": int(row["version"]),
            "title": str(row["title"]),
            "prediction": str(row["prediction"]),
            "kill_predicate": str(row["kill_predicate"]),
            "budget_cap": float(row["budget_cap"]),
            "spent": float(row["spent"]),
            "decision_deadline": str(row["deadline"]),
            "limitations": _json_value(str(row["limitations"])),
            "status": str(row["status"]),
        }

    def list_theses(self) -> list[dict[str, Any]]:
        return self.overview()["theses"]

    def get_thesis(self, thesis_id: str) -> dict[str, Any]:
        thesis_id = _identifier(thesis_id, "thesis_id")
        with self._ledger() as ledger:
            connection = ledger.connection
            row = connection.execute(
                "SELECT * FROM theses WHERE id=?", (thesis_id,)
            ).fetchone()
            if row is None:
                raise ServiceError(404, "THESIS_NOT_FOUND", "thesis was not found")
            plan_rows = connection.execute(
                """
                SELECT version,body,approved_by FROM plans
                WHERE thesis_id=? ORDER BY version DESC
                """,
                (thesis_id,),
            ).fetchall()
            runs = [
                self._run_document(item)
                for item in connection.execute(
                    "SELECT * FROM runs WHERE thesis_id=? ORDER BY rowid",
                    (thesis_id,),
                )
            ]
            decisions = [
                self._decision_document(item)
                for item in connection.execute(
                    "SELECT * FROM decisions WHERE thesis_id=? ORDER BY id",
                    (thesis_id,),
                )
            ]
            result = self._thesis_summary(row)
            result["plans"] = [
                {
                    "version": int(plan["version"]),
                    "approved_by": plan["approved_by"],
                    "nodes": _json_value(str(plan["body"])),
                }
                for plan in plan_rows
            ]
            result["runs"] = runs
            result["decisions"] = decisions
            return result

    def register(self, payload: Any, request_id: str) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ServiceError(422, "INVALID_INPUT", "request body must be an object")
        thesis_id = _identifier(payload.get("id"), "id")
        title = _required_text(payload.get("title"), "title", maximum=200)
        prediction = _required_text(payload.get("prediction"), "prediction")
        predicate_text = _required_text(
            payload.get("kill_predicate"), "kill_predicate", maximum=512
        )
        try:
            SafePredicate(predicate_text)
        except PredicateError as exc:
            raise ServiceError(
                422, "INVALID_PREDICATE", f"kill_predicate is unsafe: {exc}"
            ) from exc
        budget_cap = _positive_number(
            payload.get("budget_cap"), "budget_cap", maximum=self.max_budget
        )
        deadline = _required_text(
            payload.get("decision_deadline"), "decision_deadline", maximum=128
        )
        raw_limitations = payload.get("limitations", [])
        if not isinstance(raw_limitations, list) or len(raw_limitations) > 32:
            raise ServiceError(
                422, "INVALID_INPUT", "limitations must be an array of at most 32 items"
            )
        limitations = tuple(
            _required_text(item, f"limitations[{index}]", maximum=512)
            for index, item in enumerate(raw_limitations)
        )
        version = payload.get("version", 1)
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ServiceError(422, "INVALID_INPUT", "version must be a positive integer")
        raw_plan = payload.get("plan")
        if not isinstance(raw_plan, dict):
            raise ServiceError(422, "INVALID_INPUT", "plan must be an object")
        plan_version = raw_plan.get("version", version)
        if (
            not isinstance(plan_version, int)
            or isinstance(plan_version, bool)
            or plan_version < 1
        ):
            raise ServiceError(
                422, "INVALID_INPUT", "plan.version must be a positive integer"
            )
        nodes = self._parse_nodes(raw_plan.get("nodes"))
        thesis = Thesis(
            id=thesis_id,
            title=title,
            prediction=prediction,
            kill_predicate=predicate_text,
            budget_cap=budget_cap,
            decision_deadline=deadline,
            limitations=limitations,
            version=version,
        )
        now = utc_now()
        with self._write() as connection:
            state = self._require_portfolio(connection)
            if budget_cap > float(state["total_budget"]):
                raise ServiceError(
                    422,
                    "BUDGET_CAP_EXCEEDS_PORTFOLIO",
                    "thesis budget cap cannot exceed the portfolio budget",
                )
            try:
                connection.execute(
                    """
                    INSERT INTO theses(
                      id,version,title,prediction,kill_predicate,budget_cap,
                      deadline,limitations,status,spent
                    ) VALUES(?,?,?,?,?,?,?,?,?,0)
                    """,
                    (
                        thesis.id,
                        thesis.version,
                        thesis.title,
                        thesis.prediction,
                        thesis.kill_predicate,
                        thesis.budget_cap,
                        thesis.decision_deadline,
                        json.dumps(thesis.limitations),
                        "PLANNED",
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO plans(thesis_id,version,body,approved_by)
                    VALUES(?,?,?,NULL)
                    """,
                    (
                        thesis.id,
                        plan_version,
                        json.dumps(
                            [asdict(node) for node in nodes],
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ServiceError(
                    409, "THESIS_ALREADY_EXISTS", "thesis ID already exists"
                ) from exc
            _append_event(
                connection,
                kind="THESIS_REGISTERED",
                thesis_id=thesis.id,
                payload=asdict(thesis),
                request_id=request_id,
                logical_time=now,
            )
            _append_event(
                connection,
                kind="PLAN_PROPOSED",
                thesis_id=thesis.id,
                payload={"version": plan_version, "nodes": len(nodes)},
                request_id=request_id,
            )
            connection.execute(
                "UPDATE portfolio_state SET updated_at=? WHERE id=1", (utc_now(),)
            )
        return self.get_thesis(thesis_id)

    def _parse_nodes(self, value: Any) -> list[PlanNode]:
        if not isinstance(value, list) or not value or len(value) > 128:
            raise ServiceError(
                422, "INVALID_PLAN", "plan.nodes must contain 1 to 128 nodes"
            )
        nodes: list[PlanNode] = []
        identifiers: set[str] = set()
        for index, raw in enumerate(value):
            if not isinstance(raw, dict):
                raise ServiceError(
                    422, "INVALID_PLAN", f"plan.nodes[{index}] must be an object"
                )
            node_id = _identifier(raw.get("id"), f"plan.nodes[{index}].id")
            if node_id in identifiers:
                raise ServiceError(422, "INVALID_PLAN", f"duplicate node ID: {node_id}")
            identifiers.add(node_id)
            params = raw.get("params", {})
            if not isinstance(params, dict):
                raise ServiceError(
                    422,
                    "INVALID_PLAN",
                    f"plan.nodes[{index}].params must be an object",
                )
            dependencies = raw.get("depends_on", [])
            if not isinstance(dependencies, list):
                raise ServiceError(
                    422,
                    "INVALID_PLAN",
                    f"plan.nodes[{index}].depends_on must be an array",
                )
            depends_on = tuple(
                _identifier(item, f"plan.nodes[{index}].depends_on")
                for item in dependencies
            )
            try:
                node = PlanNode(
                    id=node_id,
                    kind=_required_text(
                        raw.get("kind"), f"plan.nodes[{index}].kind", maximum=32
                    ),
                    cost=_positive_number(
                        raw.get("cost"),
                        f"plan.nodes[{index}].cost",
                        maximum=self.max_budget,
                    ),
                    executor=_required_text(
                        raw.get("executor", "local"),
                        f"plan.nodes[{index}].executor",
                        maximum=32,
                    ),
                    params=params,
                    depends_on=depends_on,
                )
            except ValueError as exc:
                raise ServiceError(422, "INVALID_PLAN", str(exc)) from exc
            if node.executor != "local":
                raise ServiceError(
                    422,
                    "UNSUPPORTED_EXECUTOR",
                    "the installed service currently executes only local nodes",
                )
            # Validate paired input before accepting an executable local plan.
            try:
                scores, _, _ = LocalExecutor().execute(node)
            except (TypeError, ValueError) as exc:
                raise ServiceError(
                    422,
                    "INVALID_PLAN",
                    f"node {node.id} is not executable: {exc}",
                ) from exc
            if not all(math.isfinite(value) for value in scores.values()):
                raise ServiceError(
                    422,
                    "INVALID_PLAN",
                    f"node {node.id} produces non-finite scores",
                )
            nodes.append(node)
        if not any(node.kind == "falsifier" for node in nodes):
            raise ServiceError(
                422, "INVALID_PLAN", "an executable plan requires a falsifier"
            )
        for node in nodes:
            unknown = set(node.depends_on) - identifiers
            if unknown:
                raise ServiceError(
                    422,
                    "INVALID_PLAN",
                    f"node {node.id} has unknown dependencies: {sorted(unknown)}",
                )
            if node.id in node.depends_on:
                raise ServiceError(
                    422, "INVALID_PLAN", f"node {node.id} cannot depend on itself"
                )
            if node.kind == "falsifier" and node.depends_on:
                raise ServiceError(
                    422,
                    "INVALID_PLAN",
                    f"falsifier {node.id} cannot depend on a later-stage node",
                )
        self._validate_acyclic(nodes)
        return nodes

    @staticmethod
    def _validate_acyclic(nodes: list[PlanNode]) -> None:
        dependencies = {node.id: set(node.depends_on) for node in nodes}
        resolved: set[str] = set()
        while len(resolved) < len(nodes):
            ready = {
                node_id
                for node_id, required in dependencies.items()
                if node_id not in resolved and required <= resolved
            }
            if not ready:
                raise ServiceError(422, "INVALID_PLAN", "plan dependency graph has a cycle")
            resolved.update(ready)

    def approve(self, thesis_id: str, actor: Any, request_id: str) -> dict[str, Any]:
        thesis_id = _identifier(thesis_id, "thesis_id")
        approver = _required_text(actor, "actor", maximum=ACTOR_MAX_LENGTH)
        with self._write() as connection:
            self._require_portfolio(connection)
            plan = connection.execute(
                """
                SELECT version FROM plans WHERE thesis_id=?
                ORDER BY version DESC LIMIT 1
                """,
                (thesis_id,),
            ).fetchone()
            if plan is None:
                raise ServiceError(404, "THESIS_NOT_FOUND", "thesis was not found")
            connection.execute(
                "UPDATE plans SET approved_by=? WHERE thesis_id=? AND version=?",
                (approver, thesis_id, int(plan["version"])),
            )
            _append_event(
                connection,
                kind="PLAN_APPROVED",
                thesis_id=thesis_id,
                payload={"version": int(plan["version"]), "approver": approver},
                request_id=request_id,
            )
            connection.execute(
                "UPDATE portfolio_state SET updated_at=? WHERE id=1", (utc_now(),)
            )
        return self.get_thesis(thesis_id)

    @staticmethod
    def _plan_nodes(connection: sqlite3.Connection, thesis_id: str) -> tuple[int, list[PlanNode]]:
        plan = connection.execute(
            """
            SELECT version,body,approved_by FROM plans
            WHERE thesis_id=? ORDER BY version DESC LIMIT 1
            """,
            (thesis_id,),
        ).fetchone()
        if plan is None:
            raise ServiceError(404, "THESIS_NOT_FOUND", "thesis was not found")
        if not plan["approved_by"]:
            raise ServiceError(
                409, "PLAN_NOT_APPROVED", "unapproved plans cannot execute"
            )
        nodes = [
            PlanNode(
                id=str(raw["id"]),
                kind=str(raw["kind"]),
                cost=float(raw["cost"]),
                executor=str(raw.get("executor", "local")),
                params=dict(raw.get("params", {})),
                depends_on=tuple(raw.get("depends_on", [])),
            )
            for raw in json.loads(str(plan["body"]))
        ]
        return int(plan["version"]), nodes

    def execute(
        self, thesis_id: str, node_id: str, request_id: str
    ) -> dict[str, Any]:
        thesis_id = _identifier(thesis_id, "thesis_id")
        node_id = _identifier(node_id, "node_id")
        with self._write() as connection:
            state = self._require_portfolio(connection)
            thesis = connection.execute(
                "SELECT * FROM theses WHERE id=?", (thesis_id,)
            ).fetchone()
            if thesis is None:
                raise ServiceError(404, "THESIS_NOT_FOUND", "thesis was not found")
            status = str(thesis["status"])
            if status in {"PENDING_KILL", "KILLED", "UNDETERMINED", "SURVIVED"}:
                raise ServiceError(
                    409,
                    "THESIS_NOT_EXECUTABLE",
                    f"thesis in {status} state cannot execute another node",
                )
            plan_version, nodes = self._plan_nodes(connection, thesis_id)
            selected = next((node for node in nodes if node.id == node_id), None)
            if selected is None:
                raise ServiceError(404, "NODE_NOT_FOUND", "plan node was not found")
            run_id = f"{thesis_id}-v{plan_version}-{selected.id}"
            if connection.execute(
                "SELECT 1 FROM runs WHERE id=?", (run_id,)
            ).fetchone():
                raise ServiceError(
                    409, "NODE_ALREADY_COMPLETED", "plan node already completed"
                )
            completed_nodes = {
                str(row["node_id"])
                for row in connection.execute(
                    """
                    SELECT node_id FROM runs
                    WHERE thesis_id=? AND plan_version=? AND status='COMPLETED'
                    """,
                    (thesis_id, plan_version),
                )
            }
            missing = set(selected.depends_on) - completed_nodes
            if missing:
                raise ServiceError(
                    409,
                    "DEPENDENCY_NOT_COMPLETED",
                    "node dependencies are not complete",
                    details={"missing": sorted(missing)},
                )
            remaining = float(state["remaining"])
            spent = float(thesis["spent"])
            if selected.cost > remaining + 1e-9:
                raise ServiceError(
                    409,
                    "PORTFOLIO_BUDGET_EXHAUSTED",
                    "portfolio budget cannot fund this node",
                    details={"cost": selected.cost, "remaining": remaining},
                )
            if spent + selected.cost > float(thesis["budget_cap"]) + 1e-9:
                raise ServiceError(
                    409,
                    "THESIS_BUDGET_EXHAUSTED",
                    "thesis budget cap cannot fund this node",
                    details={
                        "cost": selected.cost,
                        "spent": spent,
                        "budget_cap": float(thesis["budget_cap"]),
                    },
                )
            try:
                scores, data_hash, seed = LocalExecutor().execute(selected)
            except (TypeError, ValueError) as exc:
                raise ServiceError(
                    422, "EXECUTION_INPUT_INVALID", str(exc)
                ) from exc
            new_remaining = remaining - selected.cost
            now = utc_now()
            connection.execute(
                """
                UPDATE portfolio_state SET remaining=?,updated_at=? WHERE id=1
                """,
                (new_remaining, now),
            )
            connection.execute(
                "UPDATE theses SET spent=spent+?,status='ACTIVE' WHERE id=?",
                (selected.cost, thesis_id),
            )
            connection.execute(
                """
                INSERT INTO budget_entries(
                  thesis_id,run_id,action,amount,remaining
                ) VALUES(?,?, 'SPEND', ?,?)
                """,
                (thesis_id, run_id, selected.cost, new_remaining),
            )
            _append_event(
                connection,
                kind="BUDGET_SPENT",
                thesis_id=thesis_id,
                payload={
                    "run_id": run_id,
                    "amount": selected.cost,
                    "remaining": new_remaining,
                },
                request_id=request_id,
                logical_time=now,
            )
            connection.execute(
                """
                INSERT INTO runs(
                  id,thesis_id,plan_version,node_id,node_kind,executor,cost,
                  status,scores,code_commit,data_hash,seed
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    run_id,
                    thesis_id,
                    plan_version,
                    selected.id,
                    selected.kind,
                    selected.executor,
                    selected.cost,
                    "COMPLETED",
                    json.dumps(scores, sort_keys=True, separators=(",", ":")),
                    "installed-service",
                    data_hash,
                    seed,
                ),
            )
            _append_event(
                connection,
                kind="RUN_COMPLETED",
                thesis_id=thesis_id,
                payload={
                    "run_id": run_id,
                    "node": selected.id,
                    "scores": scores,
                },
                request_id=request_id,
            )
            verdict: str | None = None
            if selected.kind == "falsifier":
                predicate = SafePredicate(str(thesis["kill_predicate"]))
                try:
                    fired = predicate.evaluate(scores)
                except (PredicateUndetermined, PredicateError) as exc:
                    verdict = "UNDETERMINED"
                    _append_decision(
                        connection,
                        thesis_id=thesis_id,
                        run_id=run_id,
                        verdict=verdict,
                        predicate=str(thesis["kill_predicate"]),
                        evidence={"scores": scores, "error": str(exc)},
                        request_id=request_id,
                    )
                    connection.execute(
                        "UPDATE theses SET status='UNDETERMINED' WHERE id=?",
                        (thesis_id,),
                    )
                else:
                    verdict = "PENDING_KILL" if fired else "CONTINUE"
                    _append_decision(
                        connection,
                        thesis_id=thesis_id,
                        run_id=run_id,
                        verdict=verdict,
                        predicate=str(thesis["kill_predicate"]),
                        evidence={"scores": scores},
                        request_id=request_id,
                    )
                    if fired:
                        connection.execute(
                            "UPDATE theses SET status='PENDING_KILL' WHERE id=?",
                            (thesis_id,),
                        )
            completed_nodes.add(selected.id)
            if (
                verdict != "PENDING_KILL"
                and verdict != "UNDETERMINED"
                and completed_nodes == {node.id for node in nodes}
            ):
                verdict = "PROMOTE"
                connection.execute(
                    "UPDATE theses SET status='SURVIVED' WHERE id=?", (thesis_id,)
                )
                _append_decision(
                    connection,
                    thesis_id=thesis_id,
                    run_id=run_id,
                    verdict=verdict,
                    predicate=str(thesis["kill_predicate"]),
                    evidence={"reason": "approved plan completed without a kill"},
                    request_id=request_id,
                )
        return {
            "run": self.get_run(run_id),
            "thesis": self.get_thesis(thesis_id),
            "portfolio": self.overview(),
        }

    def _next_node(self) -> tuple[str, str] | None:
        with self._ledger() as ledger:
            connection = ledger.connection
            self._require_portfolio(connection)
            candidates: list[tuple[bool, float, str, str]] = []
            for thesis in connection.execute(
                """
                SELECT id,status FROM theses
                WHERE status NOT IN ('PENDING_KILL','KILLED','UNDETERMINED','SURVIVED')
                ORDER BY id
                """
            ):
                thesis_id = str(thesis["id"])
                try:
                    plan_version, nodes = self._plan_nodes(connection, thesis_id)
                except ServiceError as exc:
                    if exc.code == "PLAN_NOT_APPROVED":
                        continue
                    raise
                completed = {
                    str(row["node_id"])
                    for row in connection.execute(
                        """
                        SELECT node_id FROM runs
                        WHERE thesis_id=? AND plan_version=? AND status='COMPLETED'
                        """,
                        (thesis_id, plan_version),
                    )
                }
                for node in nodes:
                    if node.id in completed or not set(node.depends_on) <= completed:
                        continue
                    candidates.append(
                        (node.kind != "falsifier", node.cost, thesis_id, node.id)
                    )
            if not candidates:
                return None
            _, _, thesis_id, node_id = min(candidates)
            return thesis_id, node_id

    def step(self, request_id: str) -> dict[str, Any]:
        candidate = self._next_node()
        if candidate is None:
            return {"state": "IDLE", "portfolio": self.overview()}
        thesis_id, node_id = candidate
        result = self.execute(thesis_id, node_id, request_id)
        result["state"] = "COMPLETED_STEP"
        return result

    def run(self, max_steps: Any, request_id: str) -> dict[str, Any]:
        if max_steps is None:
            max_steps = 100
        if (
            not isinstance(max_steps, int)
            or isinstance(max_steps, bool)
            or not 1 <= max_steps <= 1_000
        ):
            raise ServiceError(
                422, "INVALID_INPUT", "max_steps must be an integer from 1 to 1000"
            )
        completed: list[str] = []
        for index in range(max_steps):
            step_request_id = f"{request_id}:{index + 1}"
            try:
                result = self.step(step_request_id)
            except ServiceError as exc:
                if exc.code not in {
                    "PORTFOLIO_BUDGET_EXHAUSTED",
                    "THESIS_BUDGET_EXHAUSTED",
                }:
                    raise
                return {
                    "state": "BUDGET_BLOCKED",
                    "completed_runs": completed,
                    "blocked": {
                        "code": exc.code,
                        "message": exc.message,
                        "details": exc.details,
                    },
                    "portfolio": self.overview(),
                }
            if result["state"] == "IDLE":
                return {
                    "state": "IDLE",
                    "completed_runs": completed,
                    "portfolio": result["portfolio"],
                }
            completed.append(str(result["run"]["id"]))
        return {
            "state": "STEP_LIMIT_REACHED",
            "completed_runs": completed,
            "portfolio": self.overview(),
        }

    def confirm_kill(
        self, thesis_id: str, actor: Any, request_id: str
    ) -> dict[str, Any]:
        thesis_id = _identifier(thesis_id, "thesis_id")
        confirmer = _required_text(actor, "actor", maximum=ACTOR_MAX_LENGTH)
        report_payload: dict[str, Any]
        with self._write() as connection:
            self._require_portfolio(connection)
            thesis = connection.execute(
                "SELECT * FROM theses WHERE id=?", (thesis_id,)
            ).fetchone()
            if thesis is None:
                raise ServiceError(404, "THESIS_NOT_FOUND", "thesis was not found")
            if str(thesis["status"]) != "PENDING_KILL":
                raise ServiceError(
                    409, "KILL_NOT_PENDING", "thesis does not have a pending kill"
                )
            pending = connection.execute(
                """
                SELECT * FROM decisions
                WHERE thesis_id=? AND verdict='PENDING_KILL'
                ORDER BY id DESC LIMIT 1
                """,
                (thesis_id,),
            ).fetchone()
            if pending is None:
                raise ServiceError(
                    409, "KILL_EVIDENCE_MISSING", "pending kill has no evidence"
                )
            evidence = _json_value(str(pending["evidence"]))
            _append_decision(
                connection,
                thesis_id=thesis_id,
                run_id=str(pending["run_id"]),
                verdict="KILL",
                predicate=str(thesis["kill_predicate"]),
                evidence={
                    **(evidence if isinstance(evidence, dict) else {"raw": evidence}),
                    "limitations": _json_value(str(thesis["limitations"])),
                },
                request_id=request_id,
                confirmer=confirmer,
            )
            connection.execute(
                "UPDATE theses SET status='KILLED' WHERE id=?", (thesis_id,)
            )
            unused = max(0.0, float(thesis["budget_cap"]) - float(thesis["spent"]))
            state = self._require_portfolio(connection)
            connection.execute(
                """
                INSERT INTO budget_entries(
                  thesis_id,run_id,action,amount,remaining
                ) VALUES(?,?, 'RELEASE', ?,?)
                """,
                (
                    thesis_id,
                    str(pending["run_id"]),
                    unused,
                    float(state["remaining"]),
                ),
            )
            _append_event(
                connection,
                kind="BUDGET_RELEASED",
                thesis_id=thesis_id,
                payload={
                    "unused_cap": unused,
                    "portfolio_remaining": float(state["remaining"]),
                },
                request_id=request_id,
            )
            connection.execute(
                "UPDATE portfolio_state SET updated_at=? WHERE id=1", (utc_now(),)
            )
            report_payload = {
                "thesis_id": thesis_id,
                "prediction": str(thesis["prediction"]),
                "predicate": str(thesis["kill_predicate"]),
                "run_id": str(pending["run_id"]),
                "scores": evidence.get("scores", {})
                if isinstance(evidence, dict)
                else {},
                "limitations": _json_value(str(thesis["limitations"])),
                "confirmer": confirmer,
            }
        result = self.get_thesis(thesis_id)
        try:
            report_path = self._write_kill_report(report_payload)
        except OSError:
            with self._write() as connection:
                _append_event(
                    connection,
                    kind="KILL_REPORT_WRITE_FAILED",
                    thesis_id=thesis_id,
                    payload={"artifact": f"{thesis_id}-kill.md"},
                    request_id=request_id,
                )
            result["kill_report"] = None
            result["warnings"] = [
                "kill decision persisted, but the derived Markdown report could not be written"
            ]
        else:
            result["kill_report"] = str(report_path)
        return result

    def _write_kill_report(self, payload: dict[str, Any]) -> Path:
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        limitations = payload["limitations"]
        if not isinstance(limitations, list) or not limitations:
            limitations = [
                "No limitations were registered; this is itself a limitation."
            ]
        scores = payload["scores"] if isinstance(payload["scores"], dict) else {}
        score_lines = "\n".join(
            f"- `{key}`: {value}" for key, value in sorted(scores.items())
        ) or "- No score values were recorded."
        limit_lines = "\n".join(f"- {item}" for item in limitations)
        content = f"""# Kill report — {payload['thesis_id']}

## Decision

**KILLED**, confirmed by `{payload['confirmer']}`. The decision is reversible
through the explicit revive endpoint; the original lineage remains immutable.

## Registered prediction

{payload['prediction']}

## Evidence

- Run ID: `{payload['run_id']}`
{score_lines}

Predicate: `{payload['predicate']}`

## Conditions under which the kill may be wrong

{limit_lines}

This report records local decision provenance. It does not establish a general
false-kill accuracy result.
"""
        target = self.reports_dir / f"{payload['thesis_id']}-kill.md"
        target.write_text(content, encoding="utf-8")
        return target

    def overturn(
        self, thesis_id: str, actor: Any, reason: Any, request_id: str
    ) -> dict[str, Any]:
        return self._decision_transition(
            thesis_id=thesis_id,
            required_status="PENDING_KILL",
            next_status="ACTIVE",
            verdict="OVERTURN",
            actor=actor,
            reason=reason,
            request_id=request_id,
        )

    def revive(
        self, thesis_id: str, actor: Any, reason: Any, request_id: str
    ) -> dict[str, Any]:
        return self._decision_transition(
            thesis_id=thesis_id,
            required_status="KILLED",
            next_status="REVIVED",
            verdict="REVIVE",
            actor=actor,
            reason=reason,
            request_id=request_id,
        )

    def _decision_transition(
        self,
        *,
        thesis_id: str,
        required_status: str,
        next_status: str,
        verdict: str,
        actor: Any,
        reason: Any,
        request_id: str,
    ) -> dict[str, Any]:
        thesis_id = _identifier(thesis_id, "thesis_id")
        actor_text = _required_text(actor, "actor", maximum=ACTOR_MAX_LENGTH)
        reason_text = _required_text(reason, "reason")
        with self._write() as connection:
            self._require_portfolio(connection)
            thesis = connection.execute(
                "SELECT * FROM theses WHERE id=?", (thesis_id,)
            ).fetchone()
            if thesis is None:
                raise ServiceError(404, "THESIS_NOT_FOUND", "thesis was not found")
            if str(thesis["status"]) != required_status:
                raise ServiceError(
                    409,
                    "INVALID_DECISION_STATE",
                    f"{verdict} requires {required_status} state",
                )
            connection.execute(
                "UPDATE theses SET status=? WHERE id=?", (next_status, thesis_id)
            )
            _append_decision(
                connection,
                thesis_id=thesis_id,
                run_id=None,
                verdict=verdict,
                predicate=str(thesis["kill_predicate"]),
                evidence={"challenge": reason_text},
                request_id=request_id,
                confirmer=actor_text,
                reason=reason_text,
            )
            connection.execute(
                "UPDATE portfolio_state SET updated_at=? WHERE id=1", (utc_now(),)
            )
        return self.get_thesis(thesis_id)

    def reallocate(
        self,
        source_thesis_id: Any,
        target_thesis_id: Any,
        amount: Any,
        actor: Any,
        reason: Any,
        request_id: str,
        *,
        reversed_from: int | None = None,
    ) -> dict[str, Any]:
        source = _identifier(source_thesis_id, "source_thesis_id")
        target = _identifier(target_thesis_id, "target_thesis_id")
        if source == target:
            raise ServiceError(
                422, "INVALID_REALLOCATION", "source and target must differ"
            )
        value = _positive_number(amount, "amount", maximum=self.max_budget)
        actor_text = _required_text(actor, "actor", maximum=ACTOR_MAX_LENGTH)
        reason_text = _required_text(reason, "reason")
        with self._write() as connection:
            state = self._require_portfolio(connection)
            if reversed_from is not None and connection.execute(
                "SELECT 1 FROM reallocations WHERE reversed_from=?",
                (reversed_from,),
            ).fetchone():
                raise ServiceError(
                    409,
                    "REALLOCATION_ALREADY_REVERSED",
                    "reallocation was already reversed",
                )
            rows = {
                str(row["id"]): row
                for row in connection.execute(
                    "SELECT * FROM theses WHERE id IN (?,?)", (source, target)
                )
            }
            if source not in rows or target not in rows:
                raise ServiceError(
                    404, "THESIS_NOT_FOUND", "source or target thesis was not found"
                )
            source_row = rows[source]
            target_row = rows[target]
            if float(source_row["budget_cap"]) - value < float(source_row["spent"]) - 1e-9:
                raise ServiceError(
                    409,
                    "SOURCE_CAP_COMMITTED",
                    "source has insufficient unspent cap for this transfer",
                )
            target_cap = float(target_row["budget_cap"]) + value
            if target_cap > float(state["total_budget"]) + 1e-9:
                raise ServiceError(
                    409,
                    "TARGET_CAP_EXCEEDS_PORTFOLIO",
                    "target cap cannot exceed total portfolio budget",
                )
            connection.execute(
                "UPDATE theses SET budget_cap=budget_cap-? WHERE id=?",
                (value, source),
            )
            connection.execute(
                "UPDATE theses SET budget_cap=budget_cap+? WHERE id=?",
                (value, target),
            )
            cursor = connection.execute(
                """
                INSERT INTO reallocations(
                  source_thesis_id,target_thesis_id,amount,actor,reason,
                  logical_time,reversed_from
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    source,
                    target,
                    value,
                    actor_text,
                    reason_text,
                    utc_now(),
                    reversed_from,
                ),
            )
            transfer_id = int(cursor.lastrowid)
            _append_event(
                connection,
                kind="BUDGET_REALLOCATED",
                thesis_id=target,
                payload={
                    "reallocation_id": transfer_id,
                    "source_thesis_id": source,
                    "target_thesis_id": target,
                    "amount": value,
                    "actor": actor_text,
                    "reason": reason_text,
                    "reversed_from": reversed_from,
                },
                request_id=request_id,
            )
            connection.execute(
                "UPDATE portfolio_state SET updated_at=? WHERE id=1", (utc_now(),)
            )
        return self.get_reallocation(transfer_id)

    def reverse_reallocation(
        self, transfer_id: int, actor: Any, reason: Any, request_id: str
    ) -> dict[str, Any]:
        if transfer_id < 1:
            raise ServiceError(
                422, "INVALID_INPUT", "reallocation ID must be positive"
            )
        with self._ledger() as ledger:
            original = ledger.connection.execute(
                "SELECT * FROM reallocations WHERE id=?", (transfer_id,)
            ).fetchone()
            if original is None:
                raise ServiceError(
                    404, "REALLOCATION_NOT_FOUND", "reallocation was not found"
                )
            if ledger.connection.execute(
                "SELECT 1 FROM reallocations WHERE reversed_from=?",
                (transfer_id,),
            ).fetchone():
                raise ServiceError(
                    409,
                    "REALLOCATION_ALREADY_REVERSED",
                    "reallocation was already reversed",
                )
            source = str(original["target_thesis_id"])
            target = str(original["source_thesis_id"])
            amount = float(original["amount"])
        return self.reallocate(
            source,
            target,
            amount,
            actor,
            reason,
            request_id,
            reversed_from=transfer_id,
        )

    def get_reallocation(self, transfer_id: int) -> dict[str, Any]:
        with self._ledger() as ledger:
            row = ledger.connection.execute(
                "SELECT * FROM reallocations WHERE id=?", (transfer_id,)
            ).fetchone()
            if row is None:
                raise ServiceError(
                    404, "REALLOCATION_NOT_FOUND", "reallocation was not found"
                )
            return {
                "id": int(row["id"]),
                "source_thesis_id": str(row["source_thesis_id"]),
                "target_thesis_id": str(row["target_thesis_id"]),
                "amount": float(row["amount"]),
                "actor": str(row["actor"]),
                "reason": str(row["reason"]),
                "logical_time": str(row["logical_time"]),
                "reversed_from": row["reversed_from"],
            }

    @staticmethod
    def _run_document(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "thesis_id": str(row["thesis_id"]),
            "plan_version": int(row["plan_version"]),
            "node_id": str(row["node_id"]),
            "node_kind": str(row["node_kind"]),
            "executor": str(row["executor"]),
            "cost": float(row["cost"]),
            "status": str(row["status"]),
            "scores": _json_value(str(row["scores"])),
            "code_commit": str(row["code_commit"]),
            "data_hash": str(row["data_hash"]),
            "seed": int(row["seed"]),
        }

    @staticmethod
    def _decision_document(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "thesis_id": str(row["thesis_id"]),
            "run_id": row["run_id"],
            "verdict": str(row["verdict"]),
            "predicate": str(row["predicate"]),
            "evidence": _json_value(str(row["evidence"])),
            "confirmer": row["confirmer"],
            "reason": row["reason"],
        }

    def get_run(self, run_id: str) -> dict[str, Any]:
        run_id = _run_identifier(run_id, "run_id")
        with self._ledger() as ledger:
            row = ledger.connection.execute(
                "SELECT * FROM runs WHERE id=?", (run_id,)
            ).fetchone()
            if row is None:
                raise ServiceError(404, "RUN_NOT_FOUND", "run was not found")
            return self._run_document(row)

    def events(self, *, after: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        if after < 0 or not 1 <= limit <= 500:
            raise ServiceError(
                422, "INVALID_INPUT", "after must be nonnegative; limit must be 1..500"
            )
        with self._ledger() as ledger:
            rows = ledger.connection.execute(
                """
                SELECT * FROM events WHERE sequence>?
                ORDER BY sequence LIMIT ?
                """,
                (after, limit),
            ).fetchall()
            return [
                {
                    "sequence": int(row["sequence"]),
                    "logical_time": str(row["logical_time"]),
                    "kind": str(row["kind"]),
                    "thesis_id": row["thesis_id"],
                    "payload": _json_value(str(row["payload"])),
                }
                for row in rows
            ]


class APIServer(ThreadingHTTPServer):
    """HTTP server carrying immutable config and a persistent workspace."""

    daemon_threads = True

    def __init__(self, config: ServiceConfig, workspace: Workspace):
        self.config = config
        self.workspace = workspace
        super().__init__((config.host, config.port), APIHandler)


class APIHandler(BaseHTTPRequestHandler):
    """Small JSON API with explicit limits and stable error envelopes."""

    server: APIServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        # Keep request IDs in logs without exposing authorization headers/bodies.
        message = format % args
        print(f"{utc_now()} request_id={self._request_id()} {message}", flush=True)

    def _request_id(self) -> str:
        current = getattr(self, "_flotilla_request_id", None)
        if current:
            return str(current)
        incoming = self.headers.get("X-Request-ID", "")
        if (
            incoming
            and len(incoming) <= 128
            and all(32 <= ord(character) < 127 for character in incoming)
        ):
            current = incoming
        else:
            current = uuid.uuid4().hex
        self._flotilla_request_id = current
        return current

    def _cors_headers(self) -> dict[str, str]:
        configured = self.server.config.allowed_origin
        origin = self.headers.get("Origin")
        if configured and origin == configured:
            return {
                "Access-Control-Allow-Origin": configured,
                "Access-Control-Allow-Headers": "Authorization, Content-Type, X-Request-ID",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Private-Network": "true",
                "Vary": "Origin",
            }
        return {}

    def _send(self, status: int, body: Any) -> None:
        payload = json.dumps(
            body, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Request-ID", self._request_id())
        for name, value in self._cors_headers().items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(payload)

    def _error(self, error: ServiceError) -> None:
        body: dict[str, Any] = {
            "error": {
                "code": error.code,
                "message": error.message,
                "request_id": self._request_id(),
            }
        }
        if error.details is not None:
            body["error"]["details"] = error.details
        self._send(error.status, body)

    def _authorized(self, path: str) -> bool:
        if path in {"/healthz", "/readyz"}:
            return True
        token = self.server.config.api_token
        if token is None:
            return True
        expected = f"Bearer {token}"
        actual = self.headers.get("Authorization", "")
        return hmac.compare_digest(actual, expected)

    def _body(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            return {}
        try:
            length = int(raw_length)
        except ValueError as exc:
            self.close_connection = True
            raise ServiceError(
                400, "INVALID_CONTENT_LENGTH", "Content-Length must be an integer"
            ) from exc
        if length < 0 or length > self.server.config.max_body_bytes:
            self.close_connection = True
            raise ServiceError(
                413,
                "REQUEST_TOO_LARGE",
                f"request body exceeds {self.server.config.max_body_bytes} bytes",
            )
        content_type = self.headers.get("Content-Type", "")
        if length and not content_type.lower().startswith("application/json"):
            self.close_connection = True
            raise ServiceError(
                415, "UNSUPPORTED_MEDIA_TYPE", "Content-Type must be application/json"
            )
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            body = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ServiceError(400, "INVALID_JSON", "request body is not valid JSON") from exc
        if not isinstance(body, dict):
            raise ServiceError(422, "INVALID_INPUT", "request body must be an object")
        return body

    def do_OPTIONS(self) -> None:
        self._flotilla_request_id = None
        request_id = self._request_id()
        del request_id
        origin = self.headers.get("Origin")
        if (
            not self.server.config.allowed_origin
            or origin != self.server.config.allowed_origin
        ):
            self._error(
                ServiceError(403, "ORIGIN_NOT_ALLOWED", "origin is not allowed")
            )
            return
        self._send(204, {})

    def do_GET(self) -> None:
        self._flotilla_request_id = None
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._flotilla_request_id = None
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        request_id = self._request_id()
        target = urlsplit(self.path)
        path = target.path.rstrip("/") or "/"
        if not self._authorized(path):
            self._error(ServiceError(401, "UNAUTHORIZED", "bearer token is required"))
            return
        try:
            body = self._body() if method == "POST" else {}
            result, status = self._route(method, path, target.query, body, request_id)
            self._send(status, result)
        except ServiceError as exc:
            self._error(exc)
        except sqlite3.Error:
            self._error(
                ServiceError(
                    503,
                    "DATABASE_UNAVAILABLE",
                    "the lineage database could not complete the request",
                )
            )
        except Exception as exc:
            # Do not expose paths, SQL, or internals to clients.
            print(
                f"{utc_now()} request_id={request_id} internal_error={exc!r}",
                flush=True,
            )
            self._error(
                ServiceError(
                    500, "INTERNAL_ERROR", "the service could not complete the request"
                )
            )

    def _route(
        self,
        method: str,
        path: str,
        query: str,
        body: dict[str, Any],
        request_id: str,
    ) -> tuple[Any, int]:
        workspace = self.server.workspace
        if method == "GET" and path == "/healthz":
            return {"status": "ok"}, 200
        if method == "GET" and path == "/readyz":
            ready = workspace.readiness()
            return ready, 200 if ready["ready"] else 503
        if path == "/v1/portfolio":
            if method == "GET":
                return workspace.overview(), 200
            if method == "POST":
                return workspace.bootstrap(body.get("total_budget"), request_id), 201
        if method == "POST" and path == "/v1/portfolio/step":
            return workspace.step(request_id), 200
        if method == "POST" and path == "/v1/portfolio/run":
            return workspace.run(body.get("max_steps"), request_id), 200
        if path == "/v1/theses":
            if method == "GET":
                return {"theses": workspace.list_theses()}, 200
            if method == "POST":
                return workspace.register(body, request_id), 201
        thesis_match = re.fullmatch(
            r"/v1/theses/([^/]+)(?:/(approve|runs|confirm-kill|overturn|revive))?",
            path,
        )
        if thesis_match:
            thesis_id = unquote(thesis_match.group(1))
            action = thesis_match.group(2)
            if method == "GET" and action is None:
                return workspace.get_thesis(thesis_id), 200
            if method == "POST" and action == "approve":
                return workspace.approve(thesis_id, body.get("actor"), request_id), 200
            if method == "POST" and action == "runs":
                return workspace.execute(
                    thesis_id, body.get("node_id"), request_id
                ), 201
            if method == "POST" and action == "confirm-kill":
                return workspace.confirm_kill(
                    thesis_id, body.get("actor"), request_id
                ), 200
            if method == "POST" and action == "overturn":
                return workspace.overturn(
                    thesis_id,
                    body.get("actor"),
                    body.get("reason"),
                    request_id,
                ), 200
            if method == "POST" and action == "revive":
                return workspace.revive(
                    thesis_id,
                    body.get("actor"),
                    body.get("reason"),
                    request_id,
                ), 200
        if method == "GET" and path.startswith("/v1/runs/"):
            return workspace.get_run(unquote(path.removeprefix("/v1/runs/"))), 200
        if method == "GET" and path == "/v1/events":
            params = parse_qs(query)
            try:
                after = int(params.get("after", ["0"])[0])
                limit = int(params.get("limit", ["100"])[0])
            except ValueError as exc:
                raise ServiceError(
                    422, "INVALID_INPUT", "after and limit must be integers"
                ) from exc
            return {"events": workspace.events(after=after, limit=limit)}, 200
        if method == "POST" and path == "/v1/reallocations":
            return workspace.reallocate(
                body.get("source_thesis_id"),
                body.get("target_thesis_id"),
                body.get("amount"),
                body.get("actor"),
                body.get("reason"),
                request_id,
            ), 201
        reverse_match = re.fullmatch(r"/v1/reallocations/([0-9]+)/reverse", path)
        if method == "POST" and reverse_match:
            return workspace.reverse_reallocation(
                int(reverse_match.group(1)),
                body.get("actor"),
                body.get("reason"),
                request_id,
            ), 201
        raise ServiceError(404, "ROUTE_NOT_FOUND", "route was not found")


def create_server(
    config: ServiceConfig, *, initial_budget: float | None = None
) -> APIServer:
    """Build a configured server and optionally initialize an empty portfolio."""

    config.validate()
    workspace = Workspace(
        config.ledger_path,
        config.reports_dir,
        max_budget=config.max_budget,
    )
    if initial_budget is not None:
        workspace.bootstrap(initial_budget, "startup")
    return APIServer(config, workspace)


def serve(config: ServiceConfig, *, initial_budget: float | None = None) -> None:
    """Run until interrupted."""

    server = create_server(config, initial_budget=initial_budget)
    print(
        f"FLOTILLA service listening on http://{config.host}:{server.server_port}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
