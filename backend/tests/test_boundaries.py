"""Module boundaries (app/boundaries.py) and the capability registry
(app/capabilities.py)."""

import pytest

from app import boundaries, capabilities


def test_every_file_belongs_to_a_module():
    _, unmapped = boundaries.crossings()
    assert unmapped == [], f"add these to a module in app/boundaries.py: {unmapped}"


def test_modules_only_use_modules_that_exist():
    names = {m.name for m in boundaries.MODULES}
    for module in boundaries.MODULES:
        assert set(module.uses) <= names - {module.name, "chassis"}, module.name


def test_no_new_boundary_crossings():
    found, _ = boundaries.crossings()
    new = sorted(found - boundaries.recorded())
    assert not new, (
        "New import across a module boundary - go through app/capabilities.py, or add the module to "
        "`uses` in app/boundaries.py if the dependency is intended:\n  " + "\n  ".join(new)
    )


def test_fixed_crossings_leave_the_exceptions_list():
    found, _ = boundaries.crossings()
    gone = sorted(boundaries.recorded() - found)
    assert not gone, "Crossings fixed - run `python -m app.boundaries --write`:\n  " + "\n  ".join(gone)


def test_chassis_uses_no_business_module():
    found, _ = boundaries.crossings()
    from_chassis = {line for line in found if boundaries.module_of(line.split(" -> ")[0]) == "chassis"}
    # The event outbox table and the flow table licensing counts still sit
    # with their modules' models; nothing else may join this list.
    assert from_chassis <= {"app.data_intelligence.event_bus -> intent", "app.licensing -> flow"}


def test_module_of():
    assert boundaries.module_of("app.crm_core.bim_service") == "bim"
    assert boundaries.module_of("app.orchestration.actions_om") == "telecom"
    assert boundaries.module_of("app.orchestration.flow_engine") == "flow"
    assert boundaries.module_of("app.orchestration.agent") == "intent"
    assert boundaries.module_of("app.config") == "chassis"


def test_every_intent_has_one_provider():
    codes = capabilities.intent_codes()
    assert {"INT-01", "INT-51", "INT-117", "INT-128"} <= codes
    for code in codes:
        assert callable(capabilities.step_builder(code))
        assert capabilities.provider_of(code) in capabilities.PROVIDERS
    assert capabilities.step_builder("INT-9999") is None
    assert capabilities.step_builder(None) is None


def test_registry_refuses_a_code_two_modules_provide(monkeypatch):
    import sys
    import types

    twin = types.ModuleType("tests._twin_provider")
    twin.BUILDERS = {"INT-01": lambda *args: []}
    monkeypatch.setitem(sys.modules, "tests._twin_provider", twin)
    monkeypatch.setattr(capabilities, "PROVIDERS", capabilities.PROVIDERS + ("tests._twin_provider",))
    monkeypatch.setattr(capabilities, "_steps", {})
    monkeypatch.setattr(capabilities, "_provider_of", {})
    with pytest.raises(RuntimeError, match="INT-01 is provided by both app.orchestration.actions and tests._twin_provider"):
        capabilities.intent_codes()


def test_registered_intents_are_providers_modules():
    """Each provider's intents run in the module the map says owns them -
    so moving a module moves its intents with it."""
    for code in capabilities.intent_codes():
        assert boundaries.module_of(capabilities.provider_of(code)) not in (None, "chassis", "assembly"), code
