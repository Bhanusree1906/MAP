import os
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker

from .base import Base


BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "agent_factory.db"

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DB_PATH}")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False,
)


@event.listens_for(engine, "connect")
def _configure_sqlite(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    # FKs are off by default - without this ON DELETE / FKs are silently ignored.
    cursor.execute("PRAGMA foreign_keys=ON")
    # WAL lets readers (FastAPI threadpool) run concurrently with the single
    # background writer (asyncio.create_task pipeline phases) instead of every
    # access serialising on a global lock. busy_timeout makes a contended write
    # wait up to 5s for the lock rather than raising "database is locked"
    # immediately. synchronous=NORMAL is the safe pairing with WAL (a crash can
    # lose the last transaction, never corrupt the file). Single-writer only -
    # see the note in Readme.md about single-worker uvicorn.
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)


def init_db():
    from .models import (  # noqa: F401
        Project,
        PipelineRun,
        AgentRun,
        AgentRunEvent,
        Artifact,
        CostLog,
        JiraConnection,
        AzureConnection,
        GitAccessToken,
        GitHubConnection,
        CodexConnection,
        SalesforceConnection,
        Skill,
        SkillVersion,
        ProjectSkill,
        ProjectMemory,
        TicketGroup,
        ManualTicket,
        ManualWorkItem,
        ImportedTicket,
    )

    Base.metadata.create_all(bind=engine)
    _sqlite_add_missing_columns()


# create_all will not ALTER existing SQLite tables. Additive columns listed
# here are applied once so a pre-existing agent_factory.db keeps working
# after model changes. Do not put destructive rebuilds here.
_SQLITE_ADD_COLUMNS = {
    "skills": [
        ("description", "TEXT"),
        ("origin", "VARCHAR(50) DEFAULT 'custom' NOT NULL"),
        ("origin_ref", "VARCHAR(500)"),
        ("active_version_id", "VARCHAR(36)"),
        ("risk_notes", "TEXT"),
    ],
    "skill_versions": [
        ("storage_backend", "VARCHAR(20) DEFAULT 'local' NOT NULL"),
        ("byte_size", "INTEGER DEFAULT 0 NOT NULL"),
        ("skill_md_name", "VARCHAR(255)"),
        ("skill_md_description", "TEXT"),
    ],
    "projects": [
        ("active_memory_id", "VARCHAR(36)"),
        ("sandbox_kind", "VARCHAR(50) DEFAULT 'default' NOT NULL"),
        ("salesforce_connection_name", "VARCHAR(100)"),
        ("github_connection_name", "VARCHAR(100)"),
        ("azure_connection_name", "VARCHAR(100)"),
        ("azure_git_connection_name", "VARCHAR(100)"),
        ("ticket_group_name", "VARCHAR(100)"),
    ],
    "manual_tickets": [
        ("status", "VARCHAR(20) DEFAULT 'todo' NOT NULL"),
    ],
    "manual_work_items": [],
    "project_skills": [
        ("phase", "VARCHAR(20) DEFAULT 'both' NOT NULL"),
    ],
}


def _sqlite_add_missing_columns() -> None:
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table, columns in _SQLITE_ADD_COLUMNS.items():
            if table not in inspector.get_table_names():
                continue
            existing = {col["name"] for col in inspector.get_columns(table)}
            for name, ddl in columns:
                if name in existing:
                    continue
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()
