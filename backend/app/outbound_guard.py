"""Nothing leaves the platform during a run that will be rolled back.

A rehearsal, a Flow Studio simulation or a system test runs an intent or flow
for real and then undoes it - the database can be undone, a call to someone
else's system can't. Those runs happen inside simulation(); the integration
hub (app/hub/outbound.py) reports what it would have called instead of calling.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager

_simulating: contextvars.ContextVar[bool] = contextvars.ContextVar("brt_outbound_simulating", default=False)


def simulating() -> bool:
    return _simulating.get()


@contextmanager
def simulation() -> Iterator[None]:
    marker = _simulating.set(True)
    try:
        yield
    finally:
        _simulating.reset(marker)
