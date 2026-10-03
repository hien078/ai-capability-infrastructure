"""The query expression language: terms, operators, AND groups.

Implements README.md §4 (query): whitespace-joined terms mean AND; a term
is ``field<op><value>`` with no spaces inside; the value may be quoted.
Missing fields and incomparable types make a term FALSE, never an error.
``or`` (as a separator) and parentheses are reserved in this version.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from recdb.store import parse_value


class QueryError(Exception):
    """A query usage error (exit 2)."""


#: Two-character operators must match before their one-character prefixes.
OPERATORS = ("!=", ">=", "<=", "~=", "=", ">", "<")
_OP_CHARS = set("=!<>~")


@dataclass(frozen=True)
class Term:
    field: str
    op: str
    value: Any


def _literal(text: str, quoted: bool) -> Any:
    if quoted:
        return text
    return parse_value(text)


def parse(expr: str) -> list[Term]:
    """Parse a query expression into its AND-joined terms."""
    terms: list[Term] = []
    i, n = 0, len(expr)
    while i < n:
        while i < n and expr[i].isspace():
            i += 1
        if i >= n:
            break
        if expr.startswith("or", i) and (i + 2 >= n or expr[i + 2].isspace()):
            raise QueryError("'or' is reserved in this version")
        if expr[i] in "()":
            raise QueryError("parentheses are reserved in this version")
        j = i
        while (
            j < n and expr[j] not in _OP_CHARS and not expr[j].isspace() and expr[j] not in "()'\""
        ):
            j += 1
        field = expr[i:j]
        op = None
        for candidate in OPERATORS:
            if expr.startswith(candidate, j):
                op = candidate
                break
        if op is None:
            raise QueryError(f"expected an operator after field {field!r}")
        j += len(op)
        if j < n and expr[j] in ("'", '"'):
            quote = expr[j]
            k = j + 1
            while k < n and expr[k] != quote:
                k += 1
            if k >= n:
                raise QueryError("unbalanced quote in query")
            value, quoted = expr[j + 1 : k], True
            j = k + 1
        else:
            k = j
            while k < n and not expr[k].isspace():
                k += 1
            value, quoted = expr[j:k], False
            j = k
        if field == "":
            raise QueryError("expected a field name")
        if not quoted and value == "":
            raise QueryError(f"missing value for field {field!r}")
        terms.append(Term(field, op, _literal(value, quoted)))
        i = j
    if not terms:
        raise QueryError("empty query")
    return terms


def _klass(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "num"
    if isinstance(value, str):
        return "str"
    return "other"


def eval_term(record: dict[str, Any], term: Term) -> bool:
    if term.field not in record:
        return False
    val, lit, op = record[term.field], term.value, term.op
    ka, kb = _klass(val), _klass(lit)
    if op == "=":
        if ka == "num" and kb == "num":
            return val == lit
        if ka == "str" and kb == "str":
            return val == lit
        if ka == "bool" and kb == "bool":
            return val == lit
        if ka == "null" and kb == "null":
            return True
        return False
    if op == "!=":
        if ka == "null" or kb == "null":
            return False
        if ka != kb:
            return False
        return val != lit
    if op in (">", ">=", "<", "<="):
        if ka == "num" and kb == "num":
            pass
        elif ka == "str" and kb == "str":
            pass
        else:
            return False
        if op == ">":
            return val > lit
        if op == ">=":
            return val >= lit
        if op == "<":
            return val < lit
        return val <= lit
    if op == "~=":
        if ka == "str" and kb == "str":
            return lit in val
        return False
    raise AssertionError(f"unknown operator: {op}")


def matches(record: dict[str, Any], terms: list[Term]) -> bool:
    """Does the record satisfy every term?"""
    return all(eval_term(record, term) for term in terms)
