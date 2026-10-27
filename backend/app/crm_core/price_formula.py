"""Price formulas - a product's price impact written as an expression.

A per-value impact (AttributePriceImpact) says what one value of one
attribute adds. A price formula works an amount out of several attributes
and child products at once:

    if([Access Medium] = "Fiber" and [Contract Term] = "36 months", -10% * base, 0)
    max(0, [Bandwidth (Kbps)] - 2048) * 0.5
    [Static IP] * 250

They are ProductRule rows of rule_type "price" (definition {"expression",
"label", "charge"}); the design carries them as "price_rules", so released
model versions and product instances freeze them like every other rule, and
price() adds each one as a line of the price breakdown. A formula is never
eval'd: a small parser reads it into a tree, which is walked to an amount.

The language
- [Name]: an attribute (its number, in the attribute's unit, or its value)
  or a child product (how many are chosen). One-word names need no brackets.
- base: the product's base price. subtotal: the monthly price worked out so
  far (base + child products + attribute impacts + earlier formulas).
- numbers, 10% (= 0.10), "text"
- + - * /   = != < <= > >=   and or not   x in ("A", "B")
- if(condition, then, otherwise), min, max, round(x[, digits]), ceil, floor,
  abs, between(x, low, high)
Working an amount out never fails at selling time: a value not chosen is 0
(or empty text), and dividing by 0 gives 0. Everything else is checked when
the formula is saved.
"""

from __future__ import annotations

import difflib
import functools
import itertools
import json
import math
import re
from typing import Any

CHARGES = ("recurring", "one_time")
FUNCTIONS = {"if": (2, 3), "min": (1, 20), "max": (1, 20), "round": (1, 2), "ceil": (1, 1), "floor": (1, 1), "abs": (1, 1), "between": (3, 3)}
VARIABLES = {"base": "the base price", "subtotal": "the price so far"}
MAX_LENGTH = 2000
_SYMBOLS = str.maketrans({"×": "*", "÷": "/", "−": "-", "–": "-", "≤": "<=", "≥": ">=", "≠": "!=", "“": '"', "”": '"', "‘": "'", "’": "'"})
_TOKEN = re.compile(r"""\s*(?:
    (?P<num>\d+(?:\.\d+)?\s*%?)
  | (?P<str>"[^"]*"|'[^']*')
  | (?P<ref>\[[^\]]+\])
  | (?P<op><=|>=|!=|<>|==|[-+*/(),=<>])
  | (?P<word>[A-Za-z_][A-Za-z0-9_]*)
)""", re.X)


class FormulaError(ValueError):
    pass


