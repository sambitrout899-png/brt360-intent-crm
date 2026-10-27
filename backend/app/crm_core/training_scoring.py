"""Training scoring - the pure calculations of BRD section 10.3 (ANL-001..003,
ANL-006, ANL-007). No database access: every function takes plain values so
the formulas are table-tested on their own (tests/test_training_scoring.py).

Questions are dicts: {item_key, type, measure, options, correct_options,
points, objective, required}. Answers are {item_key: value} where value is an
option index (single / true_false), a list of indexes (multi), a number
(likert5 / rating10 / nps) or text.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

QUESTION_TYPES = ("single", "multi", "true_false", "likert5", "rating10", "short_text", "long_text", "nps")
MEASURES = ("knowledge", "confidence", "outcome", "feedback")
CHOICE_TYPES = ("single", "multi", "true_false")
NUMBER_RANGES = {"likert5": (1, 5), "rating10": (1, 10), "nps": (0, 10)}


# ---------------------------------------------------------------- answer checks (ASM-002)


def answer_problem(question: dict, value) -> str | None:
    """Why one answer is not valid for its question (None when fine)."""
    kind = question.get("type", "single")
    if value is None or value == "" or value == []:
        return "Answer this question" if question.get("required", True) else None
    if kind in ("single", "true_false"):
        count = 2 if kind == "true_false" else len(question.get("options") or [])
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value < count:
            return "Choose one of the options"
    elif kind == "multi":
        count = len(question.get("options") or [])
        if not isinstance(value, list) or not value or any(not isinstance(v, int) or isinstance(v, bool) or not 0 <= v < count for v in value):
            return "Choose at least one of the options"
    elif kind in NUMBER_RANGES:
        low, high = NUMBER_RANGES[kind]
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not low <= value <= high:
            return f"Choose a value from {low} to {high}"
    elif kind in ("short_text", "long_text"):
        limit = 500 if kind == "short_text" else 4000
        if not isinstance(value, str) or len(value) > limit:
            return f"Enter up to {limit} characters"
    else:
        return "Unknown question type"
    return None


def is_correct(question: dict, value) -> bool:
    """Single/true-false: the chosen option is a correct one. Multi: exactly
    the correct set (all or nothing)."""
    correct = sorted(set(question.get("correct_options") or []))
    if not correct or value is None:
        return False
    if question.get("type") == "multi":
        return isinstance(value, list) and sorted(set(value)) == correct
    return value in correct


# ---------------------------------------------------------------- participant scores (10.3)


def knowledge_score(questions: Iterable[dict], answers: dict, keys: set[str] | None = None) -> float | None:
    """K = sum(points awarded) / sum(points possible) x 100 over Knowledge
    questions (restricted to `keys` - the paired item_keys - when given)."""
    awarded = possible = 0.0
    for q in questions:
        if q.get("measure") != "knowledge" or (keys is not None and q["item_key"] not in keys):
            continue
        points = float(q.get("points") or 1)
        possible += points
        if is_correct(q, answers.get(q["item_key"])):
            awarded += points
    return None if possible == 0 else awarded / possible * 100


def confidence_score(questions: Iterable[dict], answers: dict, keys: set[str] | None = None) -> float | None:
    """C = mean of Likert 1-5 answers on Confidence questions."""
    values = [answers.get(q["item_key"]) for q in questions
              if q.get("measure") == "confidence" and q.get("type") == "likert5"
              and (keys is None or q["item_key"] in keys)]
    values = [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return sum(values) / len(values) if values else None


def objective_percent(questions: Iterable[dict], answers: dict, objective: str) -> float | None:
    """% correct (points-weighted) on the Knowledge questions mapped to one objective."""
    mapped = [q for q in questions if q.get("objective") == objective]
    return knowledge_score(mapped, answers)


def objective_confidence(questions: Iterable[dict], answers: dict, objective: str) -> float | None:
    return confidence_score([q for q in questions if q.get("objective") == objective], answers)


def paired_keys(pre: Iterable[dict], post: Iterable[dict], measure: str) -> set[str]:
    """item_keys that appear, with the same measure, in both templates (ASM-004)."""
    before = {q["item_key"] for q in pre if q.get("measure") == measure}
    return {q["item_key"] for q in post if q.get("measure") == measure and q["item_key"] in before}


def delta(pre: float | None, post: float | None) -> float | None:
    return None if pre is None or post is None else post - pre


def normalized_gain(k_pre: float | None, k_post: float | None) -> float | None:
    """Hake gain g = (K_post - K_pre) / (100 - K_pre); excluded (None) at K_pre = 100."""
    if k_pre is None or k_post is None or k_pre >= 100:
        return None
    return (k_post - k_pre) / (100 - k_pre)


def gain_band(g: float | None) -> str | None:
    if g is None:
        return None
    if g < 0:
        return "Decline"
    if g < 0.3:
        return "Low"
    if g < 0.7:
        return "Medium"
    return "High"


def achieved(post_percent: float | None, pass_mark: float) -> bool | None:
    """Learning outcome achievement: post % correct on the objective >= pass mark."""
    return None if post_percent is None else post_percent >= pass_mark


def mean(values: Iterable[float | None]) -> float | None:
    """Aggregates are the mean of participant-level values (not pooled totals)."""
    clean = [float(v) for v in values if v is not None]
    return sum(clean) / len(clean) if clean else None


def display(value: float | None, places: int = 1) -> float | None:
    """Store full precision; display to 1 decimal."""
    return None if value is None else round(value, places)


# ---------------------------------------------------------------- statistics (ANL-006)


def _betacf(a: float, b: float, x: float) -> float:
    qab, qap, qam = a + b, a + 1, a - 1
    c, d = 1.0, 1 - qab * x / qap
    d = 1 / (d if abs(d) > 1e-30 else 1e-30)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1 + aa * d
        d = 1 / (d if abs(d) > 1e-30 else 1e-30)
        c = 1 + aa / c if abs(1 + aa / c) > 1e-30 else 1e-30
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1 + aa * d
        d = 1 / (d if abs(d) > 1e-30 else 1e-30)
        c = 1 + aa / c if abs(1 + aa / c) > 1e-30 else 1e-30
        step = d * c
        h *= step
        if abs(step - 1) < 1e-12:
            break
    return h


def _incomplete_beta(a: float, b: float, x: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x))
    if x < (a + 1) / (a + b + 2):
        return front * _betacf(a, b, x) / a
    return 1 - front * _betacf(b, a, 1 - x) / b


def t_two_sided_p(t: float, df: int) -> float:
    """Two-sided p-value of Student's t."""
    if df <= 0:
        return 1.0
    return _incomplete_beta(df / 2, 0.5, df / (df + t * t))


