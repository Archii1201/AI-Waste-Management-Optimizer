"""Application configuration loaded from environment variables.

Every tunable in the system lives here so that operational behaviour (alert
thresholds, routing limits, model names) can be changed without touching code.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_DEV_CORS_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]

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
    app_name: str = "EcoFlow AI"
    environment: str = "development"
    debug: bool = True
    log_level: str = "INFO"
    api_v1_prefix: str = "/api/v1"
    # Local default. Production/Docker must bind 0.0.0.0 and the platform PORT.
    host: str = "127.0.0.1"
    port: int = 8000

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
    # Empty default: development falls back to local Vite origins; production
    # falls back to same-origin (no localhost). Never default to "*".
    # NoDecode stops pydantic-settings from trying to JSON-parse the raw .env
    # value, so the validator below can accept a plain comma-separated list.
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

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

    # ---------- Telemetry interpretation ----------
    # A large drop in fill level with a low residual is how the system infers
    # that a bin was emptied, since low-cost sensors never report collections.
    collection_drop_threshold_pct: float = 25.0
    collection_residual_max_pct: float = 25.0
    # Readings dated further ahead than this are rejected as clock-skew errors.
    telemetry_future_tolerance_minutes: int = 5
    # Silence longer than this marks a sensor as stale on the dashboard.
    sensor_stale_hours: int = 24
    # History window used for the rolling fill-rate estimate.
    fill_rate_window_days: int = 7

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
    # Labelled images, one folder per category. See the README for the layout.
    classifier_dataset_dir: str = "ml/datasets/waste"
    classifier_image_size: int = 224
    # Where uploaded photos are kept so a reviewer can see what was classified
    # and so corrected images can be folded into the next training run.
    upload_dir: str = "ml/uploads"
    max_upload_bytes: int = 8 * 1024 * 1024

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: object) -> object:
        """Accept a comma-separated string from .env as well as a real list."""
        if isinstance(value, str):
            origins = []
            for origin in value.split(","):
                cleaned = origin.strip().rstrip("/")
                if cleaned and cleaned != "*":
                    origins.append(cleaned)
            return origins
        if isinstance(value, list):
            return [
                str(origin).strip().rstrip("/")
                for origin in value
                if str(origin).strip() and str(origin).strip() != "*"
            ]
        return value

    @model_validator(mode="after")
    def _apply_production_guards(self) -> "Settings":
        if self.environment.lower() == "production":
            self.debug = False
            self.db_echo = False
        return self

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
    def dataset_path(self) -> Path:
        return PROJECT_ROOT / self.classifier_dataset_dir

    @property
    def upload_path(self) -> Path:
        path = PROJECT_ROOT / self.upload_dir
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def resolved_cors_origins(self) -> list[str]:
        """Origins allowed by CORSMiddleware. Never includes '*'."""
        configured = [origin for origin in self.cors_origins if origin and origin != "*"]
        if self.is_production:
            return [
                origin
                for origin in configured
                if "localhost" not in origin and "127.0.0.1" not in origin
            ]
        return configured or list(_DEV_CORS_ORIGINS)

    @property
    def uses_placeholder_secret(self) -> bool:
        return not self.secret_key or self.secret_key.startswith("change-me")


@lru_cache
def get_settings() -> Settings:
    """Cached accessor so the .env file is parsed exactly once per process."""
    return Settings()


settings = get_settings()
