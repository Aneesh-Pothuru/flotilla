# Kill report — T-01

## Decision

**KILLED**, confirmed by `demo-operator`. This decision is reversible with
`flotilla revive T-01`.

## Registered thesis

Selected features improve paired holdout accuracy by at least 1 percentage point.

## Evidence

- Run ID: `T-01-v1-falsifier`
- `ci_lower`: 0.000760
- `ci_upper`: 0.001740
- `control_mean`: 0.741250
- `delta`: 0.001250
- `n`: 4.000000
- `treatment_mean`: 0.742500

The exact deterministic predicate that fired was:

```text
ci_lower < 0.01
```

## Conditions under which this kill may be wrong

- The fixture covers four small tabular splits only.
- Alternative selectors and tuned regularization remain untested.

This fixture report demonstrates decision provenance. It is not evidence for a
general false-kill accuracy claim.
