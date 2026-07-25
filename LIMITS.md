# Limits

This repository is an honest local MVP.

## Demonstrated

- Keyless deterministic five-thesis replay using Python's standard library.
- Safe predicate parsing without `eval`.
- Explicit plan approval and kill confirmation.
- Falsifier-first shared-budget accounting with SQLite lineage.
- Local paired-measurement execution, notebook job emission, kill reports,
  revival events, and a static dashboard.
- A complete static product site plus a deterministic in-browser strategy
  simulator for scenario selection, budget controls, experiment stepping,
  capital earmarking, kill/overturn/revive decisions, evidence, and lineage.
- An explicit `UNDETERMINED` decision when predicate evidence is absent.
- A persistent installed HTTP service over the real local executor and safe
  predicate engine: initialization, registration, approval, dependency-aware
  step/run, results, human confirm/overturn/revive, reversible cap
  reallocation, restart-safe budget state, health/readiness, request IDs,
  bounded inputs, bearer auth for non-loopback binds, and exact-origin CORS.
- A non-root, capability-dropped Docker/Compose path with a persistent data
  volume and loopback-only host publication.

## Not yet demonstrated

- The demo analyzes bundled paired measurements; it does not train real
  scikit-learn models. This dependency-free substitution exercises portfolio
  mechanics, not the truth of the example ML theses.
- The ≥90% early-kill accuracy, ≤5% wrong-kill rate, ≥3× cost advantage, and
  <24-hour first-decision targets have not been measured. No such result is
  claimed by this repository.
- The false-kill study protocol is documented in `docs/FALSE_KILL_STUDY.md`,
  but no sufficiently powered study has run.
- The LLM planner, AblationBench comparison, contradiction detector, adaptive
  replanning, hosted multi-user control plane, and BATON executor are not
  implemented.
- Notebook execution is emitted, not submitted to Kaggle or Colab.
- The scheduler uses a deterministic falsifier-first/fair-floor policy. It is
  not the later budgeted-bandit policy described for v0.3.
- The SQLite backend is single-machine. The HTTP service serializes local
  writes and uses WAL, but there is no distributed claim, external identity
  provider, tenant isolation, RBAC, TLS termination, queue, or transaction
  coordinator. Operators must put a non-loopback deployment behind their own
  TLS/auth gateway and back up the data volume.
- Browser simulator state is temporary and deliberately does not mutate the
  registered SQLite ledger. Its optional live connector is read-only and needs
  an explicitly allowed origin. It demonstrates interaction and governance
  paths, not a hosted multi-user control plane.
- The installed executor still analyzes caller-registered paired measurements;
  it does not launch arbitrary training code, Kubernetes jobs, Kaggle jobs, or
  GPUs. The executor field deliberately rejects anything except `local`.

The dashboard labels fixture-derived results and must not be presented as a
scientific validation of the example theses.
