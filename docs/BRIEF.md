# 03 · FLOTILLA

**An agent swarm that runs a research team's thesis portfolio — many
hypotheses in flight at once, under one compute budget, with losers
killed early and every kill argued in writing.**

`flotilla` · Python · SQLite ledger · pluggable executors · static dashboard

---

## Objective

A research team has twelve ideas and enough compute for three. Today they
pick by intuition and find out in two weeks. FLOTILLA turns that into a
managed portfolio: **express a thesis, get it decomposed into the
cheapest experiment that could falsify it, run many of those in parallel
under a budget, and reallocate compute toward whatever is still alive.**

The distinction that matters: existing multi-agent research systems try
to *replace* the researcher — generate hypotheses, run experiments, write
the paper. FLOTILLA does not. It is a **portfolio manager for a human
team's hypotheses**: the researcher supplies the thesis and the taste;
FLOTILLA supplies parallelism, bookkeeping, and the discipline to kill
things early.

---

## Why now

- **Orchestrator-worker works and is expensive.** The canonical design —
  a lead agent spawning parallel isolated subagents — beat single-agent
  by **90.2%** on research evals at ~**15× tokens**, and token volume
  alone explained **~80% of score variance**
  ([architecture](https://blog.bytebytego.com/p/how-anthropic-built-a-multi-agent),
  [case study](https://www.zenml.io/llmops-database/building-a-multi-agent-research-system-for-complex-information-tasks)).
  If spend predicts quality, **allocating spend well is the whole game.**
- **Parallel exploration beats linear pipelines.** AI Scientist v2 moved
  to parallelized agentic tree search with typed ablation / replication /
  aggregation nodes ([paper](https://arxiv.org/pdf/2504.08066)); Google's
  co-scientist runs hypothesis tournaments.
- **Ablation planning is the measured weak spot.** AblationBench exists
  because proposing relevant, feasible, grounded ablations is hard for
  current systems ([AblationBench](https://arxiv.org/pdf/2507.08038)).
- **The field is chasing full autonomy** (FARS, AI-Supervisor,
  [empirical studies](https://arxiv.org/pdf/2603.29632)) and leaving the
  human-in-the-loop portfolio case wide open. Real teams don't want an
  autonomous scientist; they want twelve ideas triaged by Friday.

**The blog post this proves:** research velocity is an *allocation*
problem, not an ideas problem — the Δt thesis, cashed out.

---

## Non-goals

- **Not an autonomous scientist.** No paper writing, no novelty claims,
  no autonomous conclusions. It triages; humans decide.
- Not a trainer or cluster scheduler; it schedules *experiments*, which
  call whatever execution backend you have.
- Not a hypothesis generator (P2 at most). Generating ideas isn't the
  bottleneck for a good team; testing them is.

---

## Personas

| Persona | Cares about |
|---|---|
| **Research lead**, 12 ideas, 3 GPUs | "Which deserve real compute, and can I know by Friday?" |
| **Researcher** who owns 2 theses | "Run the cheap falsification and tell me I'm wrong before I spend a week." |
| **Platform owner** paying the bill | "What did we spend per thesis, and what did it buy?" |

---

## User journeys

### Journey 0 — the demo (no API key, <10 minutes)

```bash
git clone …/flotilla && make demo
```

Runs a bundled **five-thesis portfolio on CPU in ~3 minutes** — real,
falsifiable claims on classic ML (e.g. "feature selection beats
regularization on small-n tabular data," "this augmentation helps only
below 1k samples," each a scikit-learn experiment). You watch the
portfolio live in the terminal: falsifiers run first, two theses die
cheap, budget concentrates on survivors, kill reports land in
`reports/`. The dashboard (static HTML) shows alive/dead/spend/decision
timeline. Zero API keys — the demo's "agents" are deterministic planners
over experiment templates; the LLM planner is optional in live mode.

### J1 — A thesis becomes a falsification plan

Sam registers a thesis in plain language plus a falsifiable prediction:

> "Contrastive pretraining on unlabeled robot video improves downstream
> manipulation success more than adding equivalent labeled demos."

The planner returns a plan Sam edits before anything runs:

```
THESIS T-14
  ├─ CHEAPEST-FALSIFIER  2 GPU-h  linear-probe repr. gain; dead if <2pp
  ├─ ABLATION A1         6 GPU-h  pretrain data volume 10/50/100%
  ├─ ABLATION A2         6 GPU-h  labeled-demo count matched by wall-clock
  ├─ CONFOUND C1         1 GPU-h  is the gain just longer training?
  └─ REPLICATE R1        4 GPU-h  2 seeds on the surviving arm
  kill-rule: linear_probe_delta.ci_lower < 0.02
  budget: 20 GPU-h · decision-by: Fri 17:00
```

**Cheapest-falsifier-first** is the core design principle: everything is
arranged so the thesis dies cheaply if it's going to die.

### J2 — Twelve theses run as a portfolio

The team registers 12 theses against a shared weekly budget. Falsifiers
for all 12 run concurrently; five die at that stage for a fraction of
the budget; the remainder concentrates on survivors. The dashboard shows
alive/dead/blocked, spend per thesis, and time-to-decision.

### J3 — A kill fires, and it's argued, not silent

T-14's probe comes back at +0.4pp. FLOTILLA halts the thesis, releases
its budget, and files a **kill report**: the evidence, run IDs, the exact
predicate that fired, and the conditions under which the kill would be
wrong ("probe used a frozen encoder; fine-tuned probe untested"). `flotilla
revive T-14 --with A3` challenges it. Kills are reversible and argued.

### J4 — The lead audits the week

`flotilla report --week`: theses tested, GPU-hours per decision, the
surviving set, and the **cost-per-decision** trend — dollars per resolved
hypothesis, the number the whole product exists to shrink.

### End-to-end journey (the product loop)

Register thesis (+ prediction + kill predicate + budget) → planner
proposes DAG → human approves/edits → falsifier runs first → results
stream into the ledger → kill/promote predicates evaluate → budget
reallocates → kill reports & survivors accumulate → weekly portfolio
review → surviving theses graduate to full experiments; killed ones
archive with their evidence. Every artifact traceable: thesis → plan
version → node → run → data hash → seed.

---

## PRD

### P0

| ID | Requirement |
|---|---|
| P0-1 | **Thesis object** — versioned: falsifiable prediction, executable kill predicate (expression over scores — never prose, never `eval`), budget cap, decision deadline. |
| P0-2 | **Plan DAG** — typed nodes (`falsifier`, `ablation`, `confound`, `replicate`, `aggregate`); human-editable; unapproved plans never run. |
| P0-3 | **Portfolio scheduler** — shared-budget allocation, cheapest-falsifier-first, fair-share floors so no thesis starves. |
| P0-4 | **Pluggable executors** — v0.1 ships `local` (subprocess, CPU) and `notebook` (emit a Kaggle/Colab-ready job); BATON executor arrives v0.3. **No cross-project dependency in the MVP.** |
| P0-5 | **Kill/promote engine** — predicates evaluated deterministically as results land; halt + release budget on kill; kill report with evidence, run IDs, and stated limitations. |
| P0-6 | **Ledger + lineage** — SQLite: every result → thesis, plan version, code commit, data hash, seed. |
| P0-7 | **Human gates** — approve-plan and confirm-kill on by default. |
| P0-8 | **Dashboard** — static HTML portfolio view (alive/dead/spend/timeline); regenerated per event. |

### P1

| ID | Requirement |
|---|---|
| P1-1 | **LLM planner** — proposes the DAG from the thesis text (Gemini/Groq free tier); measured against AblationBench-style tasks, number published. |
| P1-2 | **Contradiction detector** — flag incompatible predictions between live theses; propose the single discriminating experiment. |
| P1-3 | **Cost-per-decision analytics** — spend, time-to-decision, kill rate, revival rate over time. |
| P1-4 | **Re-planning on partial results** — a node's result can amend the remaining plan, gated. |
| P1-5 | **Budget-aware depth** — scale plan depth to remaining budget instead of failing. |

### P2

- Hypothesis generation from a literature sweep (deliberately last).
- Cross-team dedupe (two groups testing the same thing).
- Replay-based scheduler evaluation: rerun a week's portfolio under a
  different allocation policy — a genuinely novel offline-eval story.

### Success metrics

| Metric | Target |
|---|---|
| Demo: clone → five-thesis portfolio resolved | < 10 min, $0, no key |
| Early-kill accuracy (killed theses that full runs also reject) | ≥ 90%, measured by running a sample of kills to completion anyway |
| Wrong-kill rate (revived and later validated) | ≤ 5%, always published |
| Cost per resolved hypothesis vs. manual baseline | ≥ 3× cheaper on a replayed historical set |
| Planner ablation quality | beats AblationBench baseline, published |
| First decision on a 12-thesis portfolio | < 24 h |

The false-kill study is the credibility of the entire system —
**deliberately run a sample of killed theses to completion and publish
the rate.**

### Launch-day definition

`make demo` five-thesis CPU portfolio (keyless); LLM planner working on a
free Gemini key; kill reports + revive; dashboard; the false-kill study
design documented; LIMITS.md (local executor only, single machine,
planner is assistive not autonomous).

### Risks

| Risk | Mitigation |
|---|---|
| 15× token economics | Cheapest-falsifier-first; hard per-thesis caps; cost-per-decision as a headline metric; deterministic demo needs zero LLM calls |
| Confidently kills a good idea | Kill reports state their own limitations; one-command revival; false-kill rate measured and published |
| Plausible-but-useless ablations | Human gate on plans; ablation scoring (P1); benchmark the planner |
| Becomes a science-slop generator | Hard non-goals: no papers, no novelty claims, no autonomous conclusions |

---

## System design

```
 thesis.yaml ─▶ ┌──────────┐    ┌──────────────┐  approve/edit  ┌──────────┐
                │ REGISTRY │───▶│ PLANNER      │───────────────▶│ PLAN DAG │
                └──────────┘    │ (rules; LLM  │   human gate   └────┬─────┘
                                │  optional)   │                     │
                                └──────────────┘                     ▼
        ┌─────────────────┐  reserve/release   ┌────────────────────────┐
        │   PORTFOLIO     │◀───────────────────│  NODE EXECUTOR         │
        │   SCHEDULER     │─── dispatch ──────▶│  local | notebook |    │
        │ (bandit: info   │                    │  baton (v0.3)          │
        │  per unit cost) │                    └───────────┬────────────┘
        └───────┬─────────┘                                │ results
                │                                          ▼
                │                             ┌────────────────────────┐
                │                             │  LEDGER (SQLite,       │
                │                             │  lineage, loopkit Run) │
                │                             └───────────┬────────────┘
                │              ┌───────────────┐          │
                └─────────────▶│ KILL/PROMOTE  │◀─────────┘
                               │ ENGINE        │  deterministic predicates
                               └──────┬────────┘
                                      ▼
                            kill reports · dashboard
```

**The scheduler is a budgeted bandit** where reward is *information
gained per unit cost* — approximated by how much a node's result moves
its kill predicate toward resolution. Cheapest-falsifier-first falls out
naturally; fair-share floors keep long-shots alive through their
falsifier.

**Workers are isolated on purpose** (the proven orchestrator-worker
lesson): no mid-task cross-talk. Cross-thesis reasoning lives in the
scheduler and the contradiction detector — deterministic code over
structured results, not agents chatting.

**Kill predicates are code.** The LLM proposes a predicate; it never *is*
the predicate.

### Interfaces

- **← ASSAY** — kill predicates evaluate over ASSAY-schema scores
  (vendored via loopkit in v0.1; live integration v0.3).
- **← BATON** (v0.3) — durable executor for long nodes.
- **→ CULPRIT** — infrastructural node failures handed off for
  attribution.

### Milestones

| | Scope |
|---|---|
| **v0.1** | Registry, hand-written plans, scheduler, local executor, kill engine, ledger, dashboard, five-thesis demo. **Journey 0 works.** |
| **v0.2** | LLM planner + gates, kill reports + revive, notebook executor, cost analytics. |
| **v0.3** | Bandit allocation, contradiction detector, BATON executor, ablation scoring. **Launch** with the false-kill study. |
| **v1.0** | Replay-based scheduler evaluation; cost-per-decision write-up. |

### Stack & free tier

Python 3.12 · SQLite ledger · subprocess executor (CPU demo) ·
Kaggle/Colab notebooks for GPU nodes (~60 free GPU-h/wk combined) ·
LiteLLM planner on Gemini free tier (a planner call is ~5 requests/thesis
— trivial against 1,500/day) · static dashboard on GitHub Pages. Total
required spend: **$0**.
