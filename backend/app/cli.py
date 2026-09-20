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


# ---------------------------------------------------------------------------
# Waste image classification
# ---------------------------------------------------------------------------
@app.command("dataset-info")
def dataset_info(
    data_dir: str | None = typer.Option(None, help="Dataset root (default: from settings)"),
) -> None:
    """Report how many usable images exist per category, before training."""
    from pathlib import Path

    from app.ml.classification.dataset import describe, discover_samples, verify_images
    from app.ml.classification.labels import CLASS_LABELS

    directory = Path(data_dir) if data_dir else settings.dataset_path
    if not directory.exists():
        console.print(f"[red]Missing:[/] {directory}")
        console.print(
            "Create one subfolder per category and drop images in:\n  "
            + "\n  ".join(str(directory / label) for label in CLASS_LABELS)
        )
        raise typer.Exit(code=1)

    samples = verify_images(discover_samples(directory))
    counts = describe(samples)

    table = Table(title=f"Dataset at {directory}")
    table.add_column("Category")
    table.add_column("Images", justify="right")
    for label, count in counts.items():
        table.add_row(label, f"{count:,}" if count else "[red]0[/]")
    table.add_row("[bold]Total[/]", f"[bold]{len(samples):,}[/]")
    console.print(table)


@app.command("train-classifier")
def train_classifier(
    data_dir: str | None = typer.Option(None, help="Dataset root (default: from settings)"),
    epochs: int = typer.Option(12, help="Maximum epochs; early stopping usually ends sooner"),
    warmup_epochs: int = typer.Option(2, help="Epochs with the backbone frozen"),
    batch_size: int = typer.Option(32),
    head_lr: float = typer.Option(1e-3, help="Learning rate for the new head"),
    backbone_lr: float = typer.Option(1e-4, help="Learning rate for the pretrained backbone"),
    no_pretrained: bool = typer.Option(
        False, "--no-pretrained", help="Train from scratch instead of ImageNet weights"
    ),
    num_workers: int = typer.Option(0, help="DataLoader workers; keep 0 on Windows"),
) -> None:
    """Fine-tune MobileNetV3 to classify waste images into the six categories."""
    from pathlib import Path

    from app.ml.classification.predictor import WasteClassifier
    from app.ml.classification.train import train_classifier as run_training

    result = run_training(
        data_dir=Path(data_dir) if data_dir else None,
        epochs=epochs,
        warmup_epochs=warmup_epochs,
        batch_size=batch_size,
        head_lr=head_lr,
        backbone_lr=backbone_lr,
        pretrained=not no_pretrained,
        num_workers=num_workers,
    )

    per_class_table = Table(title=f"Per-category validation scores - {result.model_version}")
    per_class_table.add_column("Category")
    per_class_table.add_column("Precision", justify="right")
    per_class_table.add_column("Recall", justify="right")
    per_class_table.add_column("F1", justify="right")
    per_class_table.add_column("Images", justify="right")
    for label, scores in result.per_class.items():
        per_class_table.add_row(
            label,
            f"{scores['precision']:.3f}",
            f"{scores['recall']:.3f}",
            f"{scores['f1']:.3f}",
            str(int(scores["support"])),
        )
    console.print(per_class_table)

    console.print(
        f"Accuracy [bold]{result.metrics['val_accuracy']:.3f}[/] | "
        f"macro-F1 [bold]{result.metrics['val_macro_f1']:.3f}[/] | "
        f"recyclable-vs-not [bold]{result.metrics['recyclable_accuracy']:.3f}[/]"
    )
    console.print(
        f"Calibration error {result.metrics['val_calibration_error_uncalibrated']:.3f} "
        f"-> [bold]{result.metrics['val_calibration_error']:.3f}[/] "
        f"after temperature scaling (T={result.temperature:.2f})"
    )

    WasteClassifier.reset_cache()
    console.print(f"[green]Saved:[/] {result.artifact_path}")


