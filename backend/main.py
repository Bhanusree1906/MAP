"""FastAPI app: mounts the API routers under /api and (once built) the
frontend's static files at /.

Run with: uvicorn backend.main:app --reload
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Scope
from fastapi.staticfiles import StaticFiles

# Must run before any router/agent code reads GOOGLE_API_KEY, OPENAI_API_KEY,
# etc. at import or request time - unlike the CLI scripts, nothing else in
# the ASGI server startup path calls this.
load_dotenv()

from database.session import init_db  # noqa: E402

from agents.pipeline_agent.runs import reconcile_orphaned_runs  # noqa: E402
from backend.services import connection_health  # noqa: E402

from .routers import (  # noqa: E402
    azure_connections,
    codex_connection,
    connections,
    github_connection,
    manual_work_items,
    projects,
    runs,
    salesforce_connections,
    skills,
    ticket_groups,
    tickets,
)

logger = logging.getLogger(__name__)


class SPAStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope):
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            return await super().get_response("index.html", scope)
        if response.status_code != 404:
            return response
        return await super().get_response("index.html", scope)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    orphaned = reconcile_orphaned_runs()
    if orphaned:
        logger.warning("Marked %d orphaned pipeline run(s) as failed on startup: %s", len(orphaned), orphaned)
    await connection_health.check_all()
    yield


app = FastAPI(title="Agentic Pipeline", lifespan=lifespan)

app.include_router(projects.router)
app.include_router(skills.router)
app.include_router(connections.router)
app.include_router(azure_connections.router)
app.include_router(github_connection.router)
app.include_router(tickets.router)
app.include_router(ticket_groups.router)
app.include_router(manual_work_items.router)
app.include_router(runs.router)
app.include_router(codex_connection.router)
app.include_router(salesforce_connections.router)

_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _FRONTEND_DIST.is_dir():
    app.mount("/", SPAStaticFiles(directory=str(_FRONTEND_DIST), html=True), name="frontend")
