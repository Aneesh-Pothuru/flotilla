# Competitive UI review

Reviewed 2026-07-24 against experiment tracking and research portfolio tools.

| Product | Relevant surface | What works |
| --- | --- | --- |
| [Weights & Biases Sweeps](https://docs.wandb.ai/models/sweeps) | parallel search and optimization | Rich experiment tracking is tied directly to the decision process and parallel execution. |
| [Neptune](https://docs.neptune.ai/reports) | experiment reports | Teams can compare runs, preserve a curated report, and distinguish an exploratory dashboard from a published conclusion. |
| [Comet](https://www.comet.com/docs/v2/guides/comet-ui/experiment-management/project-pages/overview/) | experiment management | Flexible panels coexist with a strong experiment table, filtering, grouping, and explicit run selection. |
| [MLflow](https://mlflow.org/docs/latest/ml/tracking/) | run and model comparison | Experiments, runs, metrics, parameters, and artifacts retain visible lineage. |

## Direction adopted

- Make budget allocation the dominant visual, not an afterthought.
- Represent every thesis as an invest/kill decision card with spend, predicate,
  and reversible status.
- Separate observed evidence from operator decisions and scientific claims.
- Use a portfolio allocation bar and a chronological decision tape to make
  reallocation understandable at a glance.
- Use coral for killed theses, mint for survivors, and violet for revived or
  contested decisions.

The result is a research investment desk rather than an ML metrics dashboard.
