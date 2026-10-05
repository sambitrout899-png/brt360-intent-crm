"""Internal adapter shaped like an Address Management / serviceability
system (TMF673 Geographic Address Management). A real deployment would
call an address-validation + serviceability-lookup service here before
feasibility checks even run; this is a deterministic stand-in, same
testable-without-a-live-system convention as the other adapters -
anything containing "INVALIDADDR" fails validation, everything else is
treated as a valid, resolvable address.
"""

from __future__ import annotations


def validate_address(address: str) -> tuple[bool, str]:
    if "INVALIDADDR" in (address or "").upper():
        return False, f"'{address}' could not be validated against the address registry."
    return True, f"'{address}' validated."
