"""FastAPI application: mounts the JSON API under /api and serves the built front end at /."""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import dashboards, datasets, system
from .deps import build_state
from .errors import UserFacingError
from .settings import Settings, get_settings

log = logging.getLogger("dashboard")


def _configure_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def _start_cleanup(app: FastAPI) -> None:
    ctx = app.state.ctx
    hours = ctx.settings.retention_hours

    def loop() -> None:
        while True:
            try:
                ctx.storage.cleanup(hours)
            except Exception as exc:  # noqa: BLE001
                log.warning("storage cleanup failed: %s", exc)
            stop.wait(3600)
            if stop.is_set():
                return

    stop = threading.Event()
    app.state.cleanup_stop = stop
    threading.Thread(target=loop, name="storage-cleanup", daemon=True).start()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    _configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.ctx = build_state(settings)
        if settings.retention_hours > 0:
            _start_cleanup(app)
        yield
        stop = getattr(app.state, "cleanup_stop", None)
        if stop:
            stop.set()

    app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan,
                  docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)

    app.include_router(system.router, prefix="/api", tags=["system"])
    app.include_router(datasets.router, prefix="/api", tags=["datasets"])
    app.include_router(dashboards.router, prefix="/api", tags=["dashboards"])

    @app.exception_handler(UserFacingError)
    async def _user_error(_: Request, exc: UserFacingError):
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc), "details": exc.details})

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        errors = [f"{'.'.join(str(p) for p in e.get('loc', ()))}: {e.get('msg')}" for e in exc.errors()[:5]]
        return JSONResponse(status_code=422, content={"detail": "Invalid request: " + "; ".join(errors)})

    _mount_frontend(app, settings)
    return app


def _mount_frontend(app: FastAPI, settings: Settings) -> None:
    static_dir = settings.resolved_static_dir()
    if static_dir is None:
        log.warning("No built front end found (frontend/dist). Only the API is served.")

        @app.get("/", include_in_schema=False)
        async def _root():
            return JSONResponse({"message": "API is running. Build the front end to serve the UI.", "docs": "/api/docs"})

        return
    assets = static_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")
    index = static_dir / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    async def _spa(path: str):
        if path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": "Not found."})
        candidate = (static_dir / path).resolve() if path else index
        if path and candidate.is_file() and static_dir.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index)


app = create_app()
