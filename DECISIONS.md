# Decisions

## 2026-07-24 — standard-library Journey 0

The build brief describes scikit-learn experiments. Journey 0 instead performs
real statistical calculations over bundled paired measurements with the Python
standard library. This keeps `git clone && make demo` keyless and dependency
free. The dashboard and limits explicitly label these as fixtures.

## 2026-07-24 — v0.1 scheduling policy

The system-design narrative describes a budgeted bandit while the milestone
table assigns bandit allocation to v0.3. This implementation uses the
unambiguous P0 policy: every approved falsifier receives a fair floor, all
falsifiers dispatch first, then the cheapest remaining nodes of survivors.

## 2026-07-24 — kill confirmation in replay

Runtime kills remain pending until a named confirmer approves them. The
noninteractive demo supplies a deterministic `demo-operator` confirmation and
records it as a first-class event rather than silently disabling the gate.

## 2026-07-24 — safe predicate language

Predicates support numeric score names, constants, arithmetic, comparisons,
boolean `and`/`or`, and unary `not`/sign operators. Calls, attributes,
subscripts, comprehensions, and arbitrary Python are rejected at parse time.

## 2026-07-24 — no unverified launch claims

Metric targets and external Gemini/Kaggle/Colab paths remain documented but
unclaimed until reproduced. `UNDETERMINED` is a normal decision state and its
count is shown in the dashboard.

