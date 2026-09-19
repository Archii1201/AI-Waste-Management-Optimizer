"""Aggregates every v1 endpoint module into a single router.

Each feature step of the project adds its router here, which keeps `main.py`
untouched as the system grows.
"""

from fastapi import APIRouter

from app.api.v1.endpoints import health

api_router = APIRouter()
api_router.include_router(health.router)
