"""Aggregates every v1 endpoint module into a single router.

Each feature step of the project adds its router here, which keeps `main.py`
untouched as the system grows.
"""

from fastapi import APIRouter

from app.api.v1.endpoints import (
    bins,
    classify,
    health,
    predictions,
    priorities,
    reference,
    routes,
    telemetry,
    vehicles,
    zones,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(reference.router)
api_router.include_router(zones.router)
api_router.include_router(bins.router)
api_router.include_router(telemetry.router)
api_router.include_router(vehicles.router)
api_router.include_router(predictions.router)
api_router.include_router(classify.router)
api_router.include_router(priorities.router)
api_router.include_router(routes.router)
