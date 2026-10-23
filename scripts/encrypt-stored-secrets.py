#!/usr/bin/env python3
"""One-time migration: encrypt every legacy plaintext secret at rest.

Reads each secret column with raw SQL (bypassing the ORM's transparent
decryption), re-writes any value that is not yet prefixed ``enc:v1:``
through database.crypto.encrypt_value. Idempotent - safe to re-run.

Usage:
    source envir/bin/activate
    python scripts/encrypt-stored-secrets.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

import database.crypto as crypto  # noqa: E402
from database.session import engine, init_db  # noqa: E402

# table -> columns holding secrets
SECRET_COLUMNS: dict[str, list[str]] = {
    "git_access_tokens": ["access_token"],
    "jira_connections": [
        "jira_api_token",
        "jira_oauth_access_token",
        "jira_oauth_refresh_token",
        "oauth_client_info",
        "oauth_tokens",
    ],
    "github_connections": [
        "access_token",
    ],
    "salesforce_connections": ["sfdx_auth_url"],
    "azure_connections": ["pat"],
    "codex_connections": ["auth_json"],
}


def main() -> int:
    init_db()
    total = 0
    with engine.begin() as conn:
        for table, columns in SECRET_COLUMNS.items():
            for column in columns:
                rows = conn.execute(
                    text(f"SELECT id, {column} FROM {table} WHERE {column} IS NOT NULL")  # noqa: S608
                ).fetchall()
                migrated = 0
                for row_id, raw in rows:
                    if raw and not crypto.is_encrypted(raw):
                        conn.execute(
                            text(f"UPDATE {table} SET {column} = :v WHERE id = :id"),  # noqa: S608
                            {"v": crypto.encrypt_value(raw), "id": row_id},
                        )
                        migrated += 1
                print(f"  {table}.{column}: {migrated}/{len(rows)} encrypted")
                total += migrated
    print(f"Done - {total} secret value(s) now encrypted at rest.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
