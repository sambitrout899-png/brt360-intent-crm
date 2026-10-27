"""Flow and intent errors - one shape for every failure a flow can meet.

Whatever goes wrong inside a flow - an action (intent) it runs refusing, a
record that isn't there, a {{reference}} that can't be resolved, a Raise
error step, or something unexpected - becomes a FlowError with:

  code       what went wrong, from CODES (or the code a Raise error step chose)
  category   business | validation | system - what a Catch errors step can filter on
  message    what to show a person; a system error never shows internals,
             only a reference to find the full detail in the server log
  retryable  only system errors - retrying a refusal gives the same refusal
  node / flow / intent   where it happened

A flow catches errors with an "error" output on the step that failed, or a
Catch errors step for the whole flow (see flow_engine). A Reject step is not
an error: it is a decision, and always turns the request down.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

log = logging.getLogger("brt360.flows")

CATEGORIES = ("business", "validation", "system")

# code -> (category, what it means) - shown in the Studio's Catch errors picker
CODES: dict[str, tuple[str, str]] = {
    "ACTION_FAILED": ("business", "An action (intent) the step ran refused or failed"),
    "MISSING_INPUT": ("validation", "A step is missing a value it needs"),
    "NOT_FOUND": ("validation", "A record, user, position or department the step names doesn't exist"),
    "REFERENCE_ERROR": ("validation", "A {{reference}} could not be resolved on this run"),
    "RECORD_ERROR": ("validation", "A record operation could not be applied"),
    "INVALID": ("validation", "A step's values were not accepted"),
    "NOT_ALLOWED": ("validation", "The step isn't allowed - module, state model or permission"),
    "SYSTEM": ("system", "An unexpected technical error"),
    "LOOP_LIMIT": ("system", "The flow ran too many steps - it probably loops"),
    "REJECTED": ("business", "A Reject step turned the request down"),
}
# never caught inside the flow: a loop would only run further into the problem,
# and a Reject is a decision, not a failure
UNCATCHABLE = {"LOOP_LIMIT", "REJECTED"}
CUSTOM_CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,39}$")


class FlowError(ValueError):
    """A classified failure inside a flow. A ValueError, so every existing
    "except ValueError" keeps treating it as a refusal it can show."""

    def __init__(self, code: str, message: str, category: str | None = None, *, retryable: bool | None = None,
                 details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.category = category or CODES.get(code, ("business", ""))[0]
        self.retryable = self.category == "system" and code not in UNCATCHABLE if retryable is None else retryable
        self.details = details or {}
        self.node_id: str | None = None
        self.node_label: str | None = None
        self.node_type: str | None = None
        self.flow_id: int | None = None
        self.flow_name: str | None = None
        self.ref: str | None = None
        # the partial run (trace up to the failure), attached when the error leaves the flow
        self.run: dict[str, Any] | None = None

    @property
    def catchable(self) -> bool:
        return self.code not in UNCATCHABLE

    def at(self, flow: Any = None, node: dict[str, Any] | None = None, label: str | None = None) -> "FlowError":
        if flow is not None and self.flow_id is None:
            self.flow_id, self.flow_name = getattr(flow, "id", None), getattr(flow, "name", None)
        if node is not None and self.node_id is None:
            self.node_id, self.node_type = node.get("id"), node.get("type")
            self.node_label = label or node.get("label") or node.get("type")
        return self

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "category": self.category, "message": self.message, "retryable": self.retryable,
                "node_id": self.node_id, "node": self.node_label, "node_type": self.node_type,
                "flow_id": self.flow_id, "flow": self.flow_name, "intent": self.details.get("intent"),
                "details": self.details, **({"ref": self.ref} if self.ref else {})}

    def __str__(self) -> str:
        # the same shape failures have always had: "Flow 'X' - Step: what happened"
        where = f"Flow '{self.flow_name}'" if self.flow_name else ""
        if self.node_label and self.node_type not in ("throw",):
            where = f"{where} - {self.node_label}" if where else self.node_label
        return f"{where}: {self.message}" if where else self.message


def raised(code: str, message: str, category: str | None = None) -> FlowError:
    """The error a Raise error step throws."""
    category = category if category in ("business", "validation") else "business"
    return FlowError(code, message, category, retryable=False, details={"raised": True})


def _code_for_value_error(text: str, node_type: str | None) -> str:
    lowered = text.lower()
    if lowered.startswith("reference '") or "is unavailable; check the path" in lowered:
        return "REFERENCE_ERROR"
    if node_type == "action":  # the flow couldn't give the action its inputs, or the action itself refused
        return "MISSING_INPUT" if "doesn't give" in lowered else "ACTION_FAILED"
    if "not found" in lowered or "no such" in lowered or "doesn't exist" in lowered or "is not a user" in lowered:
        return "NOT_FOUND"
    if re.search(r"\b(needs?|requires?|missing)\b", lowered):
        return "MISSING_INPUT"
    if node_type == "record":
        return "RECORD_ERROR"
    if "not allowed" in lowered or "cannot" in lowered or "can't" in lowered:
        return "NOT_ALLOWED"
    return "INVALID"


def classify(exc: BaseException, flow: Any = None, node: dict[str, Any] | None = None, label: str | None = None,
             intent: str | None = None) -> FlowError:
    """Any exception -> a FlowError at this flow/node. FlowErrors keep their code."""
    if isinstance(exc, FlowError):
        return exc.at(flow, node, label)
    node_type = (node or {}).get("type")
    details: dict[str, Any] = {"intent": intent} if intent else {}
    from fastapi import HTTPException

    if type(exc).__name__ == "FlowRejected":  # flow_engine.FlowRejected - a Reject step's decision
        err = FlowError("REJECTED", str(exc), retryable=False, details=details)
        err.run = getattr(exc, "run", None)
        return err  # its message already names the flow
    if isinstance(exc, HTTPException):  # licensing / module checks
        err = FlowError("NOT_ALLOWED", str(exc.detail), details=details)
    elif isinstance(exc, ValueError):
        text = str(exc)
        if flow is not None or node is not None:  # the location is added back from flow/node - don't say it twice
            text = re.sub(r"^Flow '[^']*' - [^:]{1,120}: ", "", text)
        err = FlowError(_code_for_value_error(text, node_type), text, details=details)
    else:
        # Unexpected - a bug or the database. Never show internals; log them with a reference.
        ref = uuid.uuid4().hex[:8].upper()
        log.exception("Flow error %s in flow %s node %s", ref, getattr(flow, "id", None), (node or {}).get("id"), exc_info=exc)
        err = FlowError("SYSTEM", f"An unexpected error stopped this step (reference {ref}) - an administrator can look it up in the server log",
                        details={**details, "exception": type(exc).__name__})
        err.ref = ref
    return err.at(flow, node, label)


def matches(err: FlowError, filters: str | list[str] | None) -> bool:
    """Whether a Catch errors step with these codes/categories handles this error (blank = every error)."""
    if not err.catchable:
        return False
    wanted = [f.strip() for f in (filters.split(",") if isinstance(filters, str) else (filters or [])) if f and f.strip()]
    if not wanted:
        return True
    return any(w.upper() == err.code or w.lower() == err.category for w in wanted)


def catalog() -> list[dict[str, str]]:
    """What the Studio offers to catch: every code, then every category."""
    return ([{"value": code, "category": cat, "label": f"{code} - {meaning}"} for code, (cat, meaning) in CODES.items() if code not in UNCATCHABLE]
            + [{"value": cat, "category": cat, "label": f"any {cat} error"} for cat in CATEGORIES])
