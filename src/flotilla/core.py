"""Core FLOTILLA models, SQLite lineage, scheduling, and artifacts."""

from __future__ import annotations

import hashlib
import html
import json
import math
import sqlite3
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .predicate import PredicateError, PredicateUndetermined, SafePredicate


@dataclass(frozen=True)
class Thesis:
    id: str
    title: str
    prediction: str
    kill_predicate: str
    budget_cap: float
    decision_deadline: str
    limitations: tuple[str, ...] = ()
    version: int = 1


@dataclass(frozen=True)
class PlanNode:
    id: str
    kind: str
    cost: float
    executor: str = "local"
    params: dict[str, Any] = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        allowed = {"falsifier", "ablation", "confound", "replicate", "aggregate"}
        if self.kind not in allowed:
            raise ValueError(f"unknown plan node kind: {self.kind}")
        if self.cost <= 0:
            raise ValueError("node cost must be positive")


@dataclass
class Plan:
    thesis_id: str
    version: int
    nodes: list[PlanNode]
    approved_by: str | None = None

    @property
    def approved(self) -> bool:
        return self.approved_by is not None


class Ledger:
    """SQLite store with an append-only event stream and normalized views."""

    def __init__(
        self, path: str | Path, *, dashboard_path: str | Path | None = None
    ):
        self.path = Path(path)
        self.dashboard_path = Path(dashboard_path) if dashboard_path else None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._create_schema()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "Ledger":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                logical_time TEXT NOT NULL,
                kind TEXT NOT NULL,
                thesis_id TEXT,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS theses (
                id TEXT PRIMARY KEY,
                version INTEGER NOT NULL,
                title TEXT NOT NULL,
                prediction TEXT NOT NULL,
                kill_predicate TEXT NOT NULL,
                budget_cap REAL NOT NULL,
                deadline TEXT NOT NULL,
                limitations TEXT NOT NULL,
                status TEXT NOT NULL,
                spent REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS plans (
                thesis_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                body TEXT NOT NULL,
                approved_by TEXT,
                PRIMARY KEY (thesis_id, version),
                FOREIGN KEY (thesis_id) REFERENCES theses(id)
            );
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                thesis_id TEXT NOT NULL,
                plan_version INTEGER NOT NULL,
                node_id TEXT NOT NULL,
                node_kind TEXT NOT NULL,
                executor TEXT NOT NULL,
                cost REAL NOT NULL,
                status TEXT NOT NULL,
                scores TEXT NOT NULL,
                code_commit TEXT NOT NULL,
                data_hash TEXT NOT NULL,
                seed INTEGER NOT NULL,
                FOREIGN KEY (thesis_id) REFERENCES theses(id)
            );
            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thesis_id TEXT NOT NULL,
                run_id TEXT,
                verdict TEXT NOT NULL,
                predicate TEXT NOT NULL,
                evidence TEXT NOT NULL,
                confirmer TEXT,
                reason TEXT,
                FOREIGN KEY (thesis_id) REFERENCES theses(id)
            );
            CREATE TABLE IF NOT EXISTS budget_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thesis_id TEXT,
                run_id TEXT,
                action TEXT NOT NULL,
                amount REAL NOT NULL,
                remaining REAL NOT NULL
            );
            """
        )
        self.connection.commit()

    def event(
        self,
        logical_time: str,
        kind: str,
        thesis_id: str | None,
        payload: dict[str, Any],
    ) -> int:
        cursor = self.connection.execute(
            "INSERT INTO events(logical_time,kind,thesis_id,payload) VALUES(?,?,?,?)",
            (logical_time, kind, thesis_id, json.dumps(payload, sort_keys=True)),
        )
        self.connection.commit()
        sequence = int(cursor.lastrowid)
        if self.dashboard_path is not None:
            render_dashboard(self, self.dashboard_path)
        return sequence

    def register_thesis(self, thesis: Thesis, logical_time: str) -> None:
        SafePredicate(thesis.kill_predicate)
        self.connection.execute(
            """
            INSERT INTO theses(
              id,version,title,prediction,kill_predicate,budget_cap,deadline,
              limitations,status,spent
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
        self.connection.commit()
        self.event(logical_time, "THESIS_REGISTERED", thesis.id, asdict(thesis))

    def save_plan(self, plan: Plan, logical_time: str) -> None:
        body = json.dumps([asdict(node) for node in plan.nodes], sort_keys=True)
        self.connection.execute(
            "INSERT INTO plans(thesis_id,version,body,approved_by) VALUES(?,?,?,?)",
            (plan.thesis_id, plan.version, body, plan.approved_by),
        )
        self.connection.commit()
        self.event(
            logical_time,
            "PLAN_PROPOSED",
            plan.thesis_id,
            {"version": plan.version, "nodes": len(plan.nodes)},
        )

    def approve_plan(
        self, thesis_id: str, version: int, approver: str, logical_time: str
    ) -> None:
        cursor = self.connection.execute(
            "UPDATE plans SET approved_by=? WHERE thesis_id=? AND version=?",
            (approver, thesis_id, version),
        )
        if cursor.rowcount != 1:
            raise KeyError(f"plan not found: {thesis_id} v{version}")
        self.connection.commit()
        self.event(
            logical_time,
            "PLAN_APPROVED",
            thesis_id,
            {"version": version, "approver": approver},
        )

    def plan_is_approved(self, thesis_id: str, version: int) -> bool:
        row = self.connection.execute(
            "SELECT approved_by FROM plans WHERE thesis_id=? AND version=?",
            (thesis_id, version),
        ).fetchone()
        return bool(row and row["approved_by"])

    def thesis_status(self, thesis_id: str) -> str:
        row = self.connection.execute(
            "SELECT status FROM theses WHERE id=?", (thesis_id,)
        ).fetchone()
        if row is None:
            raise KeyError(thesis_id)
        return str(row["status"])

    def set_status(self, thesis_id: str, status: str) -> None:
        self.connection.execute(
            "UPDATE theses SET status=? WHERE id=?", (status, thesis_id)
        )
        self.connection.commit()

    def spend(
        self,
        thesis_id: str,
        run_id: str,
        amount: float,
        portfolio_remaining: float,
        logical_time: str,
    ) -> None:
        row = self.connection.execute(
            "SELECT spent,budget_cap FROM theses WHERE id=?", (thesis_id,)
        ).fetchone()
        if row is None:
            raise KeyError(thesis_id)
        if float(row["spent"]) + amount > float(row["budget_cap"]) + 1e-9:
            raise RuntimeError(f"thesis budget exceeded: {thesis_id}")
        self.connection.execute(
            "UPDATE theses SET spent=spent+? WHERE id=?", (amount, thesis_id)
        )
        self.connection.execute(
            """
            INSERT INTO budget_entries(thesis_id,run_id,action,amount,remaining)
            VALUES(?,?,?,?,?)
            """,
            (thesis_id, run_id, "SPEND", amount, portfolio_remaining),
        )
        self.connection.commit()
        self.event(
            logical_time,
            "BUDGET_SPENT",
            thesis_id,
            {"run_id": run_id, "amount": amount, "remaining": portfolio_remaining},
        )

    def release_unused(
        self, thesis_id: str, portfolio_remaining: float, logical_time: str
    ) -> float:
        row = self.connection.execute(
            "SELECT spent,budget_cap FROM theses WHERE id=?", (thesis_id,)
        ).fetchone()
        if row is None:
            raise KeyError(thesis_id)
        amount = max(0.0, float(row["budget_cap"]) - float(row["spent"]))
        self.connection.execute(
            """
            INSERT INTO budget_entries(thesis_id,run_id,action,amount,remaining)
            VALUES(?,NULL,'RELEASE',?,?)
            """,
            (thesis_id, amount, portfolio_remaining),
        )
        self.connection.commit()
        self.event(
            logical_time,
            "BUDGET_RELEASED",
            thesis_id,
            {"unused_cap": amount, "portfolio_remaining": portfolio_remaining},
        )
        return amount

    def record_run(
        self,
        *,
        run_id: str,
        thesis_id: str,
        plan_version: int,
        node: PlanNode,
        scores: dict[str, float],
        code_commit: str,
        data_hash: str,
        seed: int,
        logical_time: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO runs(
              id,thesis_id,plan_version,node_id,node_kind,executor,cost,status,
              scores,code_commit,data_hash,seed
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run_id,
                thesis_id,
                plan_version,
                node.id,
                node.kind,
                node.executor,
                node.cost,
                "COMPLETED",
                json.dumps(scores, sort_keys=True),
                code_commit,
                data_hash,
                seed,
            ),
        )
        self.connection.commit()
        self.event(
            logical_time,
            "RUN_COMPLETED",
            thesis_id,
            {"run_id": run_id, "node": node.id, "scores": scores},
        )

    def decision(
        self,
        *,
        thesis_id: str,
        run_id: str | None,
        verdict: str,
        predicate: str,
        evidence: dict[str, Any],
        logical_time: str,
        confirmer: str | None = None,
        reason: str | None = None,
    ) -> int:
        cursor = self.connection.execute(
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
                json.dumps(evidence, sort_keys=True),
                confirmer,
                reason,
            ),
        )
        self.connection.commit()
        self.event(
            logical_time,
            "DECISION",
            thesis_id,
            {
                "decision_id": cursor.lastrowid,
                "run_id": run_id,
                "verdict": verdict,
                "confirmer": confirmer,
                "reason": reason,
            },
        )
        return int(cursor.lastrowid)

    def rows(self, table: str) -> list[sqlite3.Row]:
        allowed = {
            "events",
            "theses",
            "plans",
            "runs",
            "decisions",
            "budget_entries",
        }
        if table not in allowed:
            raise ValueError(table)
        return list(self.connection.execute(f"SELECT * FROM {table}"))  # noqa: S608


