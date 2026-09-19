"""Liveness and readiness endpoints.

`/health` answers "is the process up" and must never touch the database, so it
stays useful when the database is exactly what is broken. `/health/ready`
answers "can this instance serve traffic" and does check the database.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db

router = APIRouter(tags=["health"])

_STARTED_AT = time.monotonic()


@router.get("/health", summary="Liveness probe")
def health() -> dict:
    return {
        "status": "ok",
        "app": settings.app_name,
        "environment": settings.environment,
        "city": settings.city_name,
        "uptime_seconds": round(time.monotonic() - _STARTED_AT, 1),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/health/ready", summary="Readiness probe including database check")
def readiness(response: Response, db: Session = Depends(get_db)) -> dict:
    started = time.perf_counter()
    database: dict[str, object]
    try:
        db.execute(text("SELECT 1"))
        database = {
            "connected": True,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }
    except Exception as exc:  # noqa: BLE001 - surface the reason to the operator
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        database = {"connected": False, "error": str(exc)}

    return {
        "status": "ready" if database["connected"] else "degraded",
        "database": database,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
