"""Cooperative pause/resume/cancel control for one in-flight pipeline run.

The background phase task runs inside this app's process (see
backend/services/pipeline_runner.py - plain asyncio tasks, no queue infra),
so "pause the agent" is implemented as a shared flag + threading.Event that
the task checks at safe boundaries, combined with a hard freeze of the
sandbox container itself for the long-running Codex/opencode exec (docker
pause = SIGSTOP every process in the container). Together that means:

- If the agent is between steps when pause lands, the next checkpoint()
  simply blocks until resume.
- If the agent is *inside* a Codex run when pause lands, the container is
  frozen exactly where it is and resumed in place on unpause - nothing is
  restarted or replayed.

Cancel reuses the same machinery for the same reason pause needed it: a
checkpoint() raise alone only interrupts the task between steps, not a
blocking Codex/opencode exec already in progress inside the sandbox - that
needs the container killed directly (see SandboxManager.kill()).

The paused/cancelled run's DB status is flipped here (single writer: this
control, called from the /pause, /resume, and /cancel endpoints). The task
itself never writes status around pause/resume/cancel, only blocks or raises.

request_pause()/request_resume()/request_cancel() are invoked from FastAPI
sync endpoints, which run in a threadpool thread, never the event loop - so
a threading.Event is used instead of asyncio.Event (which is not safe to
touch off-loop), and checkpoint() blocks on it via asyncio.to_thread.
"""

from __future__ import annotations

import asyncio
import threading

from . import run_states as states
from .runs import update_pipeline_run


class RunPauseControl:
    def __init__(self, run_id: str, active_status: str) -> None:
        self.run_id = run_id
        # Status the run should return to on resume (planning or running),
        # derived from which phase owns the control when it's created.
        self.active_status = active_status
        self._resume = threading.Event()
        self._resume.set()
        self._pause_requested = False
        self._cancel_requested = False
        self._sandbox = None

    @property
    def paused(self) -> bool:
        return self._pause_requested

    def attach_sandbox(self, sandbox) -> None:
        """Register the live sandbox so pause() can freeze the container."""
        self._sandbox = sandbox

    def detach_sandbox(self) -> None:
        self._sandbox = None

    def request_pause(self) -> bool:
        """Freeze the run now. Returns True if it actually paused, False if
        it was already paused (idempotent). Flips the DB status and suspends
        the sandbox container if one is attached."""
        if self._pause_requested:
            return False
        self._pause_requested = True
        self._resume.clear()
        update_pipeline_run(self.run_id, status=states.PAUSED)
        if self._sandbox is not None:
            self._sandbox.pause()
        return True

    def request_resume(self) -> bool:
        """Unfreeze the run. Returns True if it actually resumed, False if it
        wasn't paused (idempotent). Restores the DB status to the phase's
        active status and thaws the sandbox container."""
        if not self._pause_requested:
            return False
        self._pause_requested = False
        if self._sandbox is not None:
            self._sandbox.unpause()
        self._resume.set()
        update_pipeline_run(self.run_id, status=self.active_status)
        return True

    def request_cancel(self) -> bool:
        """Stop the run now. Returns True if it actually canceled, False if
        already canceled (idempotent). Flips the DB status and kills the
        sandbox container if one is attached - unpausing first if the run is
        currently paused, since a docker-paused container is frozen at the
        cgroup level and can't receive SIGKILL until it's thawed."""
        if self._cancel_requested:
            return False
        self._cancel_requested = True
        update_pipeline_run(self.run_id, status=states.CANCELLED)
        if self._sandbox is not None:
            if self._pause_requested:
                self._sandbox.unpause()
            self._sandbox.kill()
        self._pause_requested = False
        # Unblock a checkpoint() currently waiting on a pause so it wakes up
        # and re-checks _cancel_requested instead of waiting for a resume
        # that will never come.
        self._resume.set()
        return True

    async def checkpoint(self) -> None:
        """Called at safe boundaries (between agent steps, around the Codex
        exec). Blocks until resumed if a pause is currently in effect, and
        raises if the run has been canceled - either immediately, or upon
        waking from a pause-wait that a cancel just unblocked."""
        if self._cancel_requested:
            raise asyncio.CancelledError(f"Run {self.run_id} was canceled")
        if not self._pause_requested:
            return
        await asyncio.to_thread(self._resume.wait)
        if self._cancel_requested:
            raise asyncio.CancelledError(f"Run {self.run_id} was canceled")
