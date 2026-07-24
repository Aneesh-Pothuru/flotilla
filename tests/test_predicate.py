from __future__ import annotations

import unittest

from flotilla.predicate import (
    PredicateError,
    PredicateUndetermined,
    SafePredicate,
)


class SafePredicateTests(unittest.TestCase):
    def test_numeric_boolean_expression(self) -> None:
        predicate = SafePredicate("ci_lower < 0.02 and n >= 4")
        self.assertTrue(predicate.evaluate({"ci_lower": 0.01, "n": 4}))
        self.assertFalse(predicate.evaluate({"ci_lower": 0.03, "n": 4}))

    def test_missing_score_is_undetermined(self) -> None:
        with self.assertRaises(PredicateUndetermined):
            SafePredicate("ci_lower < 0.02").evaluate({"delta": 0.1})

    def test_calls_attributes_and_subscripts_are_rejected(self) -> None:
        for source in ("__import__('os')", "scores.value < 1", "scores['x'] < 1"):
            with self.subTest(source=source), self.assertRaises(PredicateError):
                SafePredicate(source)


if __name__ == "__main__":
    unittest.main()

