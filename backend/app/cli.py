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


if __name__ == "__main__":
    app()
