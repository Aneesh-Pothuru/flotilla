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
    """Render a dependency-free static portfolio view."""

    thesis_rows = ledger.rows("theses")
    events = ledger.rows("events")
    decisions = ledger.rows("decisions")
    undetermined = sum(row["verdict"] == "UNDETERMINED" for row in decisions)
    total_spend = sum(float(row["spent"]) for row in thesis_rows)
    colors = {
        "KILLED": "dead",
        "SURVIVED": "alive",
        "REVIVED": "revived",
        "UNDETERMINED": "unknown",
    }
    thesis_html = "\n".join(
        "<tr>"
        f"<td>{html.escape(str(row['id']))}</td>"
        f"<td>{html.escape(str(row['title']))}</td>"
        f"<td><span class='pill {colors.get(str(row['status']), '')}'>"
        f"{html.escape(str(row['status']))}</span></td>"
        f"<td>{float(row['spent']):.1f}</td>"
        f"<td><code>{html.escape(str(row['kill_predicate']))}</code></td>"
        "</tr>"
        for row in thesis_rows
    )
    thesis_cards = "\n".join(
        "<article class='thesis-card'>"
        "<div class='thesis-top'>"
        f"<span class='thesis-id'>{html.escape(str(row['id']))}</span>"
        f"<span class='pill {colors.get(str(row['status']), '')}'>"
        f"{html.escape(str(row['status']))}</span></div>"
        f"<h3>{html.escape(str(row['title']))}</h3>"
        "<div class='thesis-meta'>"
        f"<span>SPEND <strong>{float(row['spent']):.1f}</strong></span>"
        f"<span>PREDICATE <code>{html.escape(str(row['kill_predicate']))}</code></span>"
        "</div></article>"
        for row in thesis_rows
    )
    allocation_segments = "".join(
        "<span "
        f"class='{colors.get(str(row['status']), '')}' "
        f"style='width:{(float(row['spent']) / total_spend * 100) if total_spend else 0:.2f}%' "
        f"title='{html.escape(str(row['id']))}: {float(row['spent']):.1f} units'></span>"
        for row in thesis_rows
    )
    event_html = "\n".join(
        "<tr>"
        f"<td>{row['sequence']}</td><td>{html.escape(str(row['logical_time']))}</td>"
        f"<td>{html.escape(str(row['kind']))}</td>"
        f"<td>{html.escape(str(row['thesis_id'] or 'portfolio'))}</td>"
        "</tr>"
        for row in events
    )
    document = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FLOTILLA — five-thesis demo</title>
