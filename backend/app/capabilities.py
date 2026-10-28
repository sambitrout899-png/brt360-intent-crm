"""Capability registry: what the platform can do, looked up by name.

Each module that carries out intents lists them in its own BUILDERS
({intent code: step builder}); the intent API, Flow Studio, the agent and
the function map ask this registry instead of importing those modules.
When a module later moves into a service of its own, its entries become
calls to that service and none of the callers change.

A code listed by two modules is an error at start, not a silent overwrite.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any

StepBuilder = Callable[..., list[Any]]  # (db, tenant_id, execution_id, entities) -> [SagaStep]

# Modules that provide intents. A new actions module is added here.
PROVIDERS = (
    "app.orchestration.actions",
    "app.orchestration.actions_l2q",
    "app.orchestration.actions_o2a",
    "app.orchestration.actions_t2r",
    "app.orchestration.actions_identity",
    "app.orchestration.actions_site",
    "app.orchestration.actions_prospect",
    "app.orchestration.actions_feasibility",
    "app.orchestration.actions_fulfillment",
    "app.orchestration.actions_lifecycle",
    "app.orchestration.actions_c360",
    "app.orchestration.actions_sales",
    "app.orchestration.actions_config",
    "app.orchestration.actions_configurator",
    "app.orchestration.actions_om",
    "app.orchestration.actions_comm",
    "app.orchestration.actions_pages",
    "app.orchestration.actions_b2c",
    "app.orchestration.fulfillment_engine",
    "app.orchestration.actions_inventory",
)

_steps: dict[str, StepBuilder] = {}
_provider_of: dict[str, str] = {}


def _load() -> dict[str, StepBuilder]:
    if not _steps:
        steps: dict[str, StepBuilder] = {}
        for name in PROVIDERS:
            for code, builder in importlib.import_module(name).BUILDERS.items():
                if code in steps:
                    raise RuntimeError(f"{code} is provided by both {_provider_of[code]} and {name}")
                steps[code] = builder
                _provider_of[code] = name
        _steps.update(steps)
    return _steps


def step_builder(code: str | None) -> StepBuilder | None:
    """The function that builds this intent's steps, or None if no module
    carries it out (it may still be a Flow Studio flow)."""
    return _load().get(code or "")


def has_steps(code: str | None) -> bool:
    return step_builder(code) is not None


def intent_codes() -> frozenset[str]:
    return frozenset(_load())


def provider_of(code: str) -> str | None:
    """The module that carries out this intent."""
    _load()
    return _provider_of.get(code)
