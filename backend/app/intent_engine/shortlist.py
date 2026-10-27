"""The few intents a request could mean - all the model is shown.

The catalog has well over a hundred intents. Describing every one of them on
every request makes the prompt longer than a small local model's context
(Ollama then silently drops its beginning) and slow to read on a CPU. The
model only needs the handful the request's words point at, picked here by
vocabulary overlap with each intent's label, description, examples and
phrases, plus the rule ranker's verb+object scores (understanding.py).
Cheap - no model, no I/O beyond the catalog the caller passes in.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

from app.intent_engine import understanding

LIMIT = 14
# Shown when a request's words point at nothing - the everyday intents.
_COMMON = ("INT-10", "INT-15", "INT-04", "INT-05", "INT-07", "INT-29", "INT-06", "INT-09")
_STOP = {"the", "and", "for", "with", "this", "that", "into", "from", "your", "their", "our", "new", "all", "any", "one", "get", "its",
         "has", "have", "been", "was", "are", "can", "you", "please", "want", "need", "would", "like", "them", "they", "his", "her",
         "since", "about", "some", "make", "via", "per", "not", "out", "now", "just", "also", "then", "there", "here", "what", "which"}
_WORD = re.compile(r"[a-z0-9]{3,}")
# everyday words for what the catalog calls something else
_SYNONYMS = {"invoi": "bill", "statm": "bill", "deal": "oppor", "deals": "oppor", "clien": "custo", "consu": "b2c", "issue": "compl",
             "fault": "compl", "outag": "compl", "ticke": "compl", "sms": "messa", "email": "messa", "whats": "messa", "plan": "produ",
             "plans": "produ", "packa": "produ", "bundl": "produ", "techn": "field", "engin": "field", "sim": "inven", "sims": "inven",
             "cpe": "inven", "route": "inven", "devic": "inven", "pause": "suspe", "stop": "cance", "disco": "cance"}


def _stems(text: str) -> set[str]:
    out = {w[:5] for w in _WORD.findall((text or "").lower()) if w not in _STOP}
    return out | {_SYNONYMS[s] for s in out if s in _SYNONYMS}


def builtin_entries() -> list[dict[str, Any]]:
    """The built-in catalog without a tenant's settings (no database at hand)."""
    from app.intent_engine import intent_registry

    return [intent_registry._builtin_entry(meta, None) for meta in intent_registry.BUILTIN.values()]


def _vocabulary(entry: dict[str, Any]) -> tuple[set[str], set[str]]:
    label = _stems(entry.get("label", ""))
    rest = _stems(" ".join([entry.get("description", ""), *(entry.get("examples") or []), *(entry.get("phrases") or [])]))
    return label, rest - label


def pick(text: str, entries: list[dict[str, Any]], context: dict[str, Any] | None = None, limit: int = LIMIT) -> list[str]:
    """Codes of the intents most likely meant, best first (at most `limit`)."""
    lowered = " ".join((text or "").lower().split())
    words = _stems(text)
    vocab = {code: _vocabulary(e) for e in entries if (code := e.get("code"))}
    known = set().union(*(label | rest for label, rest in vocab.values())) if vocab else set()
    # a misspelt word ("complaning", "bradband") still counts as the word it was meant to be
    for w in [w for w in words if w not in known and len(w) == 5]:
        words |= set(difflib.get_close_matches(w, known, n=1, cutoff=0.8))

    context_type = (context or {}).get("entity_type")
    ranked = dict(understanding.rank_intents(text, None, context_type))
    scores: dict[str, float] = {}
    for e in entries:
        code = e.get("code")
        if not code:
            continue
        label, rest = vocab[code]
        score = 3.0 * len(words & label) + 1.0 * len(words & rest) + 1.5 * ranked.get(code, 0.0)
        if any(p and len(p) > 3 and p.lower() in lowered for p in e.get("phrases") or []):
            score += 6.0
        if score and context_type and e.get("target") == context_type:
            score += 1.0
        if score:
            scores[code] = score
    picked = [c for c, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)][:limit]
    available = {e.get("code") for e in entries}
    for code in _COMMON:
        if len(picked) >= min(limit, 6):
            break
        if code in available and code not in picked:
            picked.append(code)
    return picked
