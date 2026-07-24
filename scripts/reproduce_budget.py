"""Reproduce falsifier-first and budget-accounting facts from Journey 0."""

from __future__ import annotations

from flotilla.core import Ledger
from flotilla.demo import run_demo


def main() -> int:
    summary = run_demo()
    with Ledger("reports/flotilla.sqlite") as ledger:
        runs = ledger.rows("runs")
        kinds = [str(row["node_kind"]) for row in runs]
        first_non_falsifier = next(
            (index for index, kind in enumerate(kinds) if kind != "falsifier"),
            len(kinds),
        )
        assert first_non_falsifier == 5, kinds
        assert all(kind == "falsifier" for kind in kinds[:5]), kinds
        assert summary["statuses"] == {"KILLED": 2, "SURVIVED": 3}, summary
        assert summary["spent"] == 11.0, summary
    print("reproduced: five falsifiers dispatched first")
    print("reproduced: two kills, three survivors, 11/12 budget units spent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

