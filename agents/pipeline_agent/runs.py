"""CRUD helpers for PipelineRun/AgentRun/Artifact/CostLog - the run-history
tables a UI polls against. Mirrors credentials.py's exact idiom
(_ensure_schema, SessionLocal() as db, db.expunge before returning) so
callers always get detached objects safe to use outside the session.

See run_states.py for the status/stage/type string vocabulary used here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import selectinload

from database.models.agent_run import AgentRun
from database.models.agent_run_event import AgentRunEvent
from database.models.artifact import Artifact
from database.models.cost_log import CostLog
from database.models.pipeline_run import PipelineRun
from database.session import SessionLocal, init_db

from . import run_states as states
from .pricing import price_usage

_schema_ready = False


def _ensure_schema() -> None:
    global _schema_ready
    if not _schema_ready:
        init_db()
        _schema_ready = True


# ---------------------------------------------------------------- PipelineRun


def create_pipeline_run(
    project_id: str,
    run_type: str = states.RUN_TYPE_TICKET_TO_PR,
    triggered_by: str = "user",
    metadata: dict | None = None,
) -> PipelineRun:
    _ensure_schema()
    with SessionLocal() as db:
        run = PipelineRun(
            project_id=project_id,
            run_type=run_type,
            status=states.PLANNING,
            current_stage=states.STAGE_PLANNING,
            triggered_by=triggered_by,
            started_at=datetime.utcnow(),
            metadata_=metadata,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        db.expunge(run)
        return run


def update_pipeline_run(
    run_id: str,
    *,
    status: str | None = None,
    current_stage: str | None = None,
    error_message: str | None = None,
    completed: bool = False,
    metadata_patch: dict | None = None,
) -> PipelineRun | None:
    _ensure_schema()
    with SessionLocal() as db:
        run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
        if run is None:
            return None
        if status is not None:
            run.status = status
        if current_stage is not None:
            run.current_stage = current_stage
        if error_message is not None:
            run.error_message = error_message
        if metadata_patch:
            run.metadata_ = {**(run.metadata_ or {}), **metadata_patch}
        if completed:
            run.completed_at = datetime.utcnow()
        db.commit()
        db.refresh(run)
        db.expunge(run)
        return run


def get_pipeline_run(run_id: str) -> PipelineRun | None:
    _ensure_schema()
    with SessionLocal() as db:
        run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
        if run is not None:
            db.expunge(run)
        return run


def list_pipeline_runs(project_id: str | None = None) -> list[PipelineRun]:
    _ensure_schema()
    with SessionLocal() as db:
        query = db.query(PipelineRun)
        if project_id is not None:
            query = query.filter(PipelineRun.project_id == project_id)
        runs = query.order_by(PipelineRun.created_at.desc()).all()
        for run in runs:
            db.expunge(run)
        return runs


def reconcile_orphaned_runs() -> list[str]:
    """Fail any PipelineRun/AgentRun left in an active status by a previous
    process. Execution runs in-process via asyncio.create_task (see
    backend/services/pipeline_runner.py) with no persistence of task state,
    so a server restart while a run is in flight leaves its DB row stuck in
    a non-terminal status forever with nothing left running to finish it.
    Call once at startup, before serving traffic.
    """
    _ensure_schema()
    message = "Orphaned by a server restart while this run was in progress; no process was left to complete it."
    orphaned_ids: list[str] = []
    with SessionLocal() as db:
        runs = db.query(PipelineRun).filter(PipelineRun.status.in_(states.ACTIVE_RUN_STATUSES)).all()
        for run in runs:
            run.status = states.FAILED
            run.error_message = message
            run.completed_at = datetime.utcnow()
            orphaned_ids.append(run.id)
        if orphaned_ids:
            agent_runs = (
                db.query(AgentRun)
                .filter(
                    AgentRun.pipeline_run_id.in_(orphaned_ids),
                    AgentRun.status.in_((states.AGENT_RUN_QUEUED, states.AGENT_RUN_RUNNING)),
                )
                .all()
            )
            for agent_run in agent_runs:
                agent_run.status = states.AGENT_RUN_FAILED
                agent_run.error_message = message
                agent_run.completed_at = datetime.utcnow()
        db.commit()
    return orphaned_ids


# ------------------------------------------------------------------ AgentRun


def create_agent_run(
    pipeline_run_id: str,
    agent_name: str,
    stage: str,
    status: str = states.AGENT_RUN_RUNNING,
) -> AgentRun:
    _ensure_schema()
    with SessionLocal() as db:
        agent_run = AgentRun(
            pipeline_run_id=pipeline_run_id,
            agent_name=agent_name,
            stage=stage,
            status=status,
            started_at=datetime.utcnow(),
        )
        db.add(agent_run)
        db.commit()
        db.refresh(agent_run)
        db.expunge(agent_run)
        return agent_run


def complete_agent_run(
    agent_run_id: str,
    *,
    status: str,
    output_artifact_id: str | None = None,
    error_message: str | None = None,
) -> AgentRun | None:
    _ensure_schema()
    with SessionLocal() as db:
        agent_run = db.query(AgentRun).filter(AgentRun.id == agent_run_id).first()
        if agent_run is None:
            return None
        agent_run.status = status
        agent_run.completed_at = datetime.utcnow()
        if output_artifact_id is not None:
            agent_run.output_artifact_id = output_artifact_id
        if error_message is not None:
            agent_run.error_message = error_message
        db.commit()
        db.refresh(agent_run)
        db.expunge(agent_run)
        return agent_run


# ------------------------------------------------------------- AgentRunEvent


def add_agent_run_event(agent_run_id: str, kind: str, content: str) -> AgentRunEvent:
    """Appends one live-progress event (see database/models/
    agent_run_event.py). seq is computed as max+1 per agent_run_id rather
    than a DB auto-increment column - safe because exactly one background
    task ever writes events for a given agent_run, sequentially.
    """
    _ensure_schema()
    with SessionLocal() as db:
        last_seq = (
            db.query(AgentRunEvent.seq)
            .filter(AgentRunEvent.agent_run_id == agent_run_id)
            .order_by(AgentRunEvent.seq.desc())
            .first()
        )
        next_seq = (last_seq[0] + 1) if last_seq else 1
        event = AgentRunEvent(agent_run_id=agent_run_id, seq=next_seq, kind=kind, content=content)
        db.add(event)
        db.commit()
        db.refresh(event)
        db.expunge(event)
        return event


def list_agent_run_events(agent_run_id: str, after_seq: int = 0) -> list[AgentRunEvent]:
    _ensure_schema()
    with SessionLocal() as db:
        events = (
            db.query(AgentRunEvent)
            .filter(AgentRunEvent.agent_run_id == agent_run_id, AgentRunEvent.seq > after_seq)
            .order_by(AgentRunEvent.seq.asc())
            .all()
        )
        for event in events:
            db.expunge(event)
        return events


# ------------------------------------------------------------------ Artifact


def create_artifact(
    project_id: str,
    pipeline_run_id: str | None,
    artifact_type: str,
    name: str,
    content: str,
    created_by: str,
    metadata: dict | None = None,
) -> Artifact:
    _ensure_schema()
    with SessionLocal() as db:
        latest_version = (
            db.query(Artifact.version)
            .filter(
                Artifact.project_id == project_id,
                Artifact.artifact_type == artifact_type,
                Artifact.name == name,
            )
            .order_by(Artifact.version.desc())
            .first()
        )
        artifact = Artifact(
            project_id=project_id,
            pipeline_run_id=pipeline_run_id,
            artifact_type=artifact_type,
            name=name,
            version=(latest_version[0] + 1) if latest_version else 1,
            storage_type="database",
            content=content,
            created_by=created_by,
            metadata_=metadata,
        )
        db.add(artifact)
        db.commit()
        db.refresh(artifact)
        db.expunge(artifact)
        return artifact


def get_artifact(artifact_id: str) -> Artifact | None:
    _ensure_schema()
    with SessionLocal() as db:
        artifact = db.query(Artifact).filter(Artifact.id == artifact_id).first()
        if artifact is not None:
            db.expunge(artifact)
        return artifact


def list_artifacts(pipeline_run_id: str) -> list[Artifact]:
    _ensure_schema()
    with SessionLocal() as db:
        artifacts = (
            db.query(Artifact)
            .filter(Artifact.pipeline_run_id == pipeline_run_id)
            .order_by(Artifact.created_at.asc())
            .all()
        )
        for artifact in artifacts:
            db.expunge(artifact)
        return artifacts


# ------------------------------------------------------------------ CostLog


@dataclass
class UsageTotals:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


def create_cost_log(
    project_id: str,
    pipeline_run_id: str | None,
    agent_name: str,
    provider: str,
    model: str,
    operation: str,
    usage: UsageTotals,
    duration_ms: int | None = None,
    status: str = "success",
) -> CostLog:
    _ensure_schema()
    input_cost, output_cost, total_cost = price_usage(model, usage.input_tokens, usage.output_tokens)
    with SessionLocal() as db:
        cost_log = CostLog(
            project_id=project_id,
            pipeline_run_id=pipeline_run_id,
            agent_name=agent_name,
            provider=provider,
            model=model,
            operation=operation,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
            input_cost=input_cost,
            output_cost=output_cost,
            total_cost=total_cost,
            duration_ms=duration_ms,
            status=status,
        )
        db.add(cost_log)
        db.commit()
        db.refresh(cost_log)
        db.expunge(cost_log)
        return cost_log


# -------------------------------------------------------------- Run detail


@dataclass
class RunDetail:
    run: PipelineRun
    agent_runs: list[AgentRun] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    cost_logs: list[CostLog] = field(default_factory=list)


def get_run_detail(run_id: str) -> RunDetail | None:
    _ensure_schema()
    with SessionLocal() as db:
        run = (
            db.query(PipelineRun)
            .options(
                selectinload(PipelineRun.agent_runs),
                selectinload(PipelineRun.artifacts),
                selectinload(PipelineRun.cost_logs),
            )
            .filter(PipelineRun.id == run_id)
            .first()
        )
        if run is None:
            return None
        detail = RunDetail(
            run=run,
            agent_runs=list(run.agent_runs),
            artifacts=list(run.artifacts),
            cost_logs=list(run.cost_logs),
        )
        db.expunge_all()
        return detail
