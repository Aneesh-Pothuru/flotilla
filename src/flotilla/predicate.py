"""A deliberately small expression interpreter for score predicates."""

from __future__ import annotations

import ast
import math
from collections.abc import Mapping
from typing import Any


class PredicateError(ValueError):
    """The predicate is syntactically invalid or outside the safe language."""


class PredicateUndetermined(RuntimeError):
    """The predicate cannot be decided from the supplied score evidence."""


_ALLOWED_NODES = (
    ast.Expression,
    ast.BoolOp,
    ast.BinOp,
    ast.UnaryOp,
    ast.Compare,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.And,
    ast.Or,
    ast.Not,
    ast.UAdd,
    ast.USub,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.Eq,
    ast.NotEq,
)


class SafePredicate:
    """Parse and evaluate an allowlisted expression without ``eval``."""

    def __init__(self, source: str):
        if not source.strip():
            raise PredicateError("predicate cannot be empty")
        self.source = source
        try:
            self._tree = ast.parse(source, mode="eval")
        except SyntaxError as exc:
            raise PredicateError(f"invalid predicate syntax: {exc.msg}") from exc
        for node in ast.walk(self._tree):
            if not isinstance(node, _ALLOWED_NODES):
                raise PredicateError(
                    f"{type(node).__name__} is not allowed in predicates"
                )
            if isinstance(node, ast.Constant) and not isinstance(
                node.value, (int, float, bool)
            ):
                raise PredicateError("only numeric and boolean constants are allowed")

    @property
    def score_names(self) -> frozenset[str]:
        return frozenset(
            node.id for node in ast.walk(self._tree) if isinstance(node, ast.Name)
        )

    def evaluate(self, scores: Mapping[str, float | int]) -> bool:
        try:
            result = self._evaluate_node(self._tree.body, scores)
        except (ZeroDivisionError, OverflowError) as exc:
            raise PredicateUndetermined(f"numeric error: {exc}") from exc
        if not isinstance(result, bool):
            raise PredicateError("predicate must resolve to a boolean")
        return result

    def _evaluate_node(
        self, node: ast.AST, scores: Mapping[str, float | int]
    ) -> Any:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in scores:
                raise PredicateUndetermined(f"missing score: {node.id}")
            value = scores[node.id]
            if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise PredicateUndetermined(f"non-finite score: {node.id}")
            return value
        if isinstance(node, ast.UnaryOp):
            value = self._evaluate_node(node.operand, scores)
            if isinstance(node.op, ast.Not):
                return not bool(value)
            if isinstance(node.op, ast.UAdd):
                return +value
            if isinstance(node.op, ast.USub):
                return -value
        if isinstance(node, ast.BinOp):
            left = self._evaluate_node(node.left, scores)
            right = self._evaluate_node(node.right, scores)
            operations = {
                ast.Add: lambda: left + right,
                ast.Sub: lambda: left - right,
                ast.Mult: lambda: left * right,
                ast.Div: lambda: left / right,
            }
            for operator, operation in operations.items():
                if isinstance(node.op, operator):
                    return operation()
        if isinstance(node, ast.BoolOp):
            values = [self._evaluate_node(item, scores) for item in node.values]
            if isinstance(node.op, ast.And):
                return all(bool(value) for value in values)
            if isinstance(node.op, ast.Or):
                return any(bool(value) for value in values)
        if isinstance(node, ast.Compare):
            left = self._evaluate_node(node.left, scores)
            for operator, comparator in zip(node.ops, node.comparators, strict=True):
                right = self._evaluate_node(comparator, scores)
                if not self._compare(operator, left, right):
                    return False
                left = right
            return True
        raise PredicateError(f"unsupported predicate node: {type(node).__name__}")

    @staticmethod
    def _compare(operator: ast.cmpop, left: Any, right: Any) -> bool:
        comparisons = {
            ast.Lt: lambda: left < right,
            ast.LtE: lambda: left <= right,
            ast.Gt: lambda: left > right,
            ast.GtE: lambda: left >= right,
            ast.Eq: lambda: left == right,
            ast.NotEq: lambda: left != right,
        }
        for kind, comparison in comparisons.items():
            if isinstance(operator, kind):
                return comparison()
        raise PredicateError(f"unsupported comparison: {type(operator).__name__}")