class LocalExecutor:
    """Execute a deterministic paired-measurement experiment."""

    def execute(self, node: PlanNode) -> tuple[dict[str, float], str, int]:
        if node.executor != "local":
            raise ValueError(f"local executor cannot run: {node.executor}")
        control = [float(value) for value in node.params.get("control", [])]
        treatment = [float(value) for value in node.params.get("treatment", [])]
        if not control or len(control) != len(treatment):
            raise ValueError("paired experiment needs equal non-empty samples")
        differences = [
            treatment_value - control_value
            for control_value, treatment_value in zip(
                control, treatment, strict=True
            )
        ]
        delta = statistics.fmean(differences)
        if len(differences) == 1:
            standard_error = 0.0
        else:
            standard_error = statistics.stdev(differences) / math.sqrt(
                len(differences)
            )
        margin = 1.96 * standard_error
        scores = {
            "control_mean": statistics.fmean(control),
            "treatment_mean": statistics.fmean(treatment),
            "delta": delta,
            "ci_lower": delta - margin,
            "ci_upper": delta + margin,
            "n": float(len(differences)),
        }
        canonical = json.dumps(node.params, sort_keys=True).encode()
        return scores, hashlib.sha256(canonical).hexdigest(), int(
            node.params.get("seed", 0)
        )


