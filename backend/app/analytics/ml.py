"""Machine learning in plain Python - no numpy, nothing to install on the server.

  forecast()   Holt-Winters exponential smoothing (level + damped trend +
               season), parameters chosen by grid search on the history,
               prediction intervals from the fit's own errors, and a
               back-test on the last periods against the naive forecast.
  anomalies()  robust z-scores (median / MAD) - spikes and drops in a series.
  Logistic     logistic regression (standardised features, L2, gradient
               descent) with per-record drivers; auc() to judge it.
  kmeans()     k-means++ with k picked by silhouette.

Small data is the norm in a CRM: every model says how much it learned from,
and the callers fall back to transparent rules when it is too little.
"""
from __future__ import annotations

import math
import random
import statistics
from typing import Any, Sequence

SEASON = {"month": 12, "quarter": 4, "day": 7, "hour": 24, "weekday": 0, "week": 0, "year": 0}


# ---------------------------------------------------------------- forecasting


def _smooth(y: Sequence[float], m: int, a: float, b: float, g: float, phi: float, horizon: int) -> tuple[list[float], list[float]]:
    """One-step-ahead fits and the forecast. m = 0: no season."""
    n = len(y)
    if m:
        level = sum(y[:m]) / m
        trend = (sum(y[m:2 * m]) / m - level) / m if n >= 2 * m else 0.0
        season = [y[i] - level for i in range(m)]
    else:
        level = y[0]
        trend = (y[min(n - 1, 3)] - y[0]) / min(n - 1, 3) if n > 1 else 0.0
        season = []
    fitted = []
    for t, yt in enumerate(y):
        s = season[t % m] if m else 0.0
        fitted.append(level + phi * trend + s)
        prev = level
        level = a * (yt - s) + (1 - a) * (level + phi * trend)
        trend = b * (level - prev) + (1 - b) * phi * trend
        if m:
            season[t % m] = g * (yt - level) + (1 - g) * s
    out, damp = [], 0.0
    for k in range(1, horizon + 1):
        damp += phi ** k
        out.append(level + damp * trend + (season[(n + k - 1) % m] if m else 0.0))
    return fitted, out


def _fit(y: Sequence[float], m: int) -> dict[str, Any]:
    best: dict[str, Any] | None = None
    start = m if m else 1
    for a in (0.1, 0.3, 0.5, 0.7, 0.9):
        for b in (0.0, 0.05, 0.2, 0.4):
            for phi in (0.85, 0.95, 1.0):
                for g in ((0.05, 0.2, 0.4) if m else (0.0,)):
                    fitted, _ = _smooth(y, m, a, b, g, phi, 0)
                    sse = sum((y[t] - fitted[t]) ** 2 for t in range(start, len(y)))
                    if best is None or sse < best["sse"] - 1e-9:
                        best = {"a": a, "b": b, "g": g, "phi": phi, "sse": sse, "fitted": fitted}
    assert best is not None
    return best


def _season_for(n: int, grain: str) -> int:
    m = SEASON.get(grain, 0)
    return m if m and n >= 2 * m + 2 else 0


def predict(y: Sequence[float], grain: str, horizon: int) -> dict[str, Any]:
    n = len(y)
    if n == 0:
        return {"forecast": [], "fitted": [], "method": "none", "sigma": 0.0}
    if n < 3:
        mean = sum(y) / n
        return {"forecast": [mean] * horizon, "fitted": [mean] * n, "method": "average (too little history)",
                "sigma": statistics.pstdev(y) if n > 1 else abs(mean) * 0.25}
    m = _season_for(n, grain)
    best = _fit(y, m)
    _, fc = _smooth(y, m, best["a"], best["b"], best["g"], best["phi"], horizon)
    start = m if m else 1
    errors = [y[t] - best["fitted"][t] for t in range(start, n)]
    sigma = math.sqrt(sum(e * e for e in errors) / max(1, len(errors) - 1)) if errors else 0.0
    method = ("Holt-Winters (level, trend, " + (f"{m}-period season)" if m else "no season)")) if best["b"] else \
        ("Seasonal exponential smoothing" if m else "Exponential smoothing")
    if best["phi"] < 1 and best["b"]:
        method += ", damped trend"
    return {"forecast": fc, "fitted": best["fitted"], "method": method, "sigma": sigma,
            "params": {k: best[k] for k in ("a", "b", "g", "phi")}, "season": m}


