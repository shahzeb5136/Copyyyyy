"""A tiny, safe expression language for derived columns.

The LLM writes expressions such as ``actual - budget`` or
``100 * (actual - budget) / budget``; this module parses them with its own
recursive-descent parser and evaluates them against a DataFrame. There is no
``eval``/``exec`` anywhere - only the grammar below is accepted.

Grammar
    expr    := or_expr
    or_expr := and_expr ( "or" and_expr )*
    and_expr:= not_expr ( "and" not_expr )*
    not_expr:= "not" not_expr | cmp
    cmp     := sum ( ("==" | "!=" | ">" | ">=" | "<" | "<=") sum )?
    sum     := term ( ("+" | "-") term )*
    term    := unary ( ("*" | "/" | "%") unary )*
    unary   := "-" unary | atom
    atom    := NUMBER | STRING | column | function "(" args ")" | "(" expr ")"
    column  := IDENT | `backtick quoted name`

Functions: abs, round, floor, ceil, sqrt, log, coalesce, if, min, max, year, month,
quarter, day, weekday, days_between, len, lower, upper, isnull.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


class ExpressionError(ValueError):
    pass


_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<number>\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)
  | (?P<string>'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")
  | (?P<bq>`[^`]+`)
  | (?P<ident>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op>==|!=|>=|<=|>|<|\+|-|\*|/|%|\(|\)|,)
    """,
    re.VERBOSE,
)

_KEYWORDS = {"and", "or", "not", "true", "false", "null", "none"}

FUNCTIONS = {
    "abs", "round", "floor", "ceil", "sqrt", "log", "coalesce", "if", "min", "max", "year", "month", "quarter",
    "day", "weekday", "days_between", "len", "lower", "upper", "isnull",
}


@dataclass
class Token:
    kind: str
    value: str
    pos: int


def tokenize(text: str) -> list[Token]:
    out: list[Token] = []
    pos = 0
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if not m:
            raise ExpressionError(f"Unexpected character {text[pos]!r} at position {pos} in expression {text!r}")
        kind = m.lastgroup or ""
        if kind != "ws":
            out.append(Token(kind, m.group(kind), pos))
        pos = m.end()
    return out


# AST nodes are plain tuples: ("num", 1.0) ("str", "x") ("col", "name") ("bin", op, l, r) ("neg", x)
# ("call", name, [args]) ("and", l, r) ("or", l, r) ("not", x) ("const", value)
class Parser:
    def __init__(self, text: str):
        self.text = text
        self.tokens = tokenize(text)
        self.i = 0

    def peek(self) -> Token | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def take(self, kind: str | None = None, value: str | None = None) -> Token:
        tok = self.peek()
        if tok is None:
            raise ExpressionError(f"Unexpected end of expression {self.text!r}")
        if (kind and tok.kind != kind) or (value is not None and tok.value != value):
            raise ExpressionError(f"Unexpected token {tok.value!r} at position {tok.pos} in expression {self.text!r}")
        self.i += 1
        return tok

    def _is(self, kind: str, value: str | None = None) -> bool:
        tok = self.peek()
        return tok is not None and tok.kind == kind and (value is None or tok.value.lower() == value)

    def parse(self):
        node = self.or_expr()
        if self.peek() is not None:
            tok = self.peek()
            raise ExpressionError(f"Unexpected token {tok.value!r} at position {tok.pos} in expression {self.text!r}")
        return node

    def or_expr(self):
        node = self.and_expr()
        while self._is("ident", "or"):
            self.take()
            node = ("or", node, self.and_expr())
        return node

    def and_expr(self):
        node = self.not_expr()
        while self._is("ident", "and"):
            self.take()
            node = ("and", node, self.not_expr())
        return node

    def not_expr(self):
        if self._is("ident", "not"):
            self.take()
            return ("not", self.not_expr())
        return self.cmp()

    def cmp(self):
        node = self.sum()
        tok = self.peek()
        if tok and tok.kind == "op" and tok.value in ("==", "!=", ">", ">=", "<", "<="):
            self.take()
            node = ("bin", tok.value, node, self.sum())
        return node

    def sum(self):
        node = self.term()
        while self._is("op", "+") or self._is("op", "-"):
            op = self.take().value
            node = ("bin", op, node, self.term())
        return node

    def term(self):
        node = self.unary()
        while self._is("op", "*") or self._is("op", "/") or self._is("op", "%"):
            op = self.take().value
            node = ("bin", op, node, self.unary())
        return node

    def unary(self):
        if self._is("op", "-"):
            self.take()
            return ("neg", self.unary())
        return self.atom()

    def atom(self):
        tok = self.peek()
        if tok is None:
            raise ExpressionError(f"Unexpected end of expression {self.text!r}")
        if tok.kind == "number":
            self.take()
            return ("num", float(tok.value))
        if tok.kind == "string":
            self.take()
            return ("str", bytes(tok.value[1:-1], "utf-8").decode("unicode_escape"))
        if tok.kind == "bq":
            self.take()
            return ("col", tok.value[1:-1].strip())
        if tok.kind == "op" and tok.value == "(":
            self.take()
            node = self.or_expr()
            self.take("op", ")")
            return node
        if tok.kind == "ident":
            self.take()
            low = tok.value.lower()
            if low in ("true", "false"):
                return ("const", low == "true")
            if low in ("null", "none"):
                return ("const", None)
            if self._is("op", "("):
                if low not in FUNCTIONS:
                    raise ExpressionError(f"Unknown function '{tok.value}'. Allowed: {', '.join(sorted(FUNCTIONS))}")
                self.take()
                args = []
                if not self._is("op", ")"):
                    args.append(self.or_expr())
                    while self._is("op", ","):
                        self.take()
                        args.append(self.or_expr())
                self.take("op", ")")
                return ("call", low, args)
            return ("col", tok.value)
        raise ExpressionError(f"Unexpected token {tok.value!r} at position {tok.pos} in expression {self.text!r}")