def _normal_two_sided_p(z: float) -> float:
    return math.erfc(abs(z) / math.sqrt(2))


def _ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def wilcoxon_signed_rank(diffs: Sequence[float]) -> tuple[float, float]:
    """(W, two-sided p). Zero differences are dropped; exact distribution when
    there are no tied ranks, normal approximation (tie-corrected) otherwise."""
    nonzero = [d for d in diffs if d != 0]
    n = len(nonzero)
    if n == 0:
        return 0.0, 1.0
    ranks = _ranks([abs(d) for d in nonzero])
    w_plus = sum(r for r, d in zip(ranks, nonzero) if d > 0)
    w = min(w_plus, n * (n + 1) / 2 - w_plus)
    if len(set(ranks)) == n and n <= 50:
        # exact: count sign assignments whose positive-rank sum <= w
        total = n * (n + 1) // 2
        ways = [0] * (total + 1)
        ways[0] = 1
        for r in range(1, n + 1):
            for s in range(total, r - 1, -1):
                ways[s] += ways[s - r]
        p = 2 * sum(ways[: int(math.floor(w)) + 1]) / 2 ** n
        return w_plus, min(1.0, p)
    mu = n * (n + 1) / 4
    ties = {}
    for r in ranks:
        ties[r] = ties.get(r, 0) + 1
    sigma = math.sqrt(n * (n + 1) * (2 * n + 1) / 24 - sum(t ** 3 - t for t in ties.values()) / 48)
    if sigma == 0:
        return w_plus, 1.0
    z = (w_plus - mu) / sigma
    return w_plus, _normal_two_sided_p(z)


def significance(pre: Sequence[float], post: Sequence[float], min_n: int = 5) -> dict | None:
    """Paired t-test (n >= 30) or Wilcoxon signed-rank (n < 30) with Cohen's d
    (mean difference / SD of differences). Suppressed (None) when n < 5."""
    pairs = [(a, b) for a, b in zip(pre, post) if a is not None and b is not None]
    n = len(pairs)
    if n < min_n:
        return None
    diffs = [b - a for a, b in pairs]
    mean_diff = sum(diffs) / n
    sd = math.sqrt(sum((d - mean_diff) ** 2 for d in diffs) / (n - 1)) if n > 1 else 0.0
    cohens_d = None if sd == 0 else mean_diff / sd
    if n >= 30:
        if sd == 0:
            p = 0.0 if mean_diff != 0 else 1.0
        else:
            p = t_two_sided_p(mean_diff / (sd / math.sqrt(n)), n - 1)
        test = "paired t-test"
    else:
        _, p = wilcoxon_signed_rank(diffs)
        test = "Wilcoxon signed-rank"
    return {"test": test, "n": n, "p_value": p, "cohens_d": cohens_d, "mean_difference": mean_diff,
            "significant": p < 0.05, "label": "significant at 95%" if p < 0.05 else "not significant at 95%"}


# ---------------------------------------------------------------- flags (ANL-007)


def flags(objective_post: dict[str, float | None], pass_mark: float, confidence_change: float | None,
          knowledge_change: float | None) -> list[dict]:
    out = [{"flag": "Knowledge gap", "objective": code, "detail": f"Post {pct:.1f}% is below the pass mark {pass_mark:.0f}%"}
           for code, pct in objective_post.items() if pct is not None and pct < pass_mark]
    if confidence_change is not None and knowledge_change is not None and confidence_change >= 1 and knowledge_change < 10:
        out.append({"flag": "Confidence-competence mismatch", "objective": None,
                    "detail": f"Confidence rose {confidence_change:.1f} points but knowledge only {knowledge_change:.1f} points"})
    return out