@app.command("classify-image")
def classify_image(
    image_path: str = typer.Argument(..., help="Path to the photo to classify"),
    bin_id: int | None = typer.Option(None, help="Check the item against this bin's stream"),
) -> None:
    """Classify a single image from the command line."""
    from pathlib import Path

    from app.services import classification_service

    path = Path(image_path)
    if not path.exists():
        console.print(f"[red]No such file:[/] {path}")
        raise typer.Exit(code=1)

    with SessionLocal() as session:
        record, result, contamination = classification_service.classify_and_store(
            session, data=path.read_bytes(), filename=path.name, bin_id=bin_id
        )

        table = Table(title=f"{path.name} -> {result.predicted_class.value}")
        table.add_column("Category")
        table.add_column("Probability", justify="right")
        for label, probability in sorted(
            result.probabilities.items(), key=lambda pair: pair[1], reverse=True
        ):
            table.add_row(label, f"{probability:.3f}")
        console.print(table)

        console.print(
            f"Confidence [bold]{result.confidence:.3f}[/] in "
            f"{result.inference_ms:.0f}ms | recyclable: {result.is_recyclable}"
        )
        if result.needs_review:
            console.print("[yellow]Low confidence - flagged for human review[/]")
        if contamination.is_contaminant:
            console.print(f"[red]Contamination:[/] {contamination.message}")
        console.print(f"Stored as classification #{record.id}")


@app.command("export-reviewed")
def export_reviewed(
    destination: str = typer.Option(
        None, help="Where to write the corrected images (default: dataset dir)"
    ),
) -> None:
    """Fold human-corrected images back into the training set."""
    from pathlib import Path

    from app.services import classification_service

    target = Path(destination) if destination else settings.dataset_path
    with SessionLocal() as session:
        exported = classification_service.export_reviewed_images(session, target)

    if not exported:
        console.print("[yellow]No reviewed classifications to export yet[/]")
        return
    for label, count in exported.items():
        console.print(f"  {label}: {count}")
    console.print(f"[green]Exported[/] {sum(exported.values())} images to {target}")


# ---------------------------------------------------------------------------
# Prioritisation and routing
# ---------------------------------------------------------------------------
@app.command("priorities")
def priorities(
    zone_id: int | None = typer.Option(None, help="Restrict to one zone"),
    min_score: float | None = typer.Option(None, help="Only show bins at or above this score"),
    top: int = typer.Option(20, help="How many bins to print"),
) -> None:
    """Rank bins by collection priority."""
    from app.services import prioritization

    with SessionLocal() as session:
        ranked = prioritization.prioritize(
            session, zone_id=zone_id, min_score=min_score, limit=top
        )
        counts = prioritization.tier_counts(
            prioritization.prioritize(session, zone_id=zone_id)
        )

        table = Table(title=f"Collection priorities - {counts}")
        table.add_column("Bin")
        table.add_column("Score", justify="right")
        table.add_column("Tier")
        table.add_column("Fill %", justify="right")
        table.add_column("Full in", justify="right")
        table.add_column("Why")

        for item in ranked:
            hours = item.hours_to_full
            table.add_row(
                item.bin.code,
                f"{item.score:.3f}",
                item.tier.value,
                f"{item.bin.current_fill_level:.0f}",
                "-" if hours is None else f"{hours:.1f}h",
                "; ".join(item.reasons) or "routine",
            )

    console.print(table)