def parse(text: str):
    if not text or not text.strip():
        raise ExpressionError("Empty expression.")
    return Parser(text).parse()


def referenced_columns(text: str) -> list[str]:
    """Column names an expression refers to (for static validation / compatibility checks)."""
    cols: list[str] = []

    def walk(node):
        kind = node[0]
        if kind == "col":
            if node[1] not in cols:
                cols.append(node[1])
        elif kind in ("bin",):
            walk(node[2])
            walk(node[3])
        elif kind in ("neg", "not"):
            walk(node[1])
        elif kind in ("and", "or"):
            walk(node[1])
            walk(node[2])
        elif kind == "call":
            for a in node[2]:
                walk(a)

    walk(parse(text))
    return cols


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #
def _resolve(df: pd.DataFrame, name: str) -> pd.Series:
    if name in df.columns:
        return df[name]
    lookup = {str(c).strip().lower(): c for c in df.columns}
    key = name.strip().lower()
    if key in lookup:
        return df[lookup[key]]
    key2 = re.sub(r"[^a-z0-9]+", "_", key).strip("_")
    for k, c in lookup.items():
        if re.sub(r"[^a-z0-9]+", "_", k).strip("_") == key2:
            return df[c]
    raise ExpressionError(f"Unknown column '{name}' in expression. Available columns: {', '.join(map(str, df.columns))}")


def _to_datetime(v):
    if isinstance(v, pd.Series):
        return v if pd.api.types.is_datetime64_any_dtype(v) else pd.to_datetime(v, errors="coerce")
    return pd.to_datetime(v, errors="coerce")


def _numeric(v):
    if isinstance(v, pd.Series):
        if pd.api.types.is_bool_dtype(v):
            return v.astype("float64")
        if pd.api.types.is_numeric_dtype(v):
            return v
        return pd.to_numeric(v, errors="coerce")
    return v


def _broadcast(v, index) -> pd.Series:
    return v if isinstance(v, pd.Series) else pd.Series([v] * len(index), index=index)


