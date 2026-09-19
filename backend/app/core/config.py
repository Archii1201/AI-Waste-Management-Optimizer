"""Application configuration loaded from environment variables.

Every tunable in the system lives here so that operational behaviour (alert
thresholds, routing limits, model names) can be changed without touching code.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Repository root: backend/app/core/config.py -> up four levels.
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------- Application ----------
    app_name: str = "AI Waste Management & Recycling Optimizer"
    environment: str = "development"
    debug: bool = True
    log_level: str = "INFO"
    api_v1_prefix: str = "/api/v1"

    # ---------- Database ----------
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/waste_optimizer"
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_recycle: int = 300
    db_echo: bool = False

    # ---------- Security ----------
    secret_key: str = "change-me-to-a-long-random-string"
    access_token_expire_minutes: int = 720
    algorithm: str = "HS256"

    # ---------- CORS ----------
    # NoDecode stops pydantic-settings from trying to JSON-parse the raw .env
    # value, so the validator below can accept a plain comma-separated list.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    # ---------- MQTT ----------
    mqtt_enabled: bool = True
    mqtt_host: str = "127.0.0.1"
    mqtt_port: int = 1883
    mqtt_username: str = ""
    mqtt_password: str = ""
    mqtt_telemetry_topic: str = "waste/bins/+/telemetry"
    mqtt_client_id: str = "waste-optimizer-bridge"

    # ---------- City / geography ----------
    city_name: str = "Mumbai"
    city_center_lat: float = 19.0760
    city_center_lon: float = 72.8777
    city_timezone: str = "Asia/Kolkata"

    # ---------- Operational thresholds ----------
    bin_full_threshold: float = 85.0
    bin_critical_threshold: float = 95.0
    overflow_alert_horizon_hours: int = 12
    alert_cooldown_minutes: int = 120

    # ---------- Routing ----------
    osrm_base_url: str = "https://router.project-osrm.org"
    osrm_enabled: bool = True
    default_service_time_minutes: int = 6
    max_route_duration_minutes: int = 480
    route_solver_time_limit_seconds: int = 30

    # ---------- ML artifacts ----------
    ml_artifact_dir: str = "ml/artifacts"
    fill_model_name: str = "fill_rate_gbr"
    classifier_model_name: str = "waste_mobilenetv3"
    classifier_confidence_threshold: float = 0.60

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: object) -> object:
        """Accept a comma-separated string from .env as well as a real list."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("database_url")
    @classmethod
    def _normalise_database_url(cls, value: str) -> str:
        """Upgrade bare `postgresql://` URLs to the psycopg 3 dialect.

        Cloud providers (Neon, Supabase, Render) hand out `postgresql://...`
        connection strings, which SQLAlchemy would route to psycopg2. We ship
        psycopg 3, so rewrite the scheme rather than making the user edit it.
        """
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+psycopg://", 1)
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+psycopg://", 1)
        return value

    @property
    def artifact_path(self) -> Path:
        path = PROJECT_ROOT / self.ml_artifact_dir
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    """Cached accessor so the .env file is parsed exactly once per process."""
    return Settings()


settings = get_settings()