@app.command("optimize-routes")
def optimize_routes(
    zone_id: int | None = typer.Option(None, help="Restrict the plan to one zone"),
    min_score: float = typer.Option(0.35, help="Bins below this are not worth a trip"),
    max_candidates: int = typer.Option(90, help="Cap on bins sent to the solver"),
    time_limit: int = typer.Option(30, help="Solver time budget in seconds"),
    no_osrm: bool = typer.Option(
        False, "--no-osrm", help="Skip road distances and use straight-line estimates"
    ),
) -> None:
    """Plan optimised collection routes for today."""
    from app.services import route_service

    with SessionLocal() as session:
        report = route_service.plan_routes(
            session,
            zone_id=zone_id,
            min_priority_score=min_score,
            max_candidates=max_candidates,
            time_limit_seconds=time_limit,
            prefer_osrm=not no_osrm,
        )

        table = Table(title=f"Planned routes ({report.matrix_source} distances)")
        table.add_column("Route")
        table.add_column("Vehicle")
        table.add_column("Stops", justify="right")
        table.add_column("Distance", justify="right")
        table.add_column("Duration", justify="right")
        table.add_column("Load", justify="right")
        table.add_column("Saved", justify="right")

        for route in report.routes:
            saved = route.distance_saved_pct
            table.add_row(
                route.code,
                route.vehicle.code if route.vehicle else "-",
                str(route.total_stops),
                f"{route.total_distance_km:.1f} km",
                f"{route.total_duration_minutes:.0f} min",
                f"{route.planned_volume_liters:,.0f} L",
                "-" if saved is None else f"{saved:.1f}%",
            )

        console.print(table)
        console.print(
            f"Considered {report.candidates_considered} bins | "
            f"total [bold]{report.total_distance_km:.1f} km[/] across "
            f"{report.total_stops} stops | solver {report.solver_status} "
            f"in {report.solve_seconds:.1f}s"
        )

        if report.deferred:
            console.print(
                f"[yellow]{len(report.deferred)} bins deferred[/] "
                f"(e.g. {report.deferred[0]['bin_code']}: {report.deferred[0]['reason']})"
            )


@app.command("route-summary")
def route_summary() -> None:
    """Distance, cost and efficiency gain across all stored plans."""
    from app.services import route_service

    with SessionLocal() as session:
        data = route_service.route_summary(session)

    table = Table(title="Route plan summary")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    for key, value in data.items():
        table.add_row(key.replace("_", " "), str(value))
    console.print(table)


# ---------------------------------------------------------------------------
# Alerts and analytics
# ---------------------------------------------------------------------------
@app.command("detect-alerts")
def detect_alerts(
    zone_id: int | None = typer.Option(None, help="Restrict detection to one zone"),
) -> None:
    """Run the alert rules across the network."""
    from app.services import alert_service

    with SessionLocal() as session:
        report = alert_service.detect(session, zone_id=zone_id)
        open_alerts = alert_service.list_alerts(session, open_only=True, limit=15)

        table = Table(title=f"Open alerts ({report})")
        table.add_column("Severity")
        table.add_column("Type")
        table.add_column("Title")
        for alert in open_alerts:
            table.add_row(alert.severity.value, alert.alert_type.value, alert.title)

    console.print(table)


@app.command("analytics")
def analytics(
    days: int = typer.Option(30, help="Days of history to analyse"),
) -> None:
    """Print the operational analytics summary."""
    from app.services import analytics_service

    with SessionLocal() as session:
        data = analytics_service.overview(session, days=days)

    for block in ("collections", "routes", "fill", "waste"):
        table = Table(title=block.title())
        table.add_column("Metric")
        table.add_column("Value", justify="right")
        for key, value in data[block].items():
            if isinstance(value, dict):
                continue
            table.add_row(key.replace("_", " "), str(value))
        console.print(table)


@app.command("recommendations")
def recommendations(
    days: int = typer.Option(30, help="Days of history to analyse"),
) -> None:
    """Print operational recommendations derived from the data."""
    from app.services import analytics_service

    with SessionLocal() as session:
        items = analytics_service.recommendations(session, days=days)

    if not items:
        console.print("[green]No issues found[/] - operations look healthy")
        return

    for item in items:
        console.print(f"\n[bold]{item['priority'].upper()}[/] - {item['title']}")
        console.print(f"  {item['detail']}")
        console.print(f"  [cyan]Action:[/] {item['action']}")


if __name__ == "__main__":
    app()