<style>
:root{{--bg:#080b12;--panel:#10151f;--panel-2:#151b27;--line:#28303d;
--text:#f8f9f5;--muted:#929ba8;--faint:#616a78;--mint:#7cf3b4;--coral:#ff806f;
--violet:#b29bff;--amber:#f0c46b;--cyan:#6ed9e5;--shadow:0 26px 72px rgba(0,0,0,.4)}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;color:var(--text);
background:radial-gradient(circle at 85% 0,rgba(255,128,111,.12),transparent 31rem),
radial-gradient(circle at 12% 22%,rgba(124,243,180,.08),transparent 26rem),var(--bg);
font:14px/1.55 Inter,ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
.topbar{{height:58px;border-bottom:1px solid var(--line);padding:0 26px;display:flex;
align-items:center;justify-content:space-between;position:sticky;top:0;z-index:10;
background:rgba(8,11,18,.86);backdrop-filter:blur(16px)}}.brand{{display:flex;align-items:center;
gap:11px;font-weight:780}}.brand-mark{{width:27px;height:27px;border-radius:8px;
display:grid;place-items:center;background:linear-gradient(145deg,var(--coral),#ef5c81);
color:#15090b;box-shadow:0 0 22px rgba(255,128,111,.22)}}.topmeta{{display:flex;gap:14px;
align-items:center;color:var(--muted);font:10px ui-monospace,monospace;text-transform:uppercase;
letter-spacing:.11em}}.ready{{display:flex;align-items:center;gap:7px;color:#d4f8e4}}
.ready:before{{content:"";width:6px;height:6px;border-radius:50%;background:var(--mint);
box-shadow:0 0 12px var(--mint)}}main{{max-width:1380px;margin:auto;padding:30px 26px 64px}}
.hero{{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(340px,.75fr);
border:1px solid var(--line);border-radius:18px;overflow:hidden;background:linear-gradient(145deg,
rgba(22,28,40,.98),rgba(11,15,23,.98));box-shadow:var(--shadow)}}.hero-copy{{padding:36px 38px}}
.eyebrow,.section-label{{margin:0 0 13px;color:var(--coral);font:750 10px ui-monospace,
monospace;letter-spacing:.16em;text-transform:uppercase}}.eyebrow:before{{content:"";
display:inline-block;width:24px;height:1px;background:currentColor;vertical-align:middle;
margin-right:9px}}h1{{font-size:clamp(38px,5vw,66px);line-height:.98;letter-spacing:-.05em;
margin:0}}.lede{{font-size:17px;color:#b7bec8;max-width:720px;margin:20px 0 0}}
.budget-panel{{border-left:1px solid var(--line);padding:28px;background:rgba(5,8,13,.25)}}
.budget-total{{display:flex;align-items:end;justify-content:space-between;gap:20px}}
.budget-total strong{{font-size:42px;line-height:1;letter-spacing:-.04em}}.budget-total span{{
color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.1em}}.allocation{{
height:12px;display:flex;overflow:hidden;border-radius:99px;background:#080b12;margin:21px 0 13px;
border:1px solid var(--line)}}.allocation span{{height:100%;min-width:3px;border-right:2px solid #0e121b}}
.allocation .alive{{background:var(--mint)}}.allocation .dead{{background:var(--coral)}}
.allocation .revived{{background:var(--violet)}}.allocation .unknown{{background:var(--amber)}}
.legend{{display:flex;gap:14px;flex-wrap:wrap;color:var(--muted);font-size:10px}}.legend span:before{{
content:"";display:inline-block;width:7px;height:7px;border-radius:2px;background:currentColor;
margin-right:6px}}.legend .survivor{{color:var(--mint)}}.legend .killed{{color:var(--coral)}}
.metrics{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:17px 0}}
.metric,.panel,.thesis-card{{background:linear-gradient(180deg,rgba(17,22,32,.97),
rgba(11,15,23,.98));border:1px solid var(--line);border-radius:14px}}.metric{{padding:17px}}
.metric strong{{display:block;font-size:27px;line-height:1.15}}.metric span{{display:block;
color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.1em;margin-top:7px}}
.section-head{{display:flex;justify-content:space-between;align-items:end;gap:20px;margin:28px 0 12px}}
.section-head h2{{margin:0;font-size:22px}}.section-head>p{{margin:0;color:var(--muted)}}
.thesis-grid{{display:grid;grid-template-columns:repeat(5,minmax(190px,1fr));gap:10px}}
.thesis-card{{padding:15px;min-height:178px;display:flex;flex-direction:column}}.thesis-top{{
display:flex;align-items:center;justify-content:space-between;gap:8px}}.thesis-id{{color:var(--faint);
font:700 10px ui-monospace,monospace;letter-spacing:.1em}}.thesis-card h3{{font-size:15px;
line-height:1.35;margin:19px 0;letter-spacing:-.01em}}.thesis-meta{{margin-top:auto;display:grid;
gap:8px;color:var(--faint);font:650 9px ui-monospace,monospace;letter-spacing:.08em}}
.thesis-meta span{{display:grid;grid-template-columns:58px minmax(0,1fr);gap:6px}}.thesis-meta strong,
.thesis-meta code{{color:#c7ced7;font-size:10px;overflow-wrap:anywhere}}.pill{{display:inline-flex;
padding:4px 7px;border-radius:999px;font:750 9px ui-monospace,monospace;letter-spacing:.06em;
border:1px solid transparent}}.alive{{background:#163a2b;color:var(--mint);border-color:#24543f}}
.dead{{background:#3e1d1b;color:#ff9d90;border-color:#60302c}}.revived{{background:#2c2548;
color:#cabbff;border-color:#493d75}}.unknown{{background:#392f17;color:var(--amber);
border-color:#5e4d25}}.panel{{overflow:hidden;margin-top:14px}}.panel-head{{display:flex;
align-items:center;justify-content:space-between;padding:18px 20px;border-bottom:1px solid var(--line)}}
.panel-head h2{{margin:0;font-size:20px}}.panel-head span{{color:var(--muted);font-size:11px}}
.scroll{{overflow:auto;max-height:520px}}table{{width:100%;border-collapse:collapse;font-size:12px}}
th,td{{text-align:left;padding:11px 13px;border-bottom:1px solid var(--line);vertical-align:top}}
th{{position:sticky;top:0;background:#121722;color:var(--faint);font:700 9px
ui-monospace,monospace;text-transform:uppercase;letter-spacing:.1em}}tbody tr:hover{{
background:rgba(124,243,180,.03)}}code{{font:11px ui-monospace,monospace}}.note{{display:flex;
justify-content:space-between;gap:20px;color:var(--muted);font-size:12px;border-top:1px solid
var(--line);padding-top:17px;margin-top:22px}}@media(max-width:1120px){{.thesis-grid{{
grid-template-columns:repeat(3,1fr)}}}}@media(max-width:900px){{.hero{{grid-template-columns:1fr}}
.budget-panel{{border-left:0;border-top:1px solid var(--line)}}}}@media(max-width:700px){{
.topbar{{padding:0 14px}}.topmeta>span:first-child{{display:none}}main{{padding:18px 14px 42px}}
.hero-copy,.budget-panel{{padding:24px 20px}}.metrics{{grid-template-columns:1fr}}
.thesis-grid{{grid-template-columns:1fr}}.section-head,.note{{align-items:start;flex-direction:column}}}}
</style>
</head>
<body><header class="topbar"><div class="brand"><span class="brand-mark">F</span>
FLOTILLA</div><div class="topmeta"><span>research portfolio / allocation desk</span>
<span class="ready">decisions recorded</span></div></header><main>
<section class="hero"><div class="hero-copy"><p class="eyebrow">Thesis portfolio · Journey 0</p>
<h1>Five theses. One budget.</h1><p class="lede">The cheapest falsifiers run first.
Kills require executable evidence and operator confirmation; every decision remains reversible.</p>
</div><aside class="budget-panel"><p class="section-label">Capital allocation</p>
<div class="budget-total"><strong>{total_spend:.1f}</strong><span>budget units deployed</span></div>
<div class="allocation" aria-label="Budget allocation by thesis">{allocation_segments}</div>
<div class="legend"><span class="survivor">survivor allocation</span>
<span class="killed">stopped allocation</span></div></aside></section>
<section class="metrics">
  <div class="metric"><strong>{len(thesis_rows)}</strong><span>theses under test</span></div>
  <div class="metric"><strong>{len(decisions)}</strong><span>deterministic decisions</span></div>
  <div class="metric"><strong>{undetermined}</strong><span>undetermined decisions</span></div>
</section>
<div class="section-head"><div><p class="section-label">Conviction board</p>
<h2>Portfolio state</h2></div><p>Fixture evidence · not scientific validation</p></div>
<section class="thesis-grid">{thesis_cards}</section>
<section class="panel"><div class="panel-head"><h2>Decision register</h2>
<span>predicate + spend + disposition</span></div><div class="scroll"><table>
<thead><tr><th>ID</th><th>Thesis</th><th>Status</th><th>Spend</th><th>Kill predicate</th></tr></thead>
<tbody>{thesis_html}</tbody></table></div></section>
<section class="panel"><div class="panel-head"><h2>Decision timeline</h2>
<span>append-only lineage</span></div><div class="scroll"><table>
<thead><tr><th>#</th><th>Logical time</th><th>Event</th><th>Scope</th></tr></thead>
<tbody>{event_html}</tbody></table></div></section>
<footer class="note"><span>Generated by <code>make demo</code>.</span>
<span>SQLite and Markdown kill reports retain complete evidence and lineage.</span></footer>
</main></body></html>
"""
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(document, encoding="utf-8")
    return target
