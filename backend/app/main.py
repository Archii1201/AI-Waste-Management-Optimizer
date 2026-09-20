"""FastAPI application factory and ASGI entry point.

Local:     uvicorn app.main:app --reload --app-dir backend --host 127.0.0.1 --port 8000
Production: uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1 --app-dir backend

Live Simulation keeps state in process memory: always use --workers 1.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.api.v1.router import api_router
from app.core.config import PROJECT_ROOT, settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, get_logger

logger = get_logger(__name__)


def _frontend_dist() -> Path:
    return PROJECT_ROOT / "frontend" / "dist"


def _spa_enabled() -> bool:
    dist = _frontend_dist()
    return dist.is_dir() and (dist / "index.html").is_file()


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    logger.info(
        "Starting %s (%s) for %s on %s:%s",
        settings.app_name,
        settings.environment,
        settings.city_name,
        settings.host,
        settings.port,
    )
    if settings.is_production and settings.uses_placeholder_secret:
        logger.warning(
            "SECRET_KEY is unset or still the placeholder. Set a unique value before public access."
        )
    if settings.is_production:
        logger.info(
            "Live Simulation is in-process: this process must be the only uvicorn worker. "
            "A restart clears simulation state."
        )
    yield
    from app.services import simulation_control

    simulation_control.stop()
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    docs_enabled = not settings.is_production
    app = FastAPI(
        title=settings.app_name,
        description=(
            "Intelligent waste operations: bin monitoring, fill forecasts, "
            "image classification, collection priority, and vehicle routing."
        ),
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.resolved_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    if _spa_enabled():
        dist = _frontend_dist()

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str):
            if full_path.startswith("api/") or full_path in {"docs", "redoc", "openapi.json"}:
                raise HTTPException(status_code=404)
            candidate = dist / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")
    else:

        @app.get("/", include_in_schema=False)
        def root() -> dict:
            return {
                "app": settings.app_name,
                "version": app.version,
                "environment": settings.environment,
                "docs": "/docs" if docs_enabled else None,
                "api": settings.api_v1_prefix,
                "health": f"{settings.api_v1_prefix}/health",
            }

    return app


app = create_app()
