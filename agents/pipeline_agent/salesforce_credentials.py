"""CRUD around the SalesforceConnection table - see
database/models/salesforce_connection.py for why this stores a pasted SFDX
auth URL rather than driving a browser login itself.
"""

from __future__ import annotations

from database.models.salesforce_connection import SalesforceConnection
from database.session import SessionLocal, init_db

_schema_ready = False


def _ensure_schema() -> None:
    global _schema_ready
    if not _schema_ready:
        init_db()
        _schema_ready = True


def get_salesforce_connection(name: str) -> SalesforceConnection | None:
    _ensure_schema()
    with SessionLocal() as db:
        conn = db.query(SalesforceConnection).filter(SalesforceConnection.name == name).first()
        if conn is not None:
            db.expunge(conn)
        return conn


def list_salesforce_connections() -> list[SalesforceConnection]:
    _ensure_schema()
    with SessionLocal() as db:
        conns = db.query(SalesforceConnection).order_by(SalesforceConnection.name).all()
        for conn in conns:
            db.expunge(conn)
        return conns


def upsert_salesforce_connection(
    name: str,
    org_alias: str,
    sfdx_auth_url: str | None = None,
    instance_url: str | None = None,
) -> SalesforceConnection:
    _ensure_schema()
    with SessionLocal() as db:
        conn = db.query(SalesforceConnection).filter(SalesforceConnection.name == name).first()
        if conn is None:
            conn = SalesforceConnection(name=name)
            db.add(conn)
        conn.org_alias = org_alias
        if sfdx_auth_url is not None:
            conn.sfdx_auth_url = sfdx_auth_url
        conn.instance_url = instance_url
        db.commit()
        db.refresh(conn)
        db.expunge(conn)
        return conn


def delete_salesforce_connection(name: str) -> bool:
    _ensure_schema()
    with SessionLocal() as db:
        conn = db.query(SalesforceConnection).filter(SalesforceConnection.name == name).first()
        if conn is None:
            return False
        db.delete(conn)
        db.commit()
        return True