def evaluate(node, df: pd.DataFrame):
    kind = node[0]
    if kind == "num":
        return node[1]
    if kind == "str":
        return node[1]
    if kind == "const":
        return node[1]
    if kind == "col":
        return _resolve(df, node[1])
    if kind == "neg":
        return -_numeric(evaluate(node[1], df))
    if kind == "not":
        return ~_broadcast(evaluate(node[1], df), df.index).astype("boolean")
    if kind in ("and", "or"):
        left = _broadcast(evaluate(node[1], df), df.index).astype("boolean")
        right = _broadcast(evaluate(node[2], df), df.index).astype("boolean")
        return (left & right) if kind == "and" else (left | right)
    if kind == "bin":
        op, left, right = node[1], evaluate(node[2], df), evaluate(node[3], df)
        if op in ("+", "-", "*", "/", "%"):
            # string concatenation with '+'
            if op == "+" and (isinstance(left, str) or isinstance(right, str)
                              or (isinstance(left, pd.Series) and not pd.api.types.is_numeric_dtype(left)
                                  and not pd.api.types.is_datetime64_any_dtype(left))):
                ls = left.astype("string") if isinstance(left, pd.Series) else str(left)
                rs = right.astype("string") if isinstance(right, pd.Series) else str(right)
                return ls + rs
            # date arithmetic: date - date -> days
            if op == "-" and isinstance(left, pd.Series) and pd.api.types.is_datetime64_any_dtype(left):
                return (left - _to_datetime(right)).dt.total_seconds() / 86400.0
            lv, rv = _numeric(left), _numeric(right)
            with np.errstate(divide="ignore", invalid="ignore"):
                if op == "+":
                    return lv + rv
                if op == "-":
                    return lv - rv
                if op == "*":
                    return lv * rv
                if op == "/":
                    out = lv / rv
                    if isinstance(out, pd.Series):
                        return out.replace([np.inf, -np.inf], np.nan)
                    return out if math.isfinite(out) else float("nan")
                out = lv % rv
                return out.replace([np.inf, -np.inf], np.nan) if isinstance(out, pd.Series) else out
        # comparisons
        if isinstance(left, pd.Series) and pd.api.types.is_datetime64_any_dtype(left):
            right = _to_datetime(right)
        elif isinstance(right, pd.Series) and pd.api.types.is_datetime64_any_dtype(right):
            left = _to_datetime(left)
        elif isinstance(left, pd.Series) and pd.api.types.is_numeric_dtype(left) and isinstance(right, str):
            right = pd.to_numeric(right, errors="coerce")
        elif isinstance(right, pd.Series) and pd.api.types.is_numeric_dtype(right) and isinstance(left, str):
            left = pd.to_numeric(left, errors="coerce")
        if isinstance(left, pd.Series) and not pd.api.types.is_numeric_dtype(left) \
                and not pd.api.types.is_datetime64_any_dtype(left) and isinstance(right, str):
            left = left.astype("string").str.strip().str.lower()
            right = right.strip().lower()
        ops = {"==": lambda a, b: a == b, "!=": lambda a, b: a != b, ">": lambda a, b: a > b,
               ">=": lambda a, b: a >= b, "<": lambda a, b: a < b, "<=": lambda a, b: a <= b}
        return ops[op](left, right)
    if kind == "call":
        return _call(node[1], [evaluate(a, df) for a in node[2]], df)
    raise ExpressionError(f"Cannot evaluate node {kind}")


def _call(name: str, args: list[Any], df: pd.DataFrame):
    def need(n: int) -> None:
        if len(args) < n:
            raise ExpressionError(f"{name}() needs at least {n} argument(s)")

    if name == "abs":
        need(1)
        return abs(_numeric(args[0]))
    if name == "round":
        need(1)
        digits = int(args[1]) if len(args) > 1 else 0
        v = _numeric(args[0])
        return v.round(digits) if isinstance(v, pd.Series) else round(v, digits)
    if name in ("floor", "ceil", "sqrt", "log"):
        need(1)
        v = _numeric(args[0])
        fn = {"floor": np.floor, "ceil": np.ceil, "sqrt": np.sqrt, "log": np.log}[name]
        with np.errstate(divide="ignore", invalid="ignore"):
            out = fn(v)
        return out.replace([np.inf, -np.inf], np.nan) if isinstance(out, pd.Series) else out
    if name == "coalesce":
        need(1)
        out = _broadcast(args[0], df.index)
        for a in args[1:]:
            out = out.where(out.notna(), _broadcast(a, df.index))
        return out
    if name == "if":
        need(3)
        cond = _broadcast(args[0], df.index).astype("boolean").fillna(False)
        a, b = _broadcast(args[1], df.index), _broadcast(args[2], df.index)
        return a.where(cond, b)
    if name in ("min", "max"):
        need(2)
        frame = pd.concat([_broadcast(_numeric(a), df.index) for a in args], axis=1)
        return frame.min(axis=1) if name == "min" else frame.max(axis=1)
    if name in ("year", "month", "quarter", "day", "weekday"):
        need(1)
        d = _to_datetime(_broadcast(args[0], df.index))
        return {"year": d.dt.year, "month": d.dt.month, "quarter": d.dt.quarter, "day": d.dt.day,
                "weekday": d.dt.weekday}[name]
    if name == "days_between":
        need(2)
        a, b = _to_datetime(_broadcast(args[0], df.index)), _to_datetime(_broadcast(args[1], df.index))
        return (b - a).dt.total_seconds() / 86400.0
    if name == "len":
        need(1)
        return _broadcast(args[0], df.index).astype("string").str.len()
    if name in ("lower", "upper"):
        need(1)
        s = _broadcast(args[0], df.index).astype("string")
        return s.str.lower() if name == "lower" else s.str.upper()
    if name == "isnull":
        need(1)
        return _broadcast(args[0], df.index).isna()
    raise ExpressionError(f"Unknown function '{name}'")


def evaluate_expression(text: str, df: pd.DataFrame) -> pd.Series:
    """Evaluate an expression and always return a Series aligned to ``df``."""
    result = evaluate(parse(text), df)
    return _broadcast(result, df.index)