def _key(name: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


# ---------------------------------------------------------------- reading a formula


def _tokens(text: str) -> list[tuple[str, str, int]]:
    out, i = [], 0
    while i < len(text):
        if not text[i:].strip():
            break
        m = _TOKEN.match(text, i)
        if not m or m.end() == i:
            raise FormulaError(f"I don't understand '{text[i:].strip()[:20]}' (at character {i + 1})")
        kind = m.lastgroup or ""
        out.append((kind, m.group(kind), m.start(kind)))
        i = m.end()
    return out


class _Parser:
    def __init__(self, text: str):
        self.text = text
        self.toks = _tokens(text)
        self.i = 0

    def peek(self, offset: int = 0) -> tuple[str, str, int] | None:
        j = self.i + offset
        return self.toks[j] if j < len(self.toks) else None

    def is_(self, value: str, offset: int = 0) -> bool:
        t = self.peek(offset)
        return t is not None and t[0] in ("op", "word") and t[1].lower() == value

    def take(self, value: str) -> None:
        if not self.is_(value):
            t = self.peek()
            raise FormulaError(f"Expected '{value}'" + (f" before '{t[1]}' (at character {t[2] + 1})" if t else " at the end"))
        self.i += 1

    def parse(self) -> tuple:
        if not self.toks:
            raise FormulaError("The formula is empty")
        node = self.or_()
        if self.peek() is not None:
            t = self.peek()
            raise FormulaError(f"Unexpected '{t[1]}' (at character {t[2] + 1}) - is an operator or a comma missing?")
        return node

    def or_(self) -> tuple:
        node = self.and_()
        while self.is_("or"):
            self.i += 1
            node = ("or", node, self.and_())
        return node

    def and_(self) -> tuple:
        node = self.not_()
        while self.is_("and"):
            self.i += 1
            node = ("and", node, self.not_())
        return node

    def not_(self) -> tuple:
        if self.is_("not") and not self.is_("in", 1):
            self.i += 1
            return ("not", self.not_())
        return self.cmp()

    def cmp(self) -> tuple:
        node = self.add()
        t = self.peek()
        if t and t[0] == "op" and t[1] in ("=", "==", "!=", "<>", "<", "<=", ">", ">="):
            self.i += 1
            op = {"==": "=", "<>": "!="}.get(t[1], t[1])
            return ("cmp", op, node, self.add())
        negate = self.is_("not") and self.is_("in", 1)
        if negate or self.is_("in"):
            self.i += 2 if negate else 1
            self.take("(")
            items = [self.add()]
            while self.is_(","):
                self.i += 1
                items.append(self.add())
            self.take(")")
            node = ("in", node, items)
            return ("not", node) if negate else node
        return node

    def add(self) -> tuple:
        node = self.mul()
        while self.is_("+") or self.is_("-"):
            op = self.peek()[1]
            self.i += 1
            node = ("bin", op, node, self.mul())
        return node

    def mul(self) -> tuple:
        node = self.unary()
        while self.is_("*") or self.is_("/"):
            op = self.peek()[1]
            self.i += 1
            node = ("bin", op, node, self.unary())
        return node

    def unary(self) -> tuple:
        if self.is_("-"):
            self.i += 1
            return ("neg", self.unary())
        if self.is_("+"):
            self.i += 1
            return self.unary()
        return self.primary()

    def primary(self) -> tuple:
        t = self.peek()
        if t is None:
            raise FormulaError("The formula ends too early - something is missing at the end")
        kind, value, at = t
        self.i += 1
        if kind == "num":
            text = value.replace(" ", "")
            return ("num", float(text.rstrip("%")) / (100 if text.endswith("%") else 1), text)
        if kind == "str":
            return ("str", value[1:-1])
        if kind == "ref":
            name = value[1:-1].strip()
            if not name:
                raise FormulaError(f"Empty [ ] at character {at + 1}")
            return ("ref", name)
        if kind == "op" and value == "(":
            node = self.or_()
            self.take(")")
            return node
        if kind == "word":
            word = value.lower()
            if word in ("true", "yes"):
                return ("bool", True)
            if word in ("false", "no"):
                return ("bool", False)
            if word in VARIABLES:
                return ("var", word)
            if self.is_("("):
                if word not in FUNCTIONS:
                    raise FormulaError(f"'{value}' is not a function - the functions are {', '.join(FUNCTIONS)}")
                self.i += 1
                args = [] if self.is_(")") else [self.or_()]
                while self.is_(","):
                    self.i += 1
                    args.append(self.or_())
                self.take(")")
                low, high = FUNCTIONS[word]
                if not low <= len(args) <= high:
                    need = str(low) if low == high else f"{low} to {high}"
                    raise FormulaError(f"{word}() takes {need} values, not {len(args)}" + (" - if(condition, then, otherwise)" if word == "if" else ""))
                return ("call", word, args)
            if word in ("and", "or", "not", "in"):
                raise FormulaError(f"'{value}' needs something on both sides (at character {at + 1})")
            return ("ref", value)
        raise FormulaError(f"Unexpected '{value}' (at character {at + 1})")


@functools.lru_cache(maxsize=512)
def parse(text: str) -> tuple:
    text = str(text or "").translate(_SYMBOLS).strip()
    if len(text) > MAX_LENGTH:
        raise FormulaError(f"A formula is at most {MAX_LENGTH} characters")
    return _Parser(text).parse()


def _walk(node: tuple):
    yield node
    kind = node[0]
    if kind in ("neg", "not"):
        yield from _walk(node[1])
    elif kind in ("and", "or"):
        yield from _walk(node[1])
        yield from _walk(node[2])
    elif kind in ("bin", "cmp"):
        yield from _walk(node[2])
        yield from _walk(node[3])
    elif kind == "in":
        yield from _walk(node[1])
        for item in node[2]:
            yield from _walk(item)
    elif kind == "call":
        for arg in node[2]:
            yield from _walk(arg)


def references(node: tuple) -> list[str]:
    return list(dict.fromkeys(n[1] for n in _walk(node) if n[0] == "ref"))


# ---------------------------------------------------------------- working it out


def _n(v: Any) -> float:
    if v is None or v == "":
        return 0.0
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?\d+(?:\.\d+)?", str(v).replace(",", ""))
    return float(m.group(0)) if m else 0.0


def _truth(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip() != ""
    return bool(v)


def _numeric(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _equal(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return (a in (None, "")) and (b in (None, ""))
    if isinstance(a, bool) or isinstance(b, bool):
        return _truth(a) == _truth(b)
    if _numeric(a) or _numeric(b):
        other = b if _numeric(a) else a
        if isinstance(other, str) and not re.search(r"\d", other):
            return False
        return abs(_n(a) - _n(b)) < 1e-9
    return _key(a) == _key(b)


def _compare(op: str, a: Any, b: Any) -> bool:
    if op == "=":
        return _equal(a, b)
    if op == "!=":
        return not _equal(a, b)
    if a in (None, "") or b in (None, ""):
        return False
    x, y = _n(a), _n(b)
    return {"<": x < y, "<=": x <= y, ">": x > y, ">=": x >= y}[op]


def evaluate(node: tuple, values: dict[str, Any], base: float, subtotal: float) -> Any:
    """values: _key(name) -> number / text / None."""
    kind = node[0]
    if kind in ("num", "str", "bool"):
        return node[1]
    if kind == "var":
        return base if node[1] == "base" else subtotal
    if kind == "ref":
        return values.get(_key(node[1]))
    ev = lambda n: evaluate(n, values, base, subtotal)  # noqa: E731
    if kind == "neg":
        return -_n(ev(node[1]))
    if kind == "not":
        return not _truth(ev(node[1]))
    if kind == "and":
        return _truth(ev(node[1])) and _truth(ev(node[2]))
    if kind == "or":
        return _truth(ev(node[1])) or _truth(ev(node[2]))
    if kind == "cmp":
        return _compare(node[1], ev(node[2]), ev(node[3]))
    if kind == "in":
        a = ev(node[1])
        return any(_equal(a, ev(item)) for item in node[2])
    if kind == "bin":
        x, y = _n(ev(node[2])), _n(ev(node[3]))
        op = node[1]
        if op == "+":
            return x + y
        if op == "-":
            return x - y
        if op == "*":
            return x * y
        return x / y if y else 0.0
    if kind == "call":
        fn, args = node[1], node[2]
        if fn == "if":
            return ev(args[1]) if _truth(ev(args[0])) else (ev(args[2]) if len(args) > 2 else 0.0)
        if fn == "between":
            x = ev(args[0])
            return x not in (None, "") and _n(ev(args[1])) <= _n(x) <= _n(ev(args[2]))
        nums = [_n(ev(a)) for a in args]
        if fn == "min":
            return min(nums)
        if fn == "max":
            return max(nums)
        if fn == "round":
            return round(nums[0], int(nums[1]) if len(nums) > 1 else 0)
        if fn == "ceil":
            return float(math.ceil(nums[0]))
        if fn == "floor":
            return float(math.floor(nums[0]))
        return abs(nums[0])
    raise FormulaError(f"Unknown part of a formula: {kind}")


def is_number(attr: dict[str, Any]) -> bool:
    """A ranged attribute (or one without a value list) is a number in its own unit -
    even when the shared attribute also lists values ('10 Mbps', '1 Gbps')."""
    return attr.get("min") is not None or attr.get("max") is not None or not attr.get("values")


def values_for(design: dict[str, Any], pricing: dict[str, Any]) -> dict[str, Any]:
    """What each name in a formula stands for, given the picks."""
    from app.crm_core import product_config_service as pc

    picked = {_key(k): v for k, v in (pricing or {}).items()}
    out: dict[str, Any] = {}
    for comp in design.get("components", []):
        out[_key(comp["name"])] = float(pc.component_qty(design, pricing, comp["name"]))
    for attr in design.get("attributes", []):
        if attr.get("kind") != "pricing":
            continue
        raw = picked.get(_key(attr["name"]))
        if raw in (None, ""):
            out[_key(attr["name"])] = None
        elif not is_number(attr):
            out[_key(attr["name"])] = str(raw)
        else:
            number = pc.in_unit(raw, attr.get("unit")) if attr.get("unit") else pc._num(raw)
            out[_key(attr["name"])] = number if number is not None else str(raw)
    return out


def apply(design: dict[str, Any], pricing: dict[str, Any], base: float, total: float, lines: list[dict[str, Any]]) -> float:
    """price(): each formula of the design as a line of the breakdown; the monthly total after them."""
    rules = design.get("price_rules") or []
    if not rules:
        return total
    values = values_for(design, pricing)
    for rule in rules:
        try:
            amount = round(_n(evaluate(parse(rule["expression"]), values, base, total)), 2)
        except FormulaError:
            continue  # checked when it was saved; a formula that no longer reads adds nothing
        if not amount:
            continue
        if rule.get("charge") == "one_time":
            lines.append({"label": f"{rule.get('label') or 'Price formula'} (one-time)", "amount": amount, "one_time": True, "formula": True})
        else:
            lines.append({"label": rule.get("label") or "Price formula", "amount": amount, "formula": True})
            total += amount
    return total


# ---------------------------------------------------------------- saying it in words

_CMP_WORDS = {"=": "is", "!=": "is not", "<": "is less than", "<=": "is at most", ">": "is more than", ">=": "is at least"}
_PREC = {"or": 1, "and": 2, "not": 3, "cmp": 4, "in": 4, "+": 5, "-": 5, "*": 6, "/": 6}


def _fmt(v: float) -> str:
    return f"{v:,.2f}".rstrip("0").rstrip(".") if v != int(v) else f"{int(v):,}"


def say(node: tuple, outer: int = 0) -> str:
    kind = node[0]
    if kind == "num":
        return node[2] if node[2].endswith("%") else _fmt(node[1])
    if kind == "str":
        return node[1]
    if kind == "bool":
        return "yes" if node[1] else "no"
    if kind == "var":
        return VARIABLES[node[1]]
    if kind == "ref":
        return node[1]
    if kind == "neg":
        inner = node[1]
        return f"minus {say(inner, 7)}"
    wrap = lambda text, prec: f"({text})" if prec < outer else text  # noqa: E731
    if kind == "not":
        return wrap(f"not {say(node[1], 3)}", 3)
    if kind in ("and", "or"):
        p = _PREC[kind]
        return wrap(f"{say(node[1], p)} {kind} {say(node[2], p)}", p)
    if kind == "cmp":
        return wrap(f"{say(node[2], 5)} {_CMP_WORDS[node[1]]} {say(node[3], 5)}", 4)
    if kind == "in":
        return wrap(f"{say(node[1], 5)} is one of {', '.join(say(i, 5) for i in node[2])}", 4)
    if kind == "bin":
        op = node[1]
        p = _PREC[op]
        a, b = node[2], node[3]
        pct = lambda n: n[0] == "num" and n[2].endswith("%") or n[0] == "neg" and n[1][0] == "num" and n[1][2].endswith("%")  # noqa: E731
        if op == "*" and (pct(a) or pct(b)):
            share, of = (a, b) if pct(a) else (b, a)
            words = f"minus {share[1][2]}" if share[0] == "neg" else share[2]
            return wrap(f"{words} of {say(of, 7)}", p)
        symbol = {"+": "+", "-": "−", "*": "×", "/": "÷"}[op]
        return wrap(f"{say(a, p)} {symbol} {say(b, p + (1 if op in ('-', '/') else 0))}", p)
    if kind == "call":
        fn, args = node[1], node[2]
        if fn == "if":
            rest = say(args[2]) if len(args) > 2 else "0"
            text = f"if {say(args[0])} then {say(args[1])}, otherwise {rest}"
            return f"({text})" if outer else text
        if fn == "max" and len(args) == 2 and any(a[0] == "num" for a in args):
            floor, other = (args[0], args[1]) if args[0][0] == "num" else (args[1], args[0])
            return f"({say(other)}, never below {say(floor)})"
        if fn == "min" and len(args) == 2 and any(a[0] == "num" for a in args):
            cap, other = (args[0], args[1]) if args[0][0] == "num" else (args[1], args[0])
            return f"({say(other)}, at most {say(cap)})"
        words = {"min": "the lowest of", "max": "the highest of", "ceil": "rounded up:", "floor": "rounded down:", "abs": "the size of"}
        if fn == "round":
            return f"{say(args[0], 7)} rounded" + (f" to {say(args[1])} decimals" if len(args) > 1 else "")
        if fn == "between":
            return wrap(f"{say(args[0], 5)} is between {say(args[1])} and {say(args[2])}", 4)
        return f"{words[fn]} {', '.join(say(a, 7) for a in args)}"
    return "?"


def explain_definition(d: dict[str, Any]) -> str:
    try:
        words = say(parse(d.get("expression") or ""))
    except FormulaError as exc:
        return f"Price formula that does not read: {exc}"
    when = "one-time charge" if d.get("charge") == "one_time" else "a month"
    return f"{d.get('label') or 'Price formula'} ({when}): {words}"


# ---------------------------------------------------------------- checking against a product


def _names(design: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Every name a formula may use -> what it is."""
    out: dict[str, dict[str, Any]] = {}
    for comp in design.get("components", []):
        out[_key(comp["name"])] = {"name": comp["name"], "type": "component", "max": comp.get("max_qty") or 1}
    for attr in design.get("attributes", []):
        out[_key(attr["name"])] = {"name": attr["name"], "type": "attribute", "attr": attr}
    return out


def _samples(design: dict[str, Any], node: tuple, name: str) -> list[Any]:
    """Values worth showing for one name in the preview: every value of a list, the range ends and the
    thresholds the formula itself compares against, how many of a child product."""
    info = _names(design)[_key(name)]
    if info["type"] == "component":
        return list(range(0, min(int(info["max"]), 3) + 1))
    attr = info["attr"]
    if not is_number(attr):
        return list(attr["values"])[:6]
    points: set[float] = set()
    for n in _walk(node):
        pair = (n[2], n[3]) if n[0] in ("cmp", "bin") else None
        if not pair or not any(p[0] == "ref" and _key(p[1]) == _key(name) for p in pair):
            continue
        for p in pair:
            if p[0] != "num":
                continue
            if n[0] == "bin":  # an allowance (x - 100): at it and past it
                points |= {p[1], p[1] * 2}
            elif n[1] in (">", "<="):  # either side of the threshold
                points |= {p[1], p[1] + 1}
            elif n[1] in (">=", "<"):
                points |= {p[1], max(p[1] - 1, 0)}
            else:
                points.add(p[1])
    for bound in (attr.get("min"), attr.get("max")):
        if bound is not None:
            points.add(float(bound))
    if attr.get("default") not in (None, ""):
        from app.crm_core import product_config_service as pc

        d = pc._num(attr["default"])
        if d is not None:
            points.add(d)
    if not points:
        points = {1.0, 10.0, 100.0}
    low, high = attr.get("min"), attr.get("max")
    chosen = sorted(p for p in points if (low is None or p >= low) and (high is None or p <= high))
    if len(chosen) > 6:
        chosen = [chosen[0]] + chosen[1:-1][:: max(1, len(chosen) // 5)][:4] + [chosen[-1]]
    unit = f" {attr['unit']}" if attr.get("unit") else ""
    return [f"{_fmt(p)}{unit}" for p in chosen]


def _defaults(design: dict[str, Any]) -> dict[str, Any]:
    picks: dict[str, Any] = {}
    for comp in design.get("components", []):
        qty = max(comp.get("min_qty") or 0, comp.get("default_qty") or 0)
        if qty and not comp.get("slot"):
            picks[comp["name"]] = str(qty)
    for attr in design.get("attributes", []):
        if attr.get("kind") == "pricing" and attr.get("default") not in (None, ""):
            picks[attr["name"]] = attr["default"]
    return picks


def check(design: dict[str, Any], base: float, expression: str, label: str | None = None, charge: str | None = None,
          picks: dict[str, Any] | None = None, skip_rule_id: int | None = None) -> dict[str, Any]:
    """A formula read and checked against the product: every name it uses exists and is a pricing
    attribute or a child product, every value it compares a list attribute with is one of its values,
    and it works out to an amount. Raises FormulaError; returns the formula in words, what it uses,
    an impact preview (the amount for the values worth seeing) and, given picks, the amount for them."""
    from app.crm_core import product_config_service as pc

    expression = str(expression or "").translate(_SYMBOLS).strip()
    node = parse(expression)
    names = _names(design)
    used: list[str] = []
    for ref in references(node):
        info = names.get(_key(ref))
        if info is None:
            close = difflib.get_close_matches(ref.lower(), [v["name"].lower() for v in names.values()], n=1, cutoff=0.5)
            hint = next((v["name"] for v in names.values() if close and v["name"].lower() == close[0]), None)
            raise FormulaError(f"'{ref}' is not an attribute or child product of this product"
                               + (f" - did you mean [{hint}]?" if hint else f" - it has: {', '.join(v['name'] for v in names.values()) or 'none yet'}"))
        if info["type"] == "attribute" and info["attr"].get("kind") != "pricing":
            raise FormulaError(f"{info['name']} is a technical attribute - tick 💰 Pricing impact on it to use it in a price")
        used.append(info["name"])
    for n in _walk(node):
        pairs = [(n[2], n[3]), (n[3], n[2])] if n[0] == "cmp" and n[1] in ("=", "!=") else [(n[1], i) for i in n[2]] if n[0] == "in" else []
        for ref, other in pairs:
            if ref[0] != "ref" or other[0] != "str":
                continue
            attr = names.get(_key(ref[1]), {}).get("attr")
            if attr and is_number(attr):
                number = pc.in_unit(other[1], attr.get("unit")) if attr.get("unit") else pc._num(other[1])
                raise FormulaError(f"{attr['name']} is worked with as a number{' in ' + attr['unit'] if attr.get('unit') else ''} - "
                                   + (f"write {_fmt(number).replace(',', '')} instead of \"{other[1]}\"" if number is not None else f"compare it with a number, not \"{other[1]}\""))
            if attr and attr.get("values") and _key(other[1]) not in {_key(v) for v in attr["values"]}:
                raise FormulaError(f"'{other[1]}' is not a value of {attr['name']} ({', '.join(attr['values'])})")
    shown = used[:3]
    defaults = _defaults(design)
    others = dict(design, price_rules=[r for r in design.get("price_rules") or [] if skip_rule_id is None or r.get("id") != skip_rule_id])
    rows: list[dict[str, Any]] = []
    combos = list(itertools.product(*[_samples(design, node, n) for n in shown]))[:24] if shown else [()]
    for combo in combos:
        row_picks = {**defaults, **{n: (str(v) if not isinstance(v, str) else v) for n, v in zip(shown, combo)}}
        subtotal, _ = pc.price(base, others, row_picks)
        result = evaluate(node, values_for(design, row_picks), base, subtotal)
        if isinstance(result, bool) or isinstance(result, str):
            what = "yes / no" if isinstance(result, bool) else f"the text '{result}'"
            raise FormulaError(f"This works out to {what}, not an amount - e.g. if(<condition>, 500, 0)")
        rows.append({"picks": dict(zip(shown, combo)), "amount": round(_n(result), 2)})
    charge = charge if charge in CHARGES else "recurring"
    out = {"expression": expression, "label": (label or "").strip()[:120] or _label(used), "charge": charge, "uses": used,
           "explanation": "", "preview": rows}
    out["explanation"] = explain_definition(out)
    if picks is not None:
        merged = {**defaults, **{k: v for k, v in picks.items() if v not in (None, "")}}
        subtotal, _ = pc.price(base, others, merged)
        out["amount"] = round(_n(evaluate(node, values_for(design, merged), base, subtotal)), 2)
    return out


def _label(used: list[str]) -> str:
    return f"{' & '.join(used[:2])} pricing" if used else "Price adjustment"


# ---------------------------------------------------------------- plain English -> formula (built-in reader)

_ONE_TIME = re.compile(r"\b(one[- ]?time|once|one[- ]?off|installation|install|set[- ]?up|activation)\b(\s+(charge|fee))?\s*:?", re.I)
_MONTHLY = re.compile(r"\b(per|a|each|every)\s+month\b|/\s*month\b|\bmonthly\b|\bmrc\b", re.I)
_AMOUNT = r"(?P<sign>[+-])?\s*(?:₹|rs\.?|inr)?\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<pct>%|percent)?"


class _Reader:
    def __init__(self, design: dict[str, Any]):
        self.names = sorted(_names(design).values(), key=lambda v: -len(v["name"]))
        self.attrs = [v for v in self.names if v["type"] == "attribute" and v["attr"].get("kind") == "pricing"]
        self.lists = [v for v in self.attrs if not is_number(v["attr"])]

    @staticmethod
    def ref(name: str) -> str:
        return name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) and name.lower() not in FUNCTIONS and name.lower() not in VARIABLES \
            and name.lower() not in ("and", "or", "not", "in", "true", "false", "yes", "no") else f"[{name}]"

    @staticmethod
    def lit(value: str) -> str:
        return f'"{value}"' if '"' not in value else f"'{value}'"

    def find(self, text: str) -> dict[str, Any] | None:
        """The name (longest first) a phrase mentions - with or without its '(unit)'."""
        for v in self.names:
            plain = re.sub(r"\s*\([^)]*\)\s*$", "", v["name"])
            if _key(v["name"]) and (_key(v["name"]) == _key(text) or _key(plain) == _key(text)):
                return v
        for v in self.names:
            plain = re.sub(r"\s*\([^)]*\)\s*$", "", v["name"])
            if re.search(rf"(?<!\w){re.escape(plain.lower())}(?!\w)", text.lower()):
                return v
        return None

    def value_of(self, attr: dict[str, Any], text: str) -> str | None:
        for v in sorted(attr.get("values") or [], key=len, reverse=True):
            if _key(v) == _key(text) or re.search(rf"(?<![\w]){re.escape(v.lower())}(?![\w])", text.lower()):
                return v
        return None

    def cond(self, text: str) -> str | None:
        text = text.strip().strip(",").strip()
        if not text:
            return None
        # longer phrases first - "is at least" must not be read as "is"
        if m := re.match(r"^(?P<a>.+?)\s+(?P<op>is one of|is between|is at least|is at most|is more than|is less than|is greater than|is over|"
                         r"is above|is below|is under|is not|isn't|equals|is|==|!=|>=|<=|=|>|<|one of|in|between|at least|at most|more than|"
                         r"greater than|less than|over|above|below|under)\s+(?P<b>.+)$", text, re.I):
            target, op, rhs = self.find(m.group("a")), m.group("op").lower(), m.group("b").strip()
            if target is not None:
                ref = self.ref(target["name"])
                attr = target.get("attr") or {}
                if op in ("is between", "between"):
                    nums = re.findall(r"\d+(?:\.\d+)?", rhs.replace(",", ""))
                    return f"between({ref}, {nums[0]}, {nums[1]})" if len(nums) >= 2 else None
                if op in ("is one of", "one of", "in"):
                    items = [self.value_of(attr, p) or p.strip() for p in re.split(r",|\bor\b", rhs) if p.strip()]
                    return f"{ref} in ({', '.join(self.lit(i) for i in items)})"
                symbol = {"is not": "!=", "isn't": "!=", "!=": "!=", "is at least": ">=", "at least": ">=", ">=": ">=", "is at most": "<=",
                          "at most": "<=", "<=": "<=", "is more than": ">", "more than": ">", "is over": ">", "over": ">", "is above": ">",
                          "above": ">", "is greater than": ">", "greater than": ">", ">": ">", "is less than": "<", "less than": "<",
                          "is below": "<", "below": "<", "is under": "<", "under": "<", "<": "<"}.get(op, "=")
                if target["type"] == "component" and re.match(r"^(chosen|selected|included|added|taken|present)$", rhs, re.I):
                    return f"{ref} {'=' if symbol == '!=' else '>'} 0"
                if attr and is_number(attr):  # "Bandwidth is at least 2 Mbps" -> Bandwidth >= 2048 (in its unit)
                    from app.crm_core import product_config_service as pc

                    number = pc.in_unit(rhs, attr.get("unit")) if attr.get("unit") else pc._num(rhs)
                    return f"{ref} {symbol} {_fmt(number).replace(',', '')}" if number is not None else None
                if attr.get("values"):
                    value = self.value_of(attr, rhs)
                    if value is not None:
                        return f"{ref} {symbol} {self.lit(value)}"
                num = re.match(r"^(\d+(?:\.\d+)?)", rhs.replace(",", ""))
                if num and target["type"] == "component":
                    return f"{ref} {symbol} {num.group(1)}"
                return f"{ref} {symbol} {self.lit(rhs)}"
        if m := re.match(r"^(?:the\s+)?(?P<a>.+?)\s+is\s+(?:chosen|selected|included|added|taken)$|^(?:with|has|having)\s+(?:an?\s+|the\s+)?(?P<b>.+)$", text, re.I):
            target = self.find(m.group("a") or m.group("b"))
            if target and target["type"] == "component":
                return f"{self.ref(target['name'])} > 0"
        # a bare value ("for Fiber", "36 months") names the one attribute it belongs to
        owners = [(a, self.value_of(a["attr"], text)) for a in self.lists]
        owners = [(a, v) for a, v in owners if v is not None and _key(v) == _key(re.sub(r"^(the|a|an)\s+", "", text, flags=re.I))]
        if len(owners) == 1:
            return f"{self.ref(owners[0][0]['name'])} = {self.lit(owners[0][1])}"
        target = self.find(text)
        if target and target["type"] == "component":
            return f"{self.ref(target['name'])} > 0"
        return None

    def conds(self, text: str) -> str | None:
        parts = re.split(r"\s+(and|or)\s+", text.strip(), flags=re.I)
        out: list[str] = []
        for i, part in enumerate(parts):
            if i % 2:
                out.append(part.lower())
                continue
            c = self.cond(part)
            if c is None:
                return None
            out.append(c)
        return " ".join(out)

    def amount(self, text: str, negate: bool = False) -> str | None:
        """'+500', '10%', '10% off', 'a 500 discount', '2 per Mbps' ... -> an amount formula."""
        t = text.strip().rstrip(".").strip()
        off = bool(re.search(r"\b(off|discount|cheaper|less|reduction|rebate|waive[ds]?)\b", t, re.I)) or negate
        t = re.sub(r"\b(a|an|of|the|discount|off|cheaper|less|reduction|rebate|surcharge|extra|more|add|charge|plus|minus|by)\b", " ", t, flags=re.I)
        of_total = bool(re.search(r"\b(total|subtotal|price so far)\b", text, re.I))
        t = re.sub(r"\b(total|subtotal|price so far|price|base|base price)\b", " ", t, flags=re.I).strip()
        if m := re.match(rf"^{_AMOUNT}\s*(?:each|per\s+(?P<per>.+?))?(?:\s+(?:above|over|beyond)\s+(?P<over>\d+(?:\.\d+)?))?\s*$", t, re.I):
            num = m.group("num")
            sign = -1 if (m.group("sign") == "-") != off else 1
            if m.group("pct"):
                expr = f"{num}% * {'subtotal' if of_total else 'base'}"
            elif m.group("per") or re.search(r"\beach\b", t, re.I):
                target = self.find(m.group("per") or "") if m.group("per") else None
                if target is None and m.group("per"):
                    unit = m.group("per").strip().lower()
                    target = next((a for a in self.attrs if _key(a["attr"].get("unit") or "") == _key(unit)
                                   or _key(re.sub(r".*\(([^)]*)\).*", r"\1", a["name"])) == _key(unit)), None)
                if target is None:
                    return None
                qty = self.ref(target["name"])
                if m.group("over"):
                    qty = f"max(0, {qty} - {m.group('over')})"
                expr = f"{qty} * {num}"
            else:
                expr = num
            return f"-{expr}" if sign < 0 and not expr.startswith("-") else expr
        return None

    def clause(self, text: str) -> tuple[str | None, str | None]:
        """(condition, amount) for one clause; condition None = always / otherwise."""
        t = text.strip().rstrip(".").strip()
        if m := re.match(r"^(?:otherwise|else|for everything else|in all other cases)\s*[,:]?\s*(?P<a>.+)$", t, re.I):
            return None, self.amount(m.group("a"))
        if m := re.match(r"^(?:if|when|where|for)\s+(?P<c>.+?)\s*(?:,|\bthen\b|:|->|→)\s*(?P<a>.+)$", t, re.I):
            return self.conds(m.group("c")) or "", self.amount(m.group("a"))
        if m := re.match(r"^(?P<a>.+?)\s+(?:if|when|where|for)\s+(?P<c>.+)$", t, re.I):
            amount = self.amount(m.group("a"))
            if amount is not None:
                return self.conds(m.group("c")) or "", amount
        if m := re.match(r"^(?P<c>[^:]+?)\s*(?::|->|→)\s*(?P<a>.+)$", t):
            return self.conds(m.group("c")) or "", self.amount(m.group("a"))
        return None, self.amount(t)

    def read(self, text: str) -> str | None:
        clauses = [c for c in re.split(r";|\n|\.\s+(?=[A-Z])|,\s*(?=otherwise\b|else\b)", text, flags=re.I) if c.strip()]
        branches: list[tuple[str, str]] = []
        fallback: str | None = None
        for c in clauses:
            cond, amount = self.clause(c)
            if amount is None or cond == "":
                return None
            if cond is None:
                if fallback is not None:
                    return None
                fallback = amount
            else:
                branches.append((cond, amount))
        if not branches:
            return fallback
        expr = fallback or "0"
        for cond, amount in reversed(branches):
            expr = f"if({cond}, {amount}, {expr})"
        return expr


def read_english(design: dict[str, Any], text: str) -> tuple[str | None, str | None]:
    """(formula, charge) from plain English - None when it can't be read."""
    t = str(text or "").translate(_SYMBOLS)
    t = re.sub(r"(?<=\d),(?=\d{3}\b)", "", t)  # 1,500 -> 1500
    charge = None
    if _ONE_TIME.search(t):
        charge = "one_time"
        t = _ONE_TIME.sub(" ", t)
    elif _MONTHLY.search(t):
        charge = "recurring"
    t = _MONTHLY.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip(" .")
    return _Reader(design).read(t), charge


_PROMPT = """You write price formulas for a telecom product modeller. Reply with JSON only:
{"expression": str, "label": str (a short line label for the quote, e.g. "Fiber 36-month discount"), "charge": "recurring" | "one_time"}
or {"error": str} when the request is not a price impact of this product.

The formula language (nothing else is allowed):
- [Name] - an attribute or child product of the product, exactly as listed. An attribute is its value (text) or,
  for a numeric attribute, its number in the attribute's unit. A child product is how many are chosen (0 = not chosen).
- base = the product's base price; subtotal = the monthly price worked out so far.
- numbers (no thousands separators), 10% (= 0.10), "text" in double quotes - text values must be one of the attribute's values.
- + - * /   = != < <= > >=   and or not   [X] in ("A", "B")
- if(condition, then, otherwise), min, max, round(x, digits), ceil, floor, abs, between(x, low, high)
The formula works out to the amount added to the price (negative = discount). A percentage discount is e.g. -10% * base.
Tiers: nested if - if([Bandwidth] <= 100, 0, if([Bandwidth] <= 1000, 500, 1500)).
Per unit above an allowance: max(0, [Bandwidth] - 100) * 2.
"""


def ai_formula(db, tenant_id: int, design: dict[str, Any], product: str, base: float, text: str) -> dict[str, Any] | None:
    """The tenant's AI model, when one is set up: {"expression", "label", "charge"} or {"error"} - None = no model."""
    from app.crm_core import llm

    target = llm.resolve(db, tenant_id)
    if target is None:
        return None
    lines = []
    for comp in design.get("components", []):
        lines.append(f"- [{comp['name']}] child product, 0 to {comp.get('max_qty') or 1}")
    for attr in design.get("attributes", []):
        if attr.get("kind") != "pricing":
            continue
        domain = (f"values: {', '.join(attr['values'])}" if not is_number(attr) else
                  f"a number{' in ' + attr['unit'] if attr.get('unit') else ''} (compare it with numbers, never text)"
                  + (f", {attr['min']:g} to {attr['max']:g}" if attr.get("min") is not None and attr.get("max") is not None else ""))
        lines.append(f"- [{attr['name']}] attribute, {domain}")
    listing = f"Product: {product} (base price {base:g})\nNames:\n" + ("\n".join(lines) or "(none)")
    reply = llm.chat_json(target, _PROMPT, f"{listing}\n\nPrice impact: {text}")
    return reply if isinstance(reply, dict) else None


# ---------------------------------------------------------------- the product's formulas (ProductRule rule_type "price")


def rules_of(db, tenant_id: int, offering_name: str) -> list[dict[str, Any]]:
    from app.models.product_model import ProductRule

    rows = (db.query(ProductRule)
            .filter(ProductRule.tenant_id == tenant_id, ProductRule.offering_name.ilike(offering_name or ""),
                    ProductRule.rule_type == "price", ProductRule.is_active.is_(True))
            .order_by(ProductRule.id).all())
    out = []
    for r in rows:
        try:
            d = json.loads(r.definition_json or "{}")
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict) and d.get("expression"):
            out.append({"id": r.id, "expression": d["expression"], "label": d.get("label") or "", "charge": d.get("charge") or "recurring"})
    return out


def _context(db, tenant_id: int, offering_name: str) -> tuple[dict[str, Any], float]:
    from app.crm_core import model_lifecycle
    from app.crm_core import product_config_service as pc

    return pc.schema(db, tenant_id, offering_name, workspace=True), model_lifecycle.base_of(db, tenant_id, offering_name)


EXAMPLES = ['add 500 if Access Medium is Fiber', '10% off when Contract Term is 36 months',
            '2 per Kbps above 2048', 'one-time 1500 installation if CPE is chosen',
            'if Bandwidth is at most 2048 then 0; if Bandwidth is at most 10240 then 800; otherwise 2000']


def compile_text(db, tenant_id: int, offering_name: str, text: str, label: str | None = None, charge: str | None = None,
                 picks: dict[str, Any] | None = None, rule_id: int | None = None, use_ai: bool = True) -> dict[str, Any]:
    """A formula - or a price impact described in plain English - as a checked formula.
    A formula is taken as written; plain English goes to the AI model when one is set up, else (or when
    its answer doesn't check out) to the built-in reader."""
    design, base = _context(db, tenant_id, offering_name)
    raw = str(text or "").strip()
    if not raw:
        raise FormulaError("Describe the price impact, or write its formula")
    if m := re.match(r"^\s*one[- ]?time\s*:\s*(.+)$", raw, re.I | re.S):
        raw, charge = m.group(1).strip(), "one_time"
    problem = ""
    try:
        return {**check(design, base, raw, label, charge, picks, rule_id), "engine": "formula", "text": raw}
    except FormulaError as exc:
        problem = str(exc)
        if "[" in raw or re.search(r"\b(?:if|min|max|round|ceil|floor|abs|between)\s*\(", raw, re.I):
            raise  # it is a formula - say what is wrong with it
    if use_ai:
        try:
            reply = ai_formula(db, tenant_id, design, offering_name, base, raw)
        except Exception as exc:  # noqa: BLE001 - the built-in reader is next
            reply, problem = None, f"AI unavailable ({type(exc).__name__})"
        if reply and reply.get("expression"):
            try:
                return {**check(design, base, str(reply["expression"]), label or reply.get("label"), charge or reply.get("charge"), picks, rule_id),
                        "engine": "ai", "text": raw}
            except FormulaError as exc:
                problem = f"the AI's formula didn't check out: {exc}"
        elif reply and reply.get("error"):
            problem = str(reply["error"])
    formula, said_charge = read_english(design, raw)
    if formula:
        try:
            return {**check(design, base, formula, label, charge or said_charge, picks, rule_id), "engine": "rules", "text": raw}
        except FormulaError as exc:
            problem = str(exc)
    raise FormulaError("I couldn't turn that into a price formula" + (f" ({problem})" if problem else "")
                       + f". Try e.g. \"{EXAMPLES[0]}\", \"{EXAMPLES[1]}\" or a formula like if([Access Medium] = \"Fiber\", 500, 0)")


def save(db, tenant_id: int, offering_name: str, expression: str, label: str | None, charge: str | None, described_as: str = "",
         rule_id: int | None = None):
    """Stores a checked formula. Its rule text is the formula itself ("one-time: ..." for a one-time
    charge), so a spreadsheet export imports back to exactly the same formula."""
    from app.models.product_model import ProductRule

    design, base = _context(db, tenant_id, offering_name)
    checked = check(design, base, expression, label, charge, skip_rule_id=rule_id)
    row = db.get(ProductRule, rule_id) if rule_id else None
    if row is None or row.tenant_id != tenant_id or row.rule_type != "price":
        row = ProductRule(tenant_id=tenant_id, offering_name=offering_name, rule_type="price")
        db.add(row)
    definition = {"expression": checked["expression"], "label": checked["label"], "charge": checked["charge"]}
    if described_as and described_as.strip() != checked["expression"]:
        definition["described_as"] = described_as.strip()[:500]
    row.definition_json = json.dumps(definition)
    row.source_text = ("one-time: " if checked["charge"] == "one_time" else "") + checked["expression"]
    row.message = checked["label"][:300]
    db.flush()
    return row
