"""Operator CLI.

    python -m app.cli --help

Every long-running process in the system (broker, bridge, simulator) and every
one-shot task (seeding, schema creation) is launched from here, so there is one
place to look for "how do I run this".
"""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.table import Table

from app.core.config import settings
from app.core.database import SessionLocal, engine
from app.core.logging import configure_logging, get_logger

app = typer.Typer(help="AI-Powered Waste Management & Recycling Optimizer", no_args_is_help=True)
console = Console()
logger = get_logger(__name__)


@app.callback()
def _bootstrap() -> None:
    configure_logging()


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
@app.command("init-db")
def init_db() -> None:
    """Create any missing tables directly from the models.

    Convenient for a local SQLite file. Against PostgreSQL prefer
    `alembic upgrade head`, which keeps a versioned migration history.
    """
    from app.models import Base

    Base.metadata.create_all(engine)
    console.print(f"[green]Schema ready[/] ({len(Base.metadata.tables)} tables)")


@app.command("seed")
def seed(
    seed_value: int = typer.Option(20260919, "--seed", help="Generator seed for reproducibility"),
) -> None:
    """Create the Mumbai zones, bins and vehicles. Safe to re-run."""
    from app.seed.network import seed_network

    with SessionLocal() as session:
        report = seed_network(session, seed=seed_value)
    console.print(f"[green]Seeded:[/] {report}")


@app.command("generate-history")
def generate_history(
    days: int = typer.Option(90, help="Days of history to generate, ending now"),
    interval_minutes: int = typer.Option(30, help="Minutes between generated readings"),
    reset: bool = typer.Option(
        False, "--reset", help="Wipe existing readings and collections first"
    ),
    zone_id: int | None = typer.Option(None, help="Restrict generation to one zone"),
    seed_value: int | None = typer.Option(20260919, "--seed", help="Generator seed"),
) -> None:
    """Backfill the telemetry history the fill-level model trains on."""
    from app.seed.history import generate_history as run_generation

    with SessionLocal() as session:
        report = run_generation(
            session,
            days=days,
            interval_minutes=interval_minutes,
            seed=seed_value,
            reset=reset,
            zone_id=zone_id,
        )
    console.print(f"[green]Generated:[/] {report}")


@app.command("status")
def status() -> None:
    """Show what is currently in the database."""
    from sqlalchemy import func, select

    from app.models.alert import Alert
    from app.models.bin import Bin, BinReading
    from app.models.collection import CollectionEvent
    from app.models.vehicle import Vehicle
    from app.models.zone import Zone

    table = Table(title=f"{settings.city_name} network")
    table.add_column("Entity")
    table.add_column("Count", justify="right")

    with SessionLocal() as session:
        for label, model in (
            ("Zones", Zone),
            ("Bins", Bin),
            ("Readings", BinReading),
            ("Collections", CollectionEvent),
            ("Vehicles", Vehicle),
            ("Alerts", Alert),
        ):
            count = session.scalar(select(func.count()).select_from(model)) or 0
            table.add_row(label, f"{count:,}")

        avg_fill = session.scalar(select(func.avg(Bin.current_fill_level)))

    console.print(table)
    if avg_fill is not None:
        console.print(f"Average fill level: [bold]{avg_fill:.1f}%[/]")


# ---------------------------------------------------------------------------
# IoT
# ---------------------------------------------------------------------------
@app.command("broker")
def broker() -> None:
    """Run the embedded MQTT broker."""
    from app.iot.broker import run_broker

    console.print(
        f"[cyan]MQTT broker[/] starting on {settings.mqtt_host}:{settings.mqtt_port} "
        "(Ctrl+C to stop)"
    )
    try:
        asyncio.run(run_broker())
    except KeyboardInterrupt:
        console.print("[yellow]Broker stopped[/]")


@app.command("bridge")
def bridge() -> None:
    """Run the MQTT-to-database telemetry bridge."""
    from app.iot.bridge import TelemetryBridge

    console.print(
        f"[cyan]Bridge[/] connecting to {settings.mqtt_host}:{settings.mqtt_port}, "
        f"subscribing to {settings.mqtt_telemetry_topic}"
    )
    TelemetryBridge().run_forever()


