# Competitive UI review

Reviewed 2026-07-24 using current first-party product documentation. The goal
was not to reproduce an experiment tracker. It was to identify interaction
patterns FLOTILLA should preserve, then build a surface that makes portfolio
governance—not charts—its center of gravity.

## Primary-source review

| Product | First-party source | Relevant interaction pattern | FLOTILLA interpretation |
| --- | --- | --- | --- |
| Weights & Biases Reports | [Reports overview](https://docs.wandb.ai/models/reports), [editing and frozen run sets](https://docs.wandb.ai/models/reports/edit-a-report) | Reports combine narrative, images, experiment panels, and selected run sets. A run set can be frozen so the report preserves one point-in-time evidence state. | Treat the public surface as a durable research memorandum. Put conclusions and allocation first; retain exact run evidence inside expandable dossiers. |
| Weights & Biases run comparison | [Pin and compare runs](https://docs.wandb.ai/models/runs/compare-runs) | Important and baseline runs remain visible, and summary deltas are framed against the baseline. | Keep the falsifier and registered predicate visible along each thesis trajectory instead of hiding the decision baseline in a generic chart. |
| Neptune | [Dashboards](https://docs.neptune.ai/custom_dashboard), [select runs](https://docs.neptune.ai/select_runs), [group runs](https://docs.neptune.ai/groups) | Neptune distinguishes ongoing dashboards from final reports, lets viewers select visible runs, and groups comparisons by metadata. | Separate the live temporary simulator from the immutable registered ledger. Thesis selection changes the evidence drawer without rewriting the static report. |
| Comet | [Analyze experiments](https://www.comet.com/docs/v2/guides/experiment-management/analyze-experiments/) | Project panels compare many runs, while diff mode narrows to a small side-by-side set and exposes parameter, code, and dependency changes. | Keep portfolio-scale trajectories visible, but let one selected thesis reveal code commit, data hash, seed, scores, predicate, and reviewer decision. |
| MLflow Tracking | [MLflow Tracking](https://mlflow.org/docs/latest/ml/tracking) | Runs bind parameters, metrics, code versions, datasets, and artifacts; experiments group runs and support search and comparison. | Preserve provenance as the substrate. The product view summarizes the decision without dropping run identity, dataset identity, or artifacts. |
| Litmaps | [Use and edit visualization](https://docs.litmaps.com/en/articles/9181490-use-and-edit-litmaps-visualization), [introduction](https://docs.litmaps.com/en/articles/7240465-introduction-to-litmaps) | A research map exposes relationships, supports different axes, distinguishes roles through labels/color/icons, and uses annotation to tell a research story. | Represent theses as courses through a shared strategy chart. Position and route length communicate stage; text labels carry status so color is never the sole signal. |

## Interaction lessons retained

1. **Narrative and evidence need separate layers.** The landing page explains
   the allocation thesis; the simulator is the decision surface; the registered
   ledger remains available below it.
2. **Selection should change context, not truth.** Selecting a thesis updates
   the evidence drawer and highlights its course. It does not alter registered
   evidence.
3. **Baselines must stay visible.** Every route starts at registration, passes
   through its falsifier, and ends in a named state. The executable predicate
   remains readable.
4. **Portfolio and detail must coexist.** The strategy table shows all five
   trajectories. Expandable dossiers preserve runs, scores, code/data identity,
   limitations, and decisions.
5. **Temporary exploration must be unmistakable.** Browser simulation state is
   in memory only. The default ledger report remains immutable and is always
   labeled as fixture evidence rather than scientific validation.

## Chosen design thesis

**An editorial research portfolio and nautical strategy table, not an ML
dashboard.**

- Warm chart-paper ground, navy ink, oxidized teal, vermilion stop marks, and
  restrained brass replace dark panels and glowing status cards.
- A newspaper-like masthead and large serif argument make the product thesis
  legible before any controls appear.
- Thesis trajectories read like plotted courses: registration, falsifier,
  follow-up, and terminal decision are positions on one horizontal route.
- Capital appears as flow and soundings—spent, earmarked, released, and
  reserve—not as ornamental KPI tiles.
- The browser lab supports scenario and thesis selection, mandate changes,
  launch/step/reset, reserve reallocation, kill confirmation, reviewer
  overturn, revival, evolving graphs, evidence, and event lineage.
- Native controls, semantic headings, status text, focus treatments,
  reduced-motion handling, and responsive layouts keep the experience usable
  without relying on color, hover, or a wide viewport.

The result is deliberately unlike W&B, Neptune, Comet, and MLflow: those
products help teams compare experiments; FLOTILLA helps a research director
decide which line of inquiry deserves the next unit of capital.
