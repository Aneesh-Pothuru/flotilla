# Kill report — T-03

## Decision

**KILLED**, confirmed by `demo-operator`. This decision is reversible with
`flotilla revive T-03`.

## Registered thesis

Aggressive regularization improves paired accuracy by at least 1 percentage point.

## Evidence

- Run ID: `T-03-v1-falsifier`
- `ci_lower`: -0.034001
- `ci_upper`: -0.025999
- `control_mean`: 0.708750
- `delta`: -0.030000
- `n`: 4.000000
- `treatment_mean`: 0.678750

The exact deterministic predicate that fired was:

```text
ci_lower < 0.01
```

## Conditions under which this kill may be wrong

- Noise is synthetic and class-symmetric.
- The regularization grid has only one aggressive setting.

This fixture report demonstrates decision provenance. It is not evidence for a
general false-kill accuracy claim.
