from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from agents.pipeline_agent import manual_work_items
from agents.pipeline_agent.credentials import get_project_by_id
from backend.schemas import (
    ManualWorkItemCreate,
    ManualWorkItemOut,
    ManualWorkItemStatusIn,
    ManualWorkItemUpdate,
)

router = APIRouter(prefix="/api/projects/{project_id}/manual-tickets", tags=["manual-tickets"])
_STATUSES = {"todo", "progress", "agentcompleted", "completed"}


def _out(item) -> ManualWorkItemOut:
    return ManualWorkItemOut.model_validate(item)


def _require_project(project_id: str) -> None:
    if get_project_by_id(project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")


def _validate_title(title: str) -> str:
    title = title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Ticket title is required")
    return title


@router.post("", response_model=ManualWorkItemOut, status_code=201)
def create_manual_work_item(project_id: str, payload: ManualWorkItemCreate) -> ManualWorkItemOut:
    _require_project(project_id)
    item = manual_work_items.create_work_item(
        project_id, _validate_title(payload.title), payload.body_html
    )
    return _out(item)


@router.get("", response_model=list[ManualWorkItemOut])
def list_manual_work_items(
    project_id: str,
    status: Literal["todo", "progress", "agentcompleted", "completed"] | None = Query(None),
) -> list[ManualWorkItemOut]:
    _require_project(project_id)
    return [_out(item) for item in manual_work_items.list_work_items(project_id, status)]


@router.get("/{public_id}", response_model=ManualWorkItemOut)
def get_manual_work_item(project_id: str, public_id: str) -> ManualWorkItemOut:
    _require_project(project_id)
    item = manual_work_items.get_work_item(project_id, public_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Manual ticket not found")
    return _out(item)


@router.patch("/{public_id}", response_model=ManualWorkItemOut)
def update_manual_work_item(
    project_id: str, public_id: str, payload: ManualWorkItemUpdate
) -> ManualWorkItemOut:
    _require_project(project_id)
    item = manual_work_items.update_work_item(
        project_id, public_id, _validate_title(payload.title), payload.body_html
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Manual ticket not found")
    return _out(item)


@router.patch("/{public_id}/status", response_model=ManualWorkItemOut)
def update_manual_work_item_status(
    project_id: str, public_id: str, payload: ManualWorkItemStatusIn
) -> ManualWorkItemOut:
    _require_project(project_id)
    try:
        item = manual_work_items.update_work_item_status(
            project_id, public_id, payload.status
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if item is None:
        raise HTTPException(status_code=404, detail="Manual ticket not found")
    return _out(item)


@router.delete("/{public_id}")
def delete_manual_work_item(project_id: str, public_id: str) -> dict[str, str]:
    _require_project(project_id)
    try:
        deleted = manual_work_items.delete_work_item(project_id, public_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    if not deleted:
        raise HTTPException(status_code=404, detail="Manual ticket not found")
    return {"status": "ok"}
