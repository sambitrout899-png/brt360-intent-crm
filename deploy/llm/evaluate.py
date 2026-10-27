"""Score a model on held-out BRT 360 requests before you switch to it.

    python3 evaluate.py --data brt360-telecom-holdout.jsonl                          # the local model
    python3 evaluate.py --data holdout.jsonl --model brt360-telecom-previous         # the one before
    python3 evaluate.py --data holdout.jsonl --base-url https://api.openai.com/v1 --model gpt-4o-mini --api-key sk-...

Standard library only - runs on the CRM server. For every example it sends the
same system + user messages the app sends and compares the answer with the
expected one: intent accuracy, field accuracy, valid-JSON rate and speed.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request


def ask(base_url: str, model: str, api_key: str, messages: list[dict], timeout: float) -> str:
    body = json.dumps({"model": model, "temperature": 0, "response_format": {"type": "json_object"}, "messages": messages}).encode()
    req = urllib.request.Request(f"{base_url.rstrip('/')}/chat/completions", data=body,
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())["choices"][0]["message"]["content"]


def norm(value):
    if isinstance(value, str):
        v = value.strip().lower()
        try:
            return float(v)
        except ValueError:
            return v
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, list):
        return [norm(v) for v in value]
    if isinstance(value, dict):
        return {k: norm(v) for k, v in value.items()}
    return value


def kind_of(answer: dict) -> str:
    """Same kinds as finetune.py: normal / hard / unknown / plan / c360."""
    if "steps" in answer:
        return "plan"
    if "ops" in answer:
        return "c360"
    if answer.get("intent_type") == "UNKNOWN":
        return "unknown"
    if answer.get("alternatives") or "Not " in str(answer.get("rationale", "")):
        return "hard"
    return "normal"


def plan_codes(answer: dict) -> list:
    return [s.get("intent_type") for s in answer.get("steps") or [] if isinstance(s, dict)]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", required=True)
    p.add_argument("--base-url", default="http://127.0.0.1:11434/v1")
    p.add_argument("--model", default="brt360-telecom")
    p.add_argument("--api-key", default="local")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--timeout", type=float, default=180)
    args = p.parse_args()

    rows = [json.loads(line) for line in open(args.data, encoding="utf-8") if line.strip()][: args.limit]
    intent_total = intent_ok = field_total = field_ok = valid = 0
    times: list[float] = []
    kinds: dict[str, list[int]] = {}  # kind -> [right, total]
    for i, row in enumerate(rows, 1):
        msgs = row["messages"]
        expected = json.loads(msgs[-1]["content"])
        kind = kind_of(expected)
        t0 = time.perf_counter()
        try:
            raw = ask(args.base_url, args.model, args.api_key, msgs[:-1], args.timeout)
            got = json.loads(raw.strip().removeprefix("```json").removesuffix("```"))
            valid += 1
        except Exception as exc:  # noqa: BLE001
            got = {}
            print(f"[{i}] no usable answer: {type(exc).__name__}")
        times.append(time.perf_counter() - t0)
        if "intent_type" in expected:
            intent_total += 1
            intent_ok += got.get("intent_type") == expected["intent_type"]
            want, have = expected.get("entities") or {}, got.get("entities") or {}
            for k, v in want.items():
                field_total += 1
                field_ok += isinstance(have, dict) and norm(have.get(k)) == norm(v)
        if kind != "c360":
            tally = kinds.setdefault(kind, [0, 0])
            tally[0] += plan_codes(got) == plan_codes(expected) if kind == "plan" else got.get("intent_type") == expected.get("intent_type")
            tally[1] += 1
        shown = plan_codes(got) if kind == "plan" else got.get("intent_type", "ops" if "ops" in got else "?")
        print(f"[{i}/{len(rows)}] {times[-1]:.1f}s  [{kind}] {plan_codes(expected) if kind == 'plan' else expected.get('intent_type', 'c360')} -> {shown}",
              flush=True)  # progress visible with tail when redirected to a file

    n = len(rows)
    print("\n================ scorecard ================")
    print(f"model           : {args.model} @ {args.base_url}")
    print(f"valid JSON      : {valid}/{n} ({valid / max(n, 1):.0%})")
    if intent_total:
        print(f"intent accuracy : {intent_ok}/{intent_total} ({intent_ok / intent_total:.0%})")
    if field_total:
        print(f"field accuracy  : {field_ok}/{field_total} ({field_ok / field_total:.0%})")
    names = {"normal": "plain requests", "hard": "look-alikes/vague", "unknown": "out of scope", "plan": "multi-step plans"}
    for kind, (ok, total) in sorted(kinds.items()):
        print(f"  {names.get(kind, kind):<18}: {ok}/{total} ({ok / max(total, 1):.0%})")
    if times:
        s = sorted(times)
        print(f"latency         : median {s[len(s) // 2]:.1f}s, worst {s[-1]:.1f}s")


if __name__ == "__main__":
    main()