def _mape(actual: Sequence[float], pred: Sequence[float]) -> float | None:
    pairs = [(a, p) for a, p in zip(actual, pred) if abs(a) > 1e-9]
    return round(100 * sum(abs(a - p) / abs(a) for a, p in pairs) / len(pairs), 1) if pairs else None


def forecast(labels: list[str], values: list[float], grain: str, horizon: int, next_label: Any,
             non_negative: bool | None = None) -> dict[str, Any]:
    """History + forecast with a 95% band, the back-test's accuracy and the trend in words."""
    y = [float(v) for v in values]
    n = len(y)
    horizon = max(1, min(int(horizon), 36))
    non_negative = all(v >= 0 for v in y) if non_negative is None else non_negative
    fit = predict(y, grain, horizon)
    future, lab = [], labels[-1] if labels else ""
    for k, v in enumerate(fit["forecast"], 1):
        lab = next_label(lab)
        spread = 1.96 * fit["sigma"] * math.sqrt(k)
        lo, hi = v - spread, v + spread
        if non_negative:
            v, lo, hi = max(0.0, v), max(0.0, lo), max(0.0, hi)
        future.append({"period": lab, "value": round(v, 2), "lower": round(lo, 2), "upper": round(hi, 2)})

    accuracy: dict[str, Any] = {"holdout": 0}
    hold = min(6, max(1, n // 5)) if n >= 8 else 0
    if hold:
        back = predict(y[:-hold], grain, hold)["forecast"]
        actual = y[-hold:]
        naive = [y[-hold - 1]] * hold
        mae = sum(abs(a - p) for a, p in zip(actual, back)) / hold
        naive_mae = sum(abs(a - p) for a, p in zip(actual, naive)) / hold
        accuracy = {"holdout": hold, "mape": _mape(actual, back), "mae": round(mae, 2),
                    "skill_vs_naive": round(100 * (1 - mae / naive_mae), 1) if naive_mae > 1e-9 else None,
                    "backtest": [{"period": labels[-hold + i], "actual": round(actual[i], 2), "predicted": round(back[i], 2)} for i in range(hold)]}
    slope, r2 = linear_trend(y)
    mean = sum(y) / n if n else 0
    trend_pct = round(100 * slope / abs(mean), 1) if mean else None
    nxt = future[0]["value"] if future else None
    total_next = round(sum(f["value"] for f in future), 2)
    return {
        "history": [{"period": p, "value": round(v, 2), "fitted": round(f, 2)} for p, v, f in zip(labels, y, fit["fitted"])],
        "forecast": future, "method": fit["method"], "season": fit.get("season", 0), "accuracy": accuracy,
        "trend": {"per_period": round(slope, 4), "pct_of_mean": trend_pct, "r2": round(r2, 3),
                  "direction": "up" if trend_pct and trend_pct > 1 else "down" if trend_pct and trend_pct < -1 else "flat"},
        "next": nxt, "horizon_total": total_next, "points": n,
        "confidence": "low" if n < 8 else "medium" if n < 24 or (accuracy.get("mape") or 0) > 30 else "high",
    }


def linear_trend(y: Sequence[float]) -> tuple[float, float]:
    n = len(y)
    if n < 2:
        return 0.0, 0.0
    xm, ym = (n - 1) / 2, sum(y) / n
    sxx = sum((i - xm) ** 2 for i in range(n))
    sxy = sum((i - xm) * (v - ym) for i, v in enumerate(y))
    slope = sxy / sxx if sxx else 0.0
    ss_tot = sum((v - ym) ** 2 for v in y)
    ss_res = sum((v - (ym + slope * (i - xm))) ** 2 for i, v in enumerate(y))
    return slope, (1 - ss_res / ss_tot) if ss_tot else 0.0


def anomalies(labels: list[str], values: list[float], threshold: float = 3.5) -> list[dict[str, Any]]:
    """Points far from the series' robust centre (modified z-score = 0.6745 (x - median) / MAD)."""
    if len(values) < 6:
        return []
    med = statistics.median(values)
    mad = statistics.median([abs(v - med) for v in values])
    scale = mad if mad > 1e-9 else (statistics.pstdev(values) / 1.4826 or 0)
    if scale <= 1e-9:
        return []
    out = []
    for p, v in zip(labels, values):
        z = 0.6745 * (v - med) / scale
        if abs(z) >= threshold:
            out.append({"period": p, "value": round(v, 2), "expected": round(med, 2), "z": round(z, 2),
                        "kind": "spike" if z > 0 else "drop"})
    return out


# ---------------------------------------------------------------- classification


def _sigmoid(z: float) -> float:
    if z < -35:
        return 0.0
    if z > 35:
        return 1.0
    return 1 / (1 + math.exp(-z))


class Logistic:
    """Logistic regression on standardised features (L2-regularised, full-batch gradient descent)."""

    def __init__(self, names: list[str], l2: float = 0.5, iters: int = 600, lr: float = 0.5):
        self.names, self.l2, self.iters, self.lr = names, l2, iters, lr
        self.mean: list[float] = []
        self.std: list[float] = []
        self.w: list[float] = []
        self.b = 0.0

    def _z(self, x: Sequence[float]) -> list[float]:
        return [(v - m) / s for v, m, s in zip(x, self.mean, self.std)]

    def fit(self, X: list[Sequence[float]], y: list[int]) -> "Logistic":
        n, d = len(X), len(self.names)
        self.mean = [sum(r[j] for r in X) / n for j in range(d)]
        self.std = [max(1e-9, math.sqrt(sum((r[j] - self.mean[j]) ** 2 for r in X) / n)) or 1.0 for j in range(d)]
        self.std = [s if s > 1e-6 else 1.0 for s in self.std]
        Z = [self._z(r) for r in X]
        self.w, base = [0.0] * d, sum(y) / n
        self.b = math.log(base / (1 - base)) if 0 < base < 1 else 0.0
        for _ in range(self.iters):
            gw, gb = [0.0] * d, 0.0
            for z, t in zip(Z, y):
                err = _sigmoid(self.b + sum(w * v for w, v in zip(self.w, z))) - t
                gb += err
                for j in range(d):
                    gw[j] += err * z[j]
            self.b -= self.lr * gb / n
            self.w = [w - self.lr * (g / n + self.l2 * w / n) for w, g in zip(self.w, gw)]
        return self

    def proba(self, x: Sequence[float]) -> float:
        return _sigmoid(self.b + sum(w * v for w, v in zip(self.w, self._z(x))))

    def drivers(self, x: Sequence[float], top: int = 3) -> list[dict[str, Any]]:
        """What pushes this record's probability up most (weight x how far it sits from the average)."""
        parts = sorted(((w * v, name) for w, v, name in zip(self.w, self._z(x), self.names)), reverse=True)
        return [{"feature": name, "impact": round(c, 3)} for c, name in parts[:top] if c > 0.05]

    def importance(self) -> list[dict[str, Any]]:
        total = sum(abs(w) for w in self.w) or 1.0
        return sorted(({"feature": n, "weight": round(w, 3), "share": round(100 * abs(w) / total, 1)} for n, w in zip(self.names, self.w)),
                      key=lambda r: -r["share"])


def auc(scores: Sequence[float], labels: Sequence[int]) -> float | None:
    """Area under the ROC curve (the chance a random positive outranks a random negative)."""
    pos = [s for s, t in zip(scores, labels) if t]
    neg = [s for s, t in zip(scores, labels) if not t]
    if not pos or not neg:
        return None
    wins = sum(1.0 if p > q else 0.5 if p == q else 0.0 for p in pos for q in neg)
    return round(wins / (len(pos) * len(neg)), 3)


def train_and_score(names: list[str], X: list[list[float]], y: list[int], min_class: int = 8) -> dict[str, Any] | None:
    """A model, or None when either class is too small to learn from. Evaluated on every 4th record held out."""
    if min(sum(y), len(y) - sum(y)) < min_class:
        return None
    test = [i for i in range(len(X)) if i % 4 == 0]
    train = [i for i in range(len(X)) if i % 4 != 0]
    held = None
    if min(sum(y[i] for i in test), len(test) - sum(y[i] for i in test)) >= 2:
        probe = Logistic(names).fit([X[i] for i in train], [y[i] for i in train])
        held = auc([probe.proba(X[i]) for i in test], [y[i] for i in test])
    model = Logistic(names).fit(X, y)
    scores = [model.proba(x) for x in X]
    return {"model": model, "auc_holdout": held, "auc_train": auc(scores, y), "trained_on": len(X), "positives": sum(y),
            "accuracy": round(sum((s >= 0.5) == bool(t) for s, t in zip(scores, y)) / len(y), 3)}


# ---------------------------------------------------------------- clustering


def standardise(X: list[list[float]]) -> list[list[float]]:
    d = len(X[0])
    mean = [sum(r[j] for r in X) / len(X) for j in range(d)]
    std = [math.sqrt(sum((r[j] - mean[j]) ** 2 for r in X) / len(X)) or 1.0 for j in range(d)]
    return [[(r[j] - mean[j]) / std[j] for j in range(d)] for r in X]


def _dist(a: Sequence[float], b: Sequence[float]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b))


def _kmeans(Z: list[list[float]], k: int, seed: int = 42) -> tuple[list[int], list[list[float]]]:
    rng = random.Random(seed)
    centres = [Z[rng.randrange(len(Z))]]
    while len(centres) < k:  # k-means++
        d2 = [min(_dist(z, c) for c in centres) for z in Z]
        total = sum(d2)
        if total <= 0:
            break
        pick, acc = rng.random() * total, 0.0
        for z, w in zip(Z, d2):
            acc += w
            if acc >= pick:
                centres.append(z)
                break
    labels = [0] * len(Z)
    for _ in range(60):
        new = [min(range(len(centres)), key=lambda c: _dist(z, centres[c])) for z in Z]
        moved = new != labels
        labels = new
        for c in range(len(centres)):
            members = [z for z, lab in zip(Z, labels) if lab == c]
            if members:
                centres[c] = [sum(col) / len(members) for col in zip(*members)]
        if not moved:
            break
    return labels, centres


def _silhouette(Z: list[list[float]], labels: list[int]) -> float:
    idx = list(range(len(Z)))[:300]
    scores = []
    for i in idx:
        own = [math.sqrt(_dist(Z[i], Z[j])) for j in idx if labels[j] == labels[i] and j != i]
        if not own:
            continue
        a = sum(own) / len(own)
        b = min((sum(math.sqrt(_dist(Z[i], Z[j])) for j in idx if labels[j] == c) / max(1, sum(1 for j in idx if labels[j] == c))
                 for c in set(labels) if c != labels[i]), default=0.0)
        scores.append((b - a) / max(a, b) if max(a, b) > 0 else 0.0)
    return sum(scores) / len(scores) if scores else 0.0


def kmeans(X: list[list[float]], k_max: int = 5) -> dict[str, Any]:
    Z = standardise(X)
    best: dict[str, Any] | None = None
    for k in range(2, max(2, min(k_max, len(Z) - 1)) + 1):
        labels, centres = _kmeans(Z, k)
        if len(set(labels)) < 2:
            continue
        s = _silhouette(Z, labels)
        if best is None or s > best["silhouette"] + 0.02:
            best = {"k": k, "labels": labels, "centres": centres, "silhouette": round(s, 3)}
    return best or {"k": 1, "labels": [0] * len(Z), "centres": [[0.0] * len(X[0])], "silhouette": 0.0}