class Portfolio:
    """Human-gated, falsifier-first portfolio scheduler."""

    def __init__(
        self,
        ledger: Ledger,
        total_budget: float,
        reports_dir: str | Path,
        *,
        code_commit: str = "bundled-demo-v1",
    ):
        if total_budget <= 0:
            raise ValueError("portfolio budget must be positive")
        self.ledger = ledger
        self.total_budget = total_budget
        self.remaining = total_budget
        self.reports_dir = Path(reports_dir)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.code_commit = code_commit
        self.plans: dict[str, Plan] = {}
        self.theses: dict[str, Thesis] = {}
        self.executor = LocalExecutor()
        self._clock = 0

    def _time(self) -> str:
        self._clock += 1
        return f"2026-07-24T09:{self._clock:02d}:00Z"

    def register(self, thesis: Thesis, plan: Plan) -> None:
        if thesis.id != plan.thesis_id:
            raise ValueError("plan and thesis IDs differ")
        self.ledger.register_thesis(thesis, self._time())
        self.ledger.save_plan(plan, self._time())
        self.theses[thesis.id] = thesis
        self.plans[thesis.id] = plan

    def approve_plan(self, thesis_id: str, approver: str) -> None:
        plan = self.plans[thesis_id]
        self.ledger.approve_plan(thesis_id, plan.version, approver, self._time())
        plan.approved_by = approver

    def run(self, *, confirmer: str | None = None) -> dict[str, Any]:
        for thesis_id, plan in self.plans.items():
            if not self.ledger.plan_is_approved(thesis_id, plan.version):
                raise RuntimeError(f"unapproved plan cannot run: {thesis_id}")

        falsifiers = sorted(
            (
                (thesis_id, plan, node)
                for thesis_id, plan in self.plans.items()
                for node in plan.nodes
                if node.kind == "falsifier"
            ),
            key=lambda item: (item[2].cost, item[0], item[2].id),
        )
        fair_floor = sum(node.cost for _, _, node in falsifiers)
        if fair_floor > self.remaining:
            raise RuntimeError(
                f"budget {self.remaining:g} cannot fund falsifier fair floor "
                f"{fair_floor:g}"
            )
        for thesis_id, plan, node in falsifiers:
            self._dispatch(thesis_id, plan, node, confirmer)

        remaining_nodes = sorted(
            (
                (thesis_id, plan, node)
                for thesis_id, plan in self.plans.items()
                for node in plan.nodes
                if node.kind != "falsifier"
            ),
            key=lambda item: (item[2].cost, item[0], item[2].id),
        )
        for thesis_id, plan, node in remaining_nodes:
            if self.ledger.thesis_status(thesis_id) == "KILLED":
                continue
            if node.cost > self.remaining:
                self.ledger.event(
                    self._time(),
                    "NODE_BLOCKED_BUDGET",
                    thesis_id,
                    {"node": node.id, "cost": node.cost, "remaining": self.remaining},
                )
                continue
            self._dispatch(thesis_id, plan, node, confirmer)

        for thesis_id, thesis in self.theses.items():
            if self.ledger.thesis_status(thesis_id) == "ACTIVE":
                self.ledger.set_status(thesis_id, "SURVIVED")
                self.ledger.decision(
                    thesis_id=thesis_id,
                    run_id=None,
                    verdict="PROMOTE",
                    predicate=thesis.kill_predicate,
                    evidence={"reason": "approved plan completed without a kill"},
                    logical_time=self._time(),
                )
        return self.summary()

    def _dispatch(
        self, thesis_id: str, plan: Plan, node: PlanNode, confirmer: str | None
    ) -> None:
        if node.executor != "local":
            raise RuntimeError("notebook nodes are emitted, not dispatched locally")
        if node.cost > self.remaining:
            raise RuntimeError("portfolio budget exhausted")
        self.ledger.set_status(thesis_id, "ACTIVE")
        run_id = f"{thesis_id}-v{plan.version}-{node.id}"
        self.remaining -= node.cost
        self.ledger.spend(
            thesis_id, run_id, node.cost, self.remaining, self._time()
        )
        scores, data_hash, seed = self.executor.execute(node)
        self.ledger.record_run(
            run_id=run_id,
            thesis_id=thesis_id,
            plan_version=plan.version,
            node=node,
            scores=scores,
            code_commit=self.code_commit,
            data_hash=data_hash,
            seed=seed,
            logical_time=self._time(),
        )
        if node.kind == "falsifier":
            self._evaluate_kill(thesis_id, run_id, scores, confirmer)

    def _evaluate_kill(
        self,
        thesis_id: str,
        run_id: str,
        scores: dict[str, float],
        confirmer: str | None,
    ) -> None:
        thesis = self.theses[thesis_id]
        predicate = SafePredicate(thesis.kill_predicate)
        try:
            fired = predicate.evaluate(scores)
        except (PredicateUndetermined, PredicateError) as exc:
            self.ledger.decision(
                thesis_id=thesis_id,
                run_id=run_id,
                verdict="UNDETERMINED",
                predicate=thesis.kill_predicate,
                evidence={"scores": scores, "error": str(exc)},
                logical_time=self._time(),
            )
            self.ledger.set_status(thesis_id, "UNDETERMINED")
            return
        if not fired:
            self.ledger.decision(
                thesis_id=thesis_id,
                run_id=run_id,
                verdict="CONTINUE",
                predicate=thesis.kill_predicate,
                evidence={"scores": scores},
                logical_time=self._time(),
            )
            return
        self.ledger.decision(
            thesis_id=thesis_id,
            run_id=run_id,
            verdict="PENDING_KILL",
            predicate=thesis.kill_predicate,
            evidence={"scores": scores},
            logical_time=self._time(),
        )
        self.ledger.set_status(thesis_id, "PENDING_KILL")
        if confirmer is not None:
            self.confirm_kill(thesis_id, run_id, scores, confirmer)

    def confirm_kill(
        self,
        thesis_id: str,
        run_id: str,
        scores: dict[str, float],
        confirmer: str,
    ) -> None:
        if self.ledger.thesis_status(thesis_id) != "PENDING_KILL":
            raise RuntimeError(f"thesis is not pending kill: {thesis_id}")
        thesis = self.theses[thesis_id]
        self.ledger.decision(
            thesis_id=thesis_id,
            run_id=run_id,
            verdict="KILL",
            predicate=thesis.kill_predicate,
            evidence={"scores": scores, "limitations": thesis.limitations},
            logical_time=self._time(),
            confirmer=confirmer,
        )
        self.ledger.set_status(thesis_id, "KILLED")
        self.ledger.release_unused(thesis_id, self.remaining, self._time())
        self._write_kill_report(thesis, run_id, scores, confirmer)

    def revive(self, thesis_id: str, reason: str, actor: str) -> None:
        if self.ledger.thesis_status(thesis_id) != "KILLED":
            raise RuntimeError(f"only killed theses can be revived: {thesis_id}")
        thesis = self.theses.get(thesis_id)
        predicate = thesis.kill_predicate if thesis else "<loaded from ledger>"
        self.ledger.set_status(thesis_id, "REVIVED")
        self.ledger.decision(
            thesis_id=thesis_id,
            run_id=None,
            verdict="REVIVE",
            predicate=predicate,
            evidence={"challenge": reason},
            logical_time=self._time(),
            confirmer=actor,
            reason=reason,
        )

    def _write_kill_report(
        self,
        thesis: Thesis,
        run_id: str,
        scores: dict[str, float],
        confirmer: str,
    ) -> None:
        limitations = "\n".join(
            f"- {item}" for item in thesis.limitations
        ) or "- No limitations were registered; this is itself a limitation."
        score_lines = "\n".join(
            f"- `{key}`: {value:.6f}" for key, value in sorted(scores.items())
        )
        report = f"""# Kill report — {thesis.id}

## Decision

**KILLED**, confirmed by `{confirmer}`. This decision is reversible with
`flotilla revive {thesis.id}`.

## Registered thesis

{thesis.prediction}

## Evidence

- Run ID: `{run_id}`
{score_lines}

The exact deterministic predicate that fired was:

```text
{thesis.kill_predicate}
```

## Conditions under which this kill may be wrong

{limitations}

This fixture report demonstrates decision provenance. It is not evidence for a
general false-kill accuracy claim.
"""
        (self.reports_dir / f"{thesis.id}-kill.md").write_text(
            report, encoding="utf-8"
        )

    def summary(self) -> dict[str, Any]:
        rows = self.ledger.rows("theses")
        statuses: dict[str, int] = {}
        for row in rows:
            statuses[str(row["status"])] = statuses.get(str(row["status"]), 0) + 1
        undetermined = sum(
            1
            for row in self.ledger.rows("decisions")
            if row["verdict"] == "UNDETERMINED"
        )
        return {
            "theses": len(rows),
            "statuses": statuses,
            "spent": round(self.total_budget - self.remaining, 6),
            "remaining": round(self.remaining, 6),
            "undetermined_decisions": undetermined,
            "runs": len(self.ledger.rows("runs")),
        }


