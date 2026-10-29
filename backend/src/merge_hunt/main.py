"""
file: main.py
importable: app, create_app

Builds the FastAPI app and wires every part together. This is the only place
that picks concrete classes, the static token provider, the SQLite database
from the settings, the real GitHub client, and hands them to the classes that
need them. Everything is built when the app starts and closed when it stops,
in the lifespan below.

The app is started with
    uv run uvicorn merge_hunt.main:app
from the backend folder. Settings are read at startup and not on import, so
importing this module needs no .env file.

description: Builds the app, connects its parts and starts and stops them with the server.
"""

# NOTE: Shutting down cancels a running scan and leaves it RUNNING. The next
# start marks it INTERRUPTED, the same way a crash is handled, so there is only
# one path for a scan that did not finish.

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker

from merge_hunt.api.routes.scans import router as scans_router
from merge_hunt.config import Settings
from merge_hunt.github.client import HTTP_TIMEOUT, GitHubClient
from merge_hunt.github.token import StaticTokenProvider
from merge_hunt.jobs.scans import ScanJob
from merge_hunt.pipeline.discovery import Discoverer
from merge_hunt.pipeline.enrichment import Enricher
from merge_hunt.storage.database import create_engine, create_tables
from merge_hunt.storage.stores import RepoStore, ScanStore

logger = logging.getLogger(__name__)

# settings is only passed in by tests, the server reads them from the
# environment and the .env file. The parts the routes need go on app.state.
def create_app(settings: Settings | None = None) -> FastAPI:
    """Builds the app with its routes, the parts are built when it starts.

    Args:
        settings: The settings to use instead of reading them at startup.

    Returns:
        The FastAPI app, ready for uvicorn.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        config = settings or Settings()
        engine = create_engine(config.database_url)
        try:
            await create_tables(engine)
            sessions = async_sessionmaker(engine)
            repos = RepoStore(sessions)
            scans = ScanStore(sessions)
            interrupted = await scans.interrupt_running(datetime.now(UTC))
            if interrupted:
                logger.warning("%d scan left unfinished by the last run marked INTERRUPTED", interrupted)
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as http:
                client = GitHubClient(StaticTokenProvider(config.github_token), http)
                job = ScanJob(Discoverer(client, repos), Enricher(client, repos), scans)
                app.state.scans = scans
                app.state.scan_job = job
                try:
                    yield
                finally:
                    await job.close()
        finally:
            await engine.dispose()

    app = FastAPI(title="merge-hunt", lifespan=lifespan)
    app.include_router(scans_router)
    return app

app = create_app()
