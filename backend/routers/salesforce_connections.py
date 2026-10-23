"""Salesforce org connection CRUD, wrapping
agents/pipeline_agent/salesforce_credentials.py.

Unlike Jira connections, there's no CLI-based creation path for these (no
browser OAuth to run locally for it) - see database/models/
salesforce_connection.py for why. PUT here both creates and updates.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from agents.pipeline_agent import salesforce_credentials
from backend.schemas import SalesforceConnectionOut, SalesforceConnectionUpdate
from backend.services import connection_health

router = APIRouter(prefix="/api/salesforce-connections", tags=["salesforce-connections"])


@router.get("", response_model=list[SalesforceConnectionOut])
async def list_connections(refresh_health: bool = False) -> list[SalesforceConnectionOut]:
    connections = salesforce_credentials.list_salesforce_connections()
    if refresh_health:
        await asyncio.gather(
            *(connection_health.refresh("salesforce", c.name) for c in connections)
        )
    return [
        SalesforceConnectionOut.from_model(c, connection_health.get("salesforce", c.name))
        for c in connections
    ]


@router.get("/{name}", response_model=SalesforceConnectionOut)
def get_connection(name: str) -> SalesforceConnectionOut:
    conn = salesforce_credentials.get_salesforce_connection(name)
    if conn is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    return SalesforceConnectionOut.from_model(conn, connection_health.get("salesforce", conn.name))


@router.put("/{name}", response_model=SalesforceConnectionOut)
async def upsert_connection(name: str, payload: SalesforceConnectionUpdate) -> SalesforceConnectionOut:
    conn = salesforce_credentials.get_salesforce_connection(name)
    if conn is None and not (payload.org_alias and payload.sfdx_auth_url):
        raise HTTPException(
            status_code=400,
            detail="Creating a new connection requires both org_alias and sfdx_auth_url",
        )
    updated = salesforce_credentials.upsert_salesforce_connection(
        name=name,
        org_alias=payload.org_alias or (conn.org_alias if conn else ""),
        sfdx_auth_url=payload.sfdx_auth_url,
        instance_url=payload.instance_url if payload.instance_url is not None else (conn.instance_url if conn else None),
    )
    health = await connection_health.refresh("salesforce", updated.name)
    return SalesforceConnectionOut.from_model(updated, health)


@router.delete("/{name}")
def delete_connection(name: str) -> dict:
    removed = salesforce_credentials.delete_salesforce_connection(name)
    if not removed:
        raise HTTPException(status_code=404, detail="Connection not found")
    return {"status": "ok"}
