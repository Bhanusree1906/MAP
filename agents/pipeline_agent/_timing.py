"""Wall-clock timing for pipeline sub-steps.

`timed()` emits a `timing` event (see database/models/agent_run_event.py's
`kind`) around a block so the run view shows where a phase actually spends its
time - clone vs github-memory vs skill install vs the engine exec itself. That
breakdown is what tells us what to optimise next, and it is the raw data the
benchmark harness (roadmap Phase 5) aggregates.

`on_event` is the same `(kind, content)` callback threaded through the runner
and the sandbox tools; when it is None (CLI, tests) `timed()` is a no-op.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

OnEvent = Callable[[str, str], None]


@contextmanager
def timed(on_event: OnEvent | None, label: str) -> Iterator[None]:
    start = time.monotonic()
    try:
        yield
    finally:
        if on_event is not None:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            on_event("timing", f"{label}: {elapsed_ms} ms")
