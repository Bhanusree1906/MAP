"""Unit tests for the project-setup router's in-memory OAuth CSRF state
(issue/consume round trip, single use, TTL expiry).
"""

from __future__ import annotations

import time

from backend.routers.project_setup import (
    _OAUTH_STATE_TTL_SECONDS,
    _pending_setup_oauth_states,
    _issue_state,
    consume_state,
)


def test_unknown_state_is_none() -> None:
    assert consume_state("never-issued") is None


def test_issue_consume_roundtrip_is_single_use() -> None:
    state = _issue_state("project-1")
    assert consume_state(state) == "project-1"
    # Popped on first consume - a replayed callback must not resolve.
    assert consume_state(state) is None


def test_expired_state_is_rejected() -> None:
    state = "expired-state"
    _pending_setup_oauth_states[state] = ("project-2", time.monotonic() - 1)
    assert consume_state(state) is None


def test_issue_purges_expired_entries() -> None:
    stale_key = "stale"
    _pending_setup_oauth_states[stale_key] = ("project-old", time.monotonic() - 1)
    fresh = _issue_state("project-new")
    assert stale_key not in _pending_setup_oauth_states
    assert consume_state(fresh) == "project-new"
    # Sanity: live states get the full TTL.
    assert _OAUTH_STATE_TTL_SECONDS > 0