@app.command("simulate")
def simulate(
    interval_minutes: int = typer.Option(30, help="Simulated minutes between readings"),
    speed: float = typer.Option(
        120.0, help="Time compression; 120 means one real second is two simulated minutes"
    ),
    start_hours_ago: float = typer.Option(
        24.0, help="How far in the past to begin, so accelerated runs stay behind the clock"
    ),
    ticks: int | None = typer.Option(None, help="Stop after N ticks instead of running forever"),
    dropout_rate: float = typer.Option(0.0, help="Fraction of readings a sensor fails to send"),
    auto_collect: bool = typer.Option(
        False, help="Model the legacy fixed-schedule crew emptying full bins"
    ),
    zone_id: int | None = typer.Option(None, help="Restrict the fleet to one zone"),
    seed_value: int | None = typer.Option(None, "--seed", help="Make the run reproducible"),
) -> None:
    """Run the bin sensor fleet simulator, publishing telemetry over MQTT."""
    from app.iot.simulator import FleetSimulator

    simulator = FleetSimulator(
        interval_minutes=interval_minutes,
        speed=speed,
        dropout_rate=dropout_rate,
        seed=seed_value,
        zone_id=zone_id,
        start_hours_ago=start_hours_ago,
        auto_collect=auto_collect,
    )
    simulator.run(ticks=ticks)


# ---------------------------------------------------------------------------
# Fill-level prediction
# ---------------------------------------------------------------------------
@app.command("train-fill-model")
def train_fill_model(
    days: int | None = typer.Option(
        None, help="Only train on the last N days of history (default: all of it)"
    ),
    validation_fraction: float = typer.Option(
        0.2, help="Share of the most recent data held out for validation"
    ),
    show_importance: bool = typer.Option(
        True, help="Print how much each feature contributes"
    ),
) -> None:
    """Train the fill-rate model on the stored telemetry history."""
    from app.ml.fill_prediction.predictor import FillRatePredictor
    from app.ml.fill_prediction.train import train_fill_model as run_training

    with SessionLocal() as session:
        result = run_training(
            session, days=days, validation_fraction=validation_fraction
        )

    metrics_table = Table(title=f"Validation metrics - {result.model_version}")
    metrics_table.add_column("Metric")
    metrics_table.add_column("Value", justify="right")
    for name, value in result.metrics.items():
        metrics_table.add_row(name, f"{value:,.4f}" if isinstance(value, float) else str(value))
    console.print(metrics_table)

    if show_importance and result.feature_importance:
        importance_table = Table(title="Permutation importance (MAE increase when shuffled)")
        importance_table.add_column("Feature")
        importance_table.add_column("Impact", justify="right")
        for name, value in list(result.feature_importance.items())[:10]:
            importance_table.add_row(name, f"{value:.4f}")
        console.print(importance_table)

    baseline = result.metrics.get("baseline_mae_rolling_24h")
    model_mae = result.metrics.get("val_mae")
    if baseline and model_mae:
        lift = 100.0 * (baseline - model_mae) / baseline
        console.print(
            f"Model beats the 24h rolling-average baseline by [bold]{lift:.1f}%[/] on MAE"
        )

    # Drop the in-process cache so a bridge or API in the same process picks the
    # new artifact up immediately.
    FillRatePredictor.reset_cache()
    console.print(f"[green]Saved:[/] {result.artifact_path}")


@app.command("predict")
def predict(
    zone_id: int | None = typer.Option(None, help="Restrict the refresh to one zone"),
    horizon_hours: int = typer.Option(336, help="How far ahead to simulate, in hours"),
    top: int = typer.Option(10, help="How many of the most urgent bins to print"),
) -> None:
    """Refresh stored fill-level forecasts for every active bin."""
    from app.services import prediction_service

    with SessionLocal() as session:
        report = prediction_service.refresh_predictions(
            session, zone_id=zone_id, horizon_hours=horizon_hours
        )
        urgent = prediction_service.latest_predictions(session, zone_id=zone_id, limit=top)

        table = Table(title=f"Most urgent bins ({report})")
        table.add_column("Bin")
        table.add_column("Fill %", justify="right")
        table.add_column("Rate pp/h", justify="right")
        table.add_column("Full in", justify="right")
        table.add_column("Method")

        for prediction in urgent:
            hours = prediction.hours_to_full
            table.add_row(
                prediction.bin.code,
                f"{prediction.fill_level_at_generation:.0f}",
                f"{prediction.predicted_fill_rate_pct_per_hour:.2f}",
                "beyond horizon" if hours is None else f"{hours:.1f}h",
                prediction.method.value,
            )

    console.print(table)


@app.command("score-predictions")
def score_predictions(
    lookback_days: int = typer.Option(7, help="How far back to look for scorable forecasts"),
) -> None:
    """Compare past forecasts against the overflows that actually happened."""
    from app.services import prediction_service

    with SessionLocal() as session:
        scored = prediction_service.score_past_predictions(
            session, lookback_days=lookback_days
        )
    console.print(f"[green]Scored[/] {scored} forecasts against observed overflows")


if __name__ == "__main__":
    app()
