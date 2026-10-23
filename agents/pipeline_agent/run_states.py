"""String constants for the pipeline run state machine.

Single source of truth for status/stage/type strings, imported by both the
agent code (agents/pipeline_agent/*) and the backend (backend/*) so they
can't drift into typo'd duplicates of each other.

PipelineRun.status flow:
    PLANNING (Codex drafts the plan) -> AWAITING_APPROVAL ->
    (REJECTED | APPROVED -> RUNNING (Codex implements) -> (COMPLETED | FAILED))

Any of PLANNING/RUNNING/PAUSED can also end in CANCELLED (user-initiated
stop, see RunPauseControl.request_cancel) - a distinct terminal state from
FAILED (crashed on its own) and REJECTED (reviewer declined the plan).
"""

from __future__ import annotations

# PipelineRun.status
QUEUED = "queued"  # created but waiting for a concurrency slot (see pipeline_runner._run_with_slot)
PLANNING = "planning"
# Legacy status retained so existing run records remain readable.
REPLANNING = "replanning"
AWAITING_APPROVAL = "awaiting_approval"
APPROVED = "approved"
REJECTED = "rejected"
RUNNING = "running"
PAUSED = "paused"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"
# Legacy terminal status retained so existing run records remain readable.
WARNINGS_UNRESOLVED = "warnings_unresolved"

# QUEUED counts as active: a queued run holds a control and can be cancelled,
# and reconcile_orphaned_runs must fail queued runs left behind by a restart.
ACTIVE_RUN_STATUSES = (QUEUED, PLANNING, REPLANNING, RUNNING, PAUSED)
TERMINAL_RUN_STATUSES = (REJECTED, COMPLETED, FAILED, CANCELLED, WARNINGS_UNRESOLVED)
# Terminal states a user can retry from - not COMPLETED (retrying a success
# isn't "retry" semantics, that's just starting a new run for the ticket).
RETRYABLE_RUN_STATUSES = (REJECTED, FAILED, CANCELLED, WARNINGS_UNRESOLVED)

# AgentRun.status
AGENT_RUN_QUEUED = "queued"
AGENT_RUN_RUNNING = "running"
AGENT_RUN_COMPLETED = "completed"
AGENT_RUN_FAILED = "failed"

# AgentRun.stage
STAGE_PLANNING = "planning"
STAGE_EXECUTION = "execution"
STAGE_MEMORY_REFRESH = "memory_refresh"

# AgentRun.agent_name
AGENT_NAME_PLANNING = "codex_planning"
AGENT_NAME_EXECUTION = "pipeline_execution_agent"
AGENT_NAME_MEMORY_REFRESH = "project_memory_refresh"

# Artifact.artifact_type
ARTIFACT_PLAN = "plan"
ARTIFACT_EXECUTION_SUMMARY = "execution_summary"
ARTIFACT_COMMENT = "comment"
ARTIFACT_PR = "pr"
ARTIFACT_TRANSCRIPT = "transcript"
ARTIFACT_MEMORY = "memory"

# PipelineRun.run_type
RUN_TYPE_TICKET_TO_PR = "ticket_to_pr"