def emit_notebook_job(node: PlanNode, output: str | Path) -> Path:
    """Emit a minimal standard-library notebook for Kaggle/Colab execution."""

    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    source = [
        "import math, statistics\n",
        f"control = {node.params.get('control', [])!r}\n",
        f"treatment = {node.params.get('treatment', [])!r}\n",
        "diffs = [b-a for a,b in zip(control,treatment)]\n",
        "delta = statistics.fmean(diffs)\n",
        "se = statistics.stdev(diffs)/math.sqrt(len(diffs)) if len(diffs)>1 else 0\n",
        "print({'delta': delta, 'ci_lower': delta-1.96*se, "
        "'ci_upper': delta+1.96*se})\n",
    ]
    notebook = {
        "cells": [
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": [
                    "# FLOTILLA emitted job\n",
                    "Generated locally; upload to Kaggle or Colab explicitly.\n",
                ],
            },
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": source,
            },
        ],
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "flotilla": {"node_id": node.id, "executor": "notebook"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    target.write_text(json.dumps(notebook, indent=2) + "\n", encoding="utf-8")
    return target


def render_dashboard(ledger: Ledger, output: str | Path) -> Path:
    """Render the ledger as an editorial research portfolio chart."""

    thesis_rows = ledger.rows("theses")
    runs = ledger.rows("runs")
    events = ledger.rows("events")
    decisions = ledger.rows("decisions")
    budget_entries = ledger.rows("budget_entries")
    undetermined = sum(row["verdict"] == "UNDETERMINED" for row in decisions)
    total_spend = sum(float(row["spent"]) for row in thesis_rows)
    remaining = (
        float(budget_entries[-1]["remaining"]) if budget_entries else 0.0
    )
    portfolio_budget = total_spend + remaining
    released = sum(
        float(row["amount"]) for row in budget_entries if row["action"] == "RELEASE"
    )
    falsifier_spend = sum(
        float(row["cost"]) for row in runs if row["node_kind"] == "falsifier"
    )
    followup_spend = total_spend - falsifier_spend
    statuses = {
        status: sum(str(row["status"]) == status for row in thesis_rows)
        for status in ("SURVIVED", "KILLED", "REVIVED", "UNDETERMINED")
    }
    colors = {
        "KILLED": "dead",
        "SURVIVED": "alive",
        "REVIVED": "revived",
        "UNDETERMINED": "unknown",
    }

    def runs_for(thesis_id: str) -> list[sqlite3.Row]:
        return [row for row in runs if row["thesis_id"] == thesis_id]

    def decisions_for(thesis_id: str) -> list[sqlite3.Row]:
        return [row for row in decisions if row["thesis_id"] == thesis_id]

    allocation_segments = "".join(
        "<span "
        f"class='{colors.get(str(row['status']), '')}' "
        f"style='width:{(float(row['spent']) / portfolio_budget * 100) if portfolio_budget else 0:.2f}%' "
        f"title='{html.escape(str(row['id']))}: {float(row['spent']):.1f} units'>"
        f"<span class='sr-only'>{html.escape(str(row['id']))}: "
        f"{float(row['spent']):.1f} units</span></span>"
        for row in thesis_rows
    )
    if remaining:
        allocation_segments += (
            "<span class='reserve' "
            f"style='width:{remaining / portfolio_budget * 100:.2f}%' "
            f"title='Uncommitted reserve: {remaining:.1f} units'>"
            f"<span class='sr-only'>Uncommitted reserve: {remaining:.1f} units"
            "</span></span>"
        )

    capital_rows = "\n".join(
        "<li>"
        f"<span class='capital-name'>{html.escape(str(row['id']))}</span>"
        "<span class='capital-soundings' aria-hidden='true'>"
        f"<i class='{colors.get(str(row['status']), '')}' "
        f"style='width:{(float(row['spent']) / portfolio_budget * 100) if portfolio_budget else 0:.2f}%'></i>"
        "</span>"
        f"<strong>{float(row['spent']):.1f}</strong>"
        "</li>"
        for row in thesis_rows
    )
    capital_rows += (
        "<li><span class='capital-name'>Reserve</span>"
        "<span class='capital-soundings' aria-hidden='true'>"
        f"<i class='reserve' style='width:{remaining / portfolio_budget * 100 if portfolio_budget else 0:.2f}%'></i>"
        f"</span><strong>{remaining:.1f}</strong></li>"
    )

    voyage_cards: list[str] = []
    for row in thesis_rows:
        thesis_id = str(row["id"])
        status = str(row["status"])
        thesis_runs = runs_for(thesis_id)
        thesis_decisions = decisions_for(thesis_id)
        route_steps = [
            "<li class='route-step done'><b>Registered</b><span>Thesis v"
            f"{int(row['version'])}</span></li>"
        ]
        route_steps.extend(
            "<li class='route-step done'>"
            f"<b>{html.escape(str(run['node_kind']).title())}</b>"
            f"<span>{float(run['cost']):.1f}u · seed {int(run['seed'])}</span>"
            "</li>"
            for run in thesis_runs
        )
        route_steps.append(
            f"<li class='route-step terminal {colors.get(status, '')}'>"
            f"<b>{html.escape(status.title())}</b>"
            f"<span>{'Challengeable stop' if status == 'KILLED' else 'Course retained'}</span>"
            "</li>"
        )
        limitation_items = "".join(
            f"<li>{html.escape(str(item))}</li>"
            for item in json.loads(str(row["limitations"]))
        )
        run_items = "".join(
            "<li><div><strong>"
            f"{html.escape(str(run['node_id']))}</strong> · "
            f"{html.escape(str(run['node_kind']))} · {float(run['cost']):.1f}u"
            "</div><code>"
            f"commit {html.escape(str(run['code_commit']))} · "
            f"data {html.escape(str(run['data_hash']))} · seed {int(run['seed'])}"
            "</code></li>"
            for run in thesis_runs
        )
        decision_items = "".join(
            "<li><strong>"
            f"{html.escape(str(decision['verdict']))}</strong>"
            f"<span>run {html.escape(str(decision['run_id'] or 'portfolio'))}"
            f" · confirmer {html.escape(str(decision['confirmer'] or 'none'))}</span>"
            f"<code>{html.escape(str(decision['predicate']))}</code></li>"
            for decision in thesis_decisions
        )
        decision_sequence = " → ".join(
            html.escape(str(decision["verdict"])) for decision in thesis_decisions
        )
        if not decision_sequence:
            decision_sequence = "No decision recorded"
        challenge = {
            "KILLED": "Stop recorded. REVIVE remains available without erasing this lineage.",
            "REVIVED": "A prior stop was challenged; the original decision remains in lineage.",
            "SURVIVED": "Promoted after its approved plan completed without a kill.",
            "UNDETERMINED": "Evidence was insufficient; no directional claim was inferred.",
        }.get(status, "Plan remains in progress.")
        voyage_cards.append(
            f"""<article class="voyage {colors.get(status, '')}" aria-labelledby="title-{thesis_id}">
<header class="voyage-head"><div><span class="signal">{html.escape(thesis_id)}</span>
<h3 id="title-{thesis_id}">{html.escape(str(row['title']))}</h3></div>
<span class="status {colors.get(status, '')}">{html.escape(status)}</span></header>
<p class="prediction">{html.escape(str(row['prediction']))}</p>
<ol class="route" aria-label="{html.escape(thesis_id)} experiment trajectory">
{''.join(route_steps)}</ol>
<div class="voyage-foot"><span><b>{float(row['spent']):.1f}</b> / {float(row['budget_cap']):.1f} units</span>
<span class="sequence">{decision_sequence}</span></div>
<details class="dossier"><summary>Open evidence dossier</summary>
<div class="dossier-grid"><section><h4>Registered rule</h4>
<code class="predicate">{html.escape(str(row['kill_predicate']))}</code>
<p>Decision deadline<br><strong>{html.escape(str(row['deadline']))}</strong></p>
<p>{html.escape(challenge)}</p><h4>Conditions under which this may be wrong</h4>
<ul>{limitation_items}</ul></section><section><h4>Run lineage</h4>
<ol class="evidence-list">{run_items}</ol><h4>Decision lineage</h4>
<ol class="evidence-list">{decision_items}</ol></section></div></details></article>"""
        )

    snapshot_theses: list[dict[str, Any]] = []
    for row in thesis_rows:
        thesis_id = str(row["id"])
        thesis_runs = runs_for(thesis_id)
        falsifier = next(
            (run for run in thesis_runs if run["node_kind"] == "falsifier"),
            None,
        )
        followup_cost = sum(
            float(run["cost"])
            for run in thesis_runs
            if run["node_kind"] != "falsifier"
        )
        snapshot_theses.append(
            {
                "id": thesis_id,
                "title": str(row["title"]),
                "prediction": str(row["prediction"]),
                "predicate": str(row["kill_predicate"]),
                "budget_cap": float(row["budget_cap"]),
                "registered_status": str(row["status"]),
                "registered_spend": float(row["spent"]),
                "deadline": str(row["deadline"]),
                "limitations": json.loads(str(row["limitations"])),
                "falsifier_scores": (
                    json.loads(str(falsifier["scores"])) if falsifier else {}
                ),
                "falsifier_cost": float(falsifier["cost"]) if falsifier else 1.0,
                "followup_cost": followup_cost or 2.0,
            }
        )
    snapshot = {
        "schema": "flotilla.browser-demo.v1",
        "portfolio_budget": portfolio_budget,
        "registered_spend": total_spend,
        "registered_remaining": remaining,
        "theses": snapshot_theses,
    }
    snapshot_json = json.dumps(snapshot, sort_keys=True).replace("</", "<\\/")
    thesis_options = "".join(
        f"<option value='{html.escape(item['id'])}'>{html.escape(item['id'])} · "
        f"{html.escape(item['title'])}</option>"
        for item in snapshot_theses
    )
    first_thesis = snapshot_theses[0] if snapshot_theses else {
        "id": "none",
        "title": "No thesis",
        "prediction": "No thesis registered.",
        "predicate": "UNDETERMINED",
    }

    thesis_html = "\n".join(
        "<tr>"
        f"<td>{html.escape(str(row['id']))}</td>"
        f"<td>{html.escape(str(row['title']))}</td>"
        f"<td><span class='status {colors.get(str(row['status']), '')}'>"
        f"{html.escape(str(row['status']))}</span></td>"
        f"<td>{float(row['spent']):.1f} / {float(row['budget_cap']):.1f}</td>"
        f"<td><code>{html.escape(str(row['kill_predicate']))}</code></td>"
        "</tr>"
        for row in thesis_rows
    )
    event_html = "\n".join(
        "<li>"
        f"<span class='log-sequence'>{int(row['sequence']):02d}</span>"
        "<span class='log-time'>"
        f"{html.escape(str(row['logical_time'])[11:16])}</span>"
        f"<strong>{html.escape(str(row['kind']).replace('_', ' ').title())}</strong>"
        f"<span>{html.escape(str(row['thesis_id'] or 'portfolio'))}</span>"
        "</li>"
        for row in events
    )
    document = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="FLOTILLA's deterministic five-thesis research portfolio and capital-allocation record.">
<title>FLOTILLA — research portfolio chart</title>
<link rel="stylesheet" href="../assets/site.css">
<style>
:root{{--paper:#f4f0e5;--paper-deep:#e8dfca;--ink:#17333b;--ink-soft:#466069;
--rule:#aab3a8;--rule-dark:#6e7f7d;--sea:#1f6766;--sea-pale:#cddfda;
--stop:#a5432f;--stop-pale:#edd4c8;--revive:#6d527d;--revive-pale:#ded2e2;
--amber:#a77a2a;--reserve:#c9b98f;--white:#fffdf6;--shadow:0 18px 42px rgba(35,50,48,.1)}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;color:var(--ink);
background-color:var(--paper);background-image:linear-gradient(rgba(42,83,84,.055) 1px,transparent 1px),
linear-gradient(90deg,rgba(42,83,84,.055) 1px,transparent 1px);
background-size:32px 32px;font:15px/1.55 "Avenir Next",Avenir,"Segoe UI",sans-serif}}
a{{color:inherit}}.skip{{position:absolute;left:1rem;top:-5rem;background:var(--ink);
color:white;padding:.6rem 1rem;z-index:20}}.skip:focus{{top:1rem}}.sr-only{{position:absolute;
width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);
white-space:nowrap;border:0}}.masthead{{border-bottom:1px solid var(--ink);
background:rgba(244,240,229,.94)}}.masthead-inner{{max-width:1480px;margin:auto;padding:18px 32px;
display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:24px}}.folio,.dateline{{
font:700 10px/1.3 ui-monospace,SFMono-Regular,Menlo,monospace;letter-spacing:.13em;
text-transform:uppercase;color:var(--ink-soft)}}.dateline{{text-align:right}}.wordmark{{
font:700 24px/1 Georgia,serif;letter-spacing:.22em}}.subnav{{border-bottom:1px solid var(--rule);
background:rgba(244,240,229,.92)}}.subnav nav{{max-width:1480px;margin:auto;padding:9px 32px;
display:flex;gap:24px;justify-content:center;flex-wrap:wrap}}.subnav a{{font-size:11px;
font-weight:700;letter-spacing:.08em;text-decoration:none;text-transform:uppercase}}.subnav a:focus-visible,
summary:focus-visible{{outline:3px solid var(--amber);outline-offset:3px}}main{{max-width:1480px;
margin:auto;padding:34px 32px 72px}}.hero{{display:grid;grid-template-columns:minmax(0,1.65fr)
minmax(330px,.7fr);gap:38px;padding:20px 0 38px;border-bottom:3px double var(--ink)}}.overline,
.section-kicker{{margin:0 0 12px;color:var(--stop);font:800 10px ui-monospace,
SFMono-Regular,Menlo,monospace;text-transform:uppercase;letter-spacing:.17em}}h1,h2,h3,h4{{
font-family:Georgia,"Times New Roman",serif}}h1{{font-size:clamp(50px,7.7vw,116px);
font-weight:500;line-height:.82;letter-spacing:-.065em;margin:0;max-width:1020px}}.deck{{
font:20px/1.45 Georgia,serif;max-width:780px;margin:28px 0 22px;color:#2e4a51}}.thesis{{
max-width:760px;padding:15px 0 0 62px;border-top:1px solid var(--rule);position:relative;
color:var(--ink-soft)}}.thesis:before{{content:"N";position:absolute;left:5px;top:13px;width:34px;
height:34px;border:1px solid var(--ink);border-radius:50%;display:grid;place-items:center;
font:700 11px Georgia,serif}}.thesis:after{{content:"";position:absolute;left:21px;top:7px;
width:1px;height:46px;background:var(--ink);transform:rotate(24deg)}}.manifest{{
border:1px solid var(--ink);background:rgba(255,253,246,.66);box-shadow:var(--shadow)}}.manifest-head{{
padding:18px 20px;border-bottom:1px solid var(--ink);display:flex;justify-content:space-between;
align-items:end}}.manifest-head h2{{font-size:25px;font-weight:500;margin:0}}.manifest-head span{{
font:700 10px ui-monospace,monospace;text-transform:uppercase;letter-spacing:.12em}}.manifest-total{{
display:grid;grid-template-columns:1fr 1fr;border-bottom:1px solid var(--rule)}}.manifest-total div{{
padding:18px 20px}}.manifest-total div+div{{border-left:1px solid var(--rule)}}.manifest-total strong{{
display:block;font:500 42px/1 Georgia,serif}}.manifest-total span{{font-size:10px;text-transform:uppercase;
letter-spacing:.1em;color:var(--ink-soft)}}.allocation{{height:18px;display:flex;margin:20px;
border:1px solid var(--ink);background:var(--paper-deep)}}.allocation>span{{display:block;height:100%;
border-right:2px solid var(--white)}}.allocation .alive,.capital-soundings .alive{{background:var(--sea)}}
.allocation .dead,.capital-soundings .dead{{background:var(--stop)}}.allocation .revived,
.capital-soundings .revived{{background:var(--revive)}}.allocation .unknown,
.capital-soundings .unknown{{background:var(--amber)}}.allocation .reserve,.capital-soundings .reserve{{
background:repeating-linear-gradient(135deg,var(--reserve),var(--reserve) 4px,
var(--paper) 4px,var(--paper) 8px)}}.capital-list{{list-style:none;margin:0;padding:0 20px 14px}}.capital-list li{{
display:grid;grid-template-columns:62px 1fr 30px;gap:9px;align-items:center;margin:8px 0;
font:700 10px ui-monospace,monospace;text-transform:uppercase}}.capital-soundings{{height:5px;
background:var(--paper-deep);display:block}}.capital-soundings i{{display:block;height:100%}}.manifest-note{{
padding:13px 20px;border-top:1px solid var(--rule);font-size:12px;color:var(--ink-soft);
margin:0}}.briefing{{display:grid;grid-template-columns:repeat(6,1fr);border-bottom:1px solid var(--ink);
margin-bottom:40px}}.briefing div{{padding:17px 16px;border-right:1px solid var(--rule)}}.briefing div:last-child{{
border-right:0}}.briefing strong{{display:block;font:500 30px/1 Georgia,serif}}.briefing span{{
font-size:9px;font-weight:800;letter-spacing:.1em;text-transform:uppercase;color:var(--ink-soft)}}
.section-head{{display:grid;grid-template-columns:1fr minmax(280px,.48fr);gap:32px;align-items:end;
margin-bottom:20px}}.section-head h2{{font-size:clamp(32px,4vw,58px);line-height:.98;font-weight:500;
letter-spacing:-.035em;margin:0}}.section-head p:last-child{{margin:0;color:var(--ink-soft);
border-left:1px solid var(--rule);padding-left:20px}}.strategy-table{{border-top:1px solid var(--ink);
margin-bottom:52px}}.voyage{{padding:24px 0 0;border-bottom:1px solid var(--ink)}}.voyage-head{{
display:grid;grid-template-columns:minmax(0,1fr) auto;gap:20px;align-items:start}}.voyage-head>div{{
display:grid;grid-template-columns:74px minmax(0,1fr);gap:18px;align-items:baseline}}.signal{{
font:800 11px ui-monospace,monospace;letter-spacing:.1em;color:var(--stop)}}.voyage h3{{
font-size:clamp(23px,2.3vw,36px);line-height:1.05;font-weight:500;margin:0}}.status{{
display:inline-block;padding:5px 8px;border:1px solid currentColor;font:800 9px ui-monospace,
monospace;letter-spacing:.09em;text-transform:uppercase;background:var(--white)}}.status.alive{{
color:var(--sea)}}.status.dead{{color:var(--stop)}}.status.revived{{color:var(--revive)}}
.status.unknown{{color:var(--amber)}}.prediction{{margin:12px 0 17px 92px;max-width:880px;
font-family:Georgia,serif;color:var(--ink-soft)}}.route{{list-style:none;margin:0 0 0 92px;
padding:0 0 20px;display:flex;position:relative}}.route:before{{content:"";position:absolute;
left:7px;right:7px;top:8px;border-top:1px dashed var(--rule-dark)}}.route-step{{flex:1;min-width:120px;
position:relative;padding:22px 14px 0 0}}.route-step:before{{content:"";position:absolute;top:2px;
left:0;width:11px;height:11px;border:2px solid var(--ink);border-radius:50%;background:var(--paper)}}
.route-step.done:before{{background:var(--ink)}}.route-step.terminal.alive:before{{background:var(--sea);
border-color:var(--sea)}}.route-step.terminal.dead:before{{background:var(--stop);border-color:var(--stop)}}
.route-step.terminal.revived:before{{background:var(--revive);border-color:var(--revive)}}
.route-step.terminal.unknown:before{{background:var(--amber);border-color:var(--amber)}}
.route-step b{{display:block;font-size:11px;text-transform:uppercase;letter-spacing:.06em}}
.route-step span{{display:block;font-size:11px;color:var(--ink-soft)}}.voyage-foot{{display:grid;
grid-template-columns:170px 1fr;gap:20px;margin-left:92px;padding:12px 0;border-top:1px dotted var(--rule);
font-size:11px;color:var(--ink-soft)}}.voyage-foot b{{font:500 20px Georgia,serif;color:var(--ink)}}
.sequence{{font:700 10px ui-monospace,monospace;text-align:right;text-transform:uppercase;
letter-spacing:.04em}}.dossier{{margin-left:92px;border-top:1px dotted var(--rule)}}.dossier summary{{
cursor:pointer;width:max-content;padding:11px 0;font-size:11px;font-weight:800;text-transform:uppercase;
letter-spacing:.08em}}.dossier summary::marker{{color:var(--stop)}}.dossier-grid{{display:grid;
grid-template-columns:minmax(240px,.75fr) minmax(0,1.25fr);gap:34px;padding:8px 0 26px}}.dossier h4{{
font:800 10px ui-monospace,monospace;text-transform:uppercase;letter-spacing:.1em;margin:10px 0}}
.dossier p,.dossier li{{font-size:12px;color:var(--ink-soft)}}.predicate{{display:block;
padding:10px;background:var(--paper-deep);overflow-wrap:anywhere}}.evidence-list{{list-style:none;
margin:0;padding:0}}.evidence-list li{{padding:7px 0;border-bottom:1px dotted var(--rule)}}
.evidence-list span,.evidence-list code{{display:block;overflow-wrap:anywhere}}code{{font:10px/1.5
ui-monospace,SFMono-Regular,Menlo,monospace}}.flow{{display:grid;grid-template-columns:1fr 1fr;
gap:38px;margin:34px 0 52px}}.flow-chart{{border:1px solid var(--ink);background:rgba(255,253,246,.55);
padding:25px}}.flow-chart h3{{font-size:27px;font-weight:500;margin:0 0 22px}}.flow-stage{{
display:grid;grid-template-columns:112px 1fr 48px;align-items:center;gap:12px;margin:14px 0}}
.flow-stage span{{font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.06em}}
.flow-stage i{{height:16px;display:block;background:var(--sea-pale);position:relative}}.flow-stage i:after{{
content:"";position:absolute;left:0;top:0;bottom:0;width:var(--flow);background:var(--sea)}}
.flow-stage strong{{font:500 20px Georgia,serif}}.flow-copy{{padding:24px 0;border-top:3px double var(--ink);
border-bottom:3px double var(--ink)}}.flow-copy blockquote{{font:500 clamp(24px,3vw,43px)/1.1
Georgia,serif;margin:0 0 20px}}.flow-copy p{{color:var(--ink-soft)}}.register{{margin-top:30px}}
.register>details{{border-top:1px solid var(--ink);border-bottom:1px solid var(--ink)}}.register summary,
.logbook summary{{cursor:pointer;padding:16px 0;display:flex;justify-content:space-between;gap:20px;
font:700 12px ui-monospace,monospace;text-transform:uppercase;letter-spacing:.07em}}.scroll{{
overflow:auto}}table{{width:100%;border-collapse:collapse;font-size:12px;background:rgba(255,253,246,.4)}}
th,td{{text-align:left;padding:11px 13px;border-top:1px solid var(--rule);vertical-align:top}}
th{{font:800 9px ui-monospace,monospace;text-transform:uppercase;letter-spacing:.09em}}
.logbook{{margin-top:18px;border-bottom:1px solid var(--ink)}}.ship-log{{list-style:none;
margin:0;padding:0;display:grid;grid-template-columns:repeat(2,1fr);border-top:1px solid var(--rule)}}
.ship-log li{{display:grid;grid-template-columns:34px 42px 1fr 62px;gap:8px;padding:8px 10px;
border-bottom:1px dotted var(--rule);font-size:10px}}.ship-log li:nth-child(odd){{
border-right:1px solid var(--rule)}}.log-sequence,.log-time{{font-family:ui-monospace,monospace;
color:var(--ink-soft)}}.ship-log strong{{font-size:10px}}footer{{display:flex;
justify-content:space-between;gap:24px;margin-top:34px;padding-top:16px;border-top:3px double var(--ink);
font-size:11px;color:var(--ink-soft)}}@media(max-width:1050px){{.briefing{{
grid-template-columns:repeat(3,1fr)}}.briefing div:nth-child(3){{border-right:0}}.hero{{
grid-template-columns:1fr}}.manifest{{max-width:660px}}.flow{{grid-template-columns:1fr}}}}
@media(max-width:760px){{.masthead-inner{{padding:14px 18px;grid-template-columns:1fr auto}}
.folio{{display:none}}.dateline{{font-size:8px}}.subnav nav{{justify-content:flex-start;padding:9px 18px;
overflow:auto;flex-wrap:nowrap}}main{{padding:25px 18px 50px}}.hero{{gap:24px}}h1{{font-size:55px}}
.briefing{{grid-template-columns:repeat(2,1fr)}}.briefing div:nth-child(3){{border-right:1px solid var(--rule)}}
.briefing div:nth-child(even){{border-right:0}}.section-head{{grid-template-columns:1fr}}.section-head p:last-child{{
border-left:0;border-top:1px solid var(--rule);padding:12px 0 0}}.voyage-head>div{{
grid-template-columns:1fr}}.prediction,.route,.voyage-foot,.dossier{{margin-left:0}}.route{{display:grid;
grid-template-columns:1fr 1fr}}.route:before{{display:none}}.route-step{{border-top:1px dashed var(--rule);
padding:13px 8px 13px 23px}}.route-step:before{{top:14px}}.voyage-foot{{grid-template-columns:1fr}}
.sequence{{text-align:left}}.dossier-grid{{grid-template-columns:1fr}}.ship-log{{grid-template-columns:1fr}}
.ship-log li:nth-child(odd){{border-right:0}}footer{{flex-direction:column}}}}
@media(max-width:430px){{h1{{font-size:47px}}.manifest-total{{grid-template-columns:1fr}}
.manifest-total div+div{{border-left:0;border-top:1px solid var(--rule)}}.briefing{{grid-template-columns:1fr}}
.briefing div{{border-right:0}}.route{{grid-template-columns:1fr}}}}
@media(prefers-reduced-motion:reduce){{html{{scroll-behavior:auto}}}}
@media print{{.subnav,.skip{{display:none}}body{{background:white}}.dossier{{display:block}}
.dossier>div{{display:grid}}}}
</style>
</head>
<body class="demo-page"><a class="skip" href="#simulator">Skip to interactive simulator</a>
<header class="masthead"><div class="masthead-inner"><span class="folio">Portfolio record · No. 001</span>
<div class="wordmark">FLOTILLA</div><span class="dateline">24 July 2026 · Journey 0</span>
</div></header><div class="subnav"><nav aria-label="Report sections">
<a href="../">Product</a><a href="#simulator">Simulator</a><a href="#capital">Capital allocation</a>
<a href="#portfolio">Thesis trajectories</a>
<a href="#lineage">Decision timeline</a></nav></div><main>
<section class="hero" aria-labelledby="report-title"><div><p class="overline">Research portfolio memorandum</p>
<h1 id="report-title">Five theses. One budget.</h1>
<p class="deck">A falsifier-first strategy table for deciding what earns another experiment—and
what returns its capital to the fleet.</p><p class="thesis">The model proposes. Executable
predicates decide. Operators confirm every stop. Kills remain reversible, with the original
evidence and lineage intact.</p></div>
<aside class="manifest" id="capital" aria-labelledby="capital-title">
<div class="manifest-head"><h2 id="capital-title">Capital allocation</h2>
<span>12-unit mandate</span></div><div class="manifest-total">
<div><strong>{total_spend:.1f}</strong><span>units deployed</span></div>
<div><strong>{remaining:.1f}</strong><span>held in reserve</span></div></div>
<div class="allocation" aria-label="Budget allocation by thesis">{allocation_segments}</div>
<ol class="capital-list">{capital_rows}</ol>
<p class="manifest-note">{released:.1f} units of unused thesis capacity released after confirmed
stops. Allocation reflects actual ledger spend, not initial caps.</p></aside></section>
<section class="briefing" aria-label="Portfolio summary">
<div><strong>{len(thesis_rows)}</strong><span>Theses charted</span></div>
<div><strong>{len(runs)}</strong><span>Runs completed</span></div>
<div><strong>{statuses['SURVIVED']}</strong><span>Courses retained</span></div>
<div><strong>{statuses['KILLED']}</strong><span>Stops confirmed</span></div>
<div><strong>{len(decisions)}</strong><span>Decisions recorded</span></div>
<div><strong>{undetermined}</strong><span>Undetermined</span></div></section>
<section class="simulator" id="simulator" aria-labelledby="simulator-title">
<div class="simulator-heading"><div><p class="section-kicker">Deterministic browser lab</p>
<h2 id="simulator-title">Interactive strategy simulator</h2></div>
<p>Temporary in-memory data only. Choose conditions, set the mandate, launch the
falsifier fleet, step experiments, adjudicate stops, reallocate reserve, and inspect
the lineage. Reset returns to the registered fixture.</p></div>
<div class="simulator-grid"><form class="sim-controls" aria-label="Simulation controls"
onsubmit="return false"><label for="scenario-select">Scenario</label>
<select id="scenario-select"><option value="registered">Registered outcome</option>
<option value="headwinds">Replication headwinds</option>
<option value="recovery">Signal recovery</option>
<option value="thin">Thin evidence</option></select>
<label for="thesis-select">Selected thesis</label><select id="thesis-select">
{thesis_options}</select><label for="budget-control">Portfolio mandate
<output id="budget-output" for="budget-control">{portfolio_budget:.1f} units</output></label>
<input id="budget-control" type="range" min="5" max="20" step="1"
value="{portfolio_budget:.0f}"><div class="control-group">
<button id="launch-button" type="button">Launch falsifiers</button>
<button id="step-button" type="button" disabled>Advance one experiment</button></div>
<div class="control-group secondary"><button id="kill-button" type="button" disabled>
Confirm stop</button><button id="challenge-button" type="button" disabled>
Overturn stop</button><button id="revive-button" type="button" disabled>Revive thesis</button>
</div><div class="control-group secondary"><button id="reallocate-button" type="button">
Reallocate 1 unit</button><button id="reset-button" type="button">Reset</button></div>
<p class="sim-status" id="sim-status" role="status" aria-live="polite">
Ready to launch five equal-cost falsifiers.</p></form>
<div class="sim-workspace"><section class="sim-panel" aria-labelledby="trajectory-title">
<div class="sim-panel-head"><h3 id="trajectory-title">Evolving thesis trajectories</h3>
<span id="sim-clock">Round 0</span></div><div id="trajectory-graph" class="trajectory-graph"
role="img" aria-label="Current progress of all thesis experiments">
<p class="js-fallback">Enable JavaScript to operate the simulator. The registered
ledger remains fully readable below.</p></div></section>
<section class="sim-panel" aria-labelledby="allocation-title"><div class="sim-panel-head">
<h3 id="allocation-title">Live capital chart</h3><span id="reserve-readout">
{remaining:.1f} reserve</span></div><div id="allocation-graph" class="allocation-graph"
role="img" aria-label="Current portfolio spend, earmarks, and reserve"></div></section></div>
<aside class="evidence-drawer" id="evidence-drawer" aria-labelledby="evidence-title">
<p class="section-kicker">Evidence drawer</p><h3 id="evidence-title">
{html.escape(first_thesis['id'])} · {html.escape(first_thesis['title'])}</h3>
<p>{html.escape(first_thesis['prediction'])}</p><code>
{html.escape(first_thesis['predicate'])}</code>
<div id="evidence-body"><p>Select a thesis to inspect registered evidence and live
decision state.</p></div></aside></div>
<section class="sim-lineage" aria-labelledby="sim-lineage-title"><div>
<p class="section-kicker">Temporary event stream</p><h3 id="sim-lineage-title">
Simulation lineage</h3></div><ol id="interactive-lineage" aria-live="polite">
<li><span>00</span><strong>Simulator ready</strong><em>Awaiting launch</em></li></ol></section>
<noscript><p class="noscript">The interactive simulator needs JavaScript; every registered
result and append-only event is still available in the static evidence sections below.</p>
</noscript></section>
<section id="portfolio"><div class="section-head"><div><p class="section-kicker">Strategy table</p>
<h2>Thesis trajectories</h2></div><p>Each course begins with the same one-unit falsifier.
Survivors receive follow-up capital; stopped theses retain their evidence and a route to revival.</p>
</div><div class="strategy-table">{''.join(voyage_cards)}</div></section>
<section class="flow" aria-labelledby="flow-title"><div class="flow-chart"><p class="section-kicker">
Capital flow</p><h3 id="flow-title">From fair floor to concentrated follow-up</h3>
<div class="flow-stage"><span>Falsifiers</span><i style="--flow:{falsifier_spend / portfolio_budget * 100 if portfolio_budget else 0:.2f}%"></i>
<strong>{falsifier_spend:.1f}</strong></div>
<div class="flow-stage"><span>Follow-ups</span><i style="--flow:{followup_spend / portfolio_budget * 100 if portfolio_budget else 0:.2f}%"></i>
<strong>{followup_spend:.1f}</strong></div>
<div class="flow-stage"><span>Reserve</span><i style="--flow:{remaining / portfolio_budget * 100 if portfolio_budget else 0:.2f}%"></i>
<strong>{remaining:.1f}</strong></div></div><div class="flow-copy">
<blockquote>“Test broadly for one unit. Concentrate only after a thesis survives contact with evidence.”</blockquote>
<p>The first five units fund one falsifier per thesis. Six more fund three registered
follow-ups. One remains uncommitted. This fixture illustrates the allocation mechanism;
it is not scientific validation.</p></div></section>
<section class="register" id="lineage"><p class="section-kicker">Append-only evidence</p>
<h2>Decision register</h2><details><summary><span>Open thesis register</span>
<span>{len(thesis_rows)} entries · predicate + spend + disposition</span></summary>
<div class="scroll"><table><thead><tr><th>ID</th><th>Thesis</th><th>Status</th>
<th>Spend / cap</th><th>Kill predicate</th></tr></thead><tbody>{thesis_html}</tbody>
</table></div></details><details class="logbook"><summary><span>Decision timeline</span>
<span>{len(events)} append-only events</span></summary><ol class="ship-log">{event_html}</ol>
</details></section><footer><span>Generated by <code>make demo</code> from the SQLite ledger.</span>
<span>Markdown kill reports preserve argued evidence; REVIVE appends rather than erases.</span>
</footer></main><script type="application/json" id="flotilla-snapshot">{snapshot_json}</script>
<script src="../assets/flotilla-demo.js" defer></script></body></html>
"""
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(document, encoding="utf-8")
    return target
