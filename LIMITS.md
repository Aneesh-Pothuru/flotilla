# Limits

This repository is an honest local MVP.

## Demonstrated

- Keyless deterministic five-thesis replay using Python's standard library.
- Safe predicate parsing without `eval`.
- Explicit plan approval and kill confirmation.
- Falsifier-first shared-budget accounting with SQLite lineage.
- Local paired-measurement execution, notebook job emission, kill reports,
  revival events, and a static dashboard.
- An explicit `UNDETERMINED` decision when predicate evidence is absent.

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
  replanning, hosted service, and BATON executor are not implemented.
- Notebook execution is emitted, not submitted to Kaggle or Colab.
- The scheduler uses a deterministic falsifier-first/fair-floor policy. It is
  not the later budgeted-bandit policy described for v0.3.
- The SQLite backend is single-process and single-machine. There is no
  distributed claim or transaction coordinator.

The dashboard labels fixture-derived results and must not be presented as a
scientific validation of the example theses.

