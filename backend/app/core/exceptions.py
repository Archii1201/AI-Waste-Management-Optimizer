"""Domain exceptions and the handlers that turn them into HTTP responses.

Services raise these instead of importing FastAPI's HTTPException, which keeps
the business logic framework-agnostic and reusable from the MQTT bridge, the
scheduler and the CLI where there is no HTTP request to fail.
"""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse


class AppError(Exception):
    """Base class for expected, reportable failures."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    error_code: str = "app_error"

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    error_code = "not_found"


class ConflictError(AppError):
    """A uniqueness or state rule was violated, e.g. duplicate bin code."""

    status_code = status.HTTP_409_CONFLICT
    error_code = "conflict"


class ValidationError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    error_code = "validation_error"


class ModelNotTrainedError(AppError):
    """Raised when inference is requested before the model artifact exists."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    error_code = "model_not_trained"


class OptimizationError(AppError):
    """The routing solver could not produce a feasible plan."""

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    error_code = "optimization_failed"


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.error_code,
                    "message": exc.message,
                    "details": exc.details,
                }
            },
        )
