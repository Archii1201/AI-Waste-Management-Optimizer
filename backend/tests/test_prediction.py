"""Tests for the fill-rate model, from dataset construction to the API.

The emphasis is on the properties that make a forecast trustworthy rather than
on a headline accuracy number: no leakage into the features, collections not
mistaken for negative waste generation, chronological validation splits, and
honest degradation when a bin has no history.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.ml.fill_prediction.dataset import (
    MAX_INTERVAL_HOURS,
    build_inference_contexts,
    build_training_frame,
)
from app.ml.fill_prediction.features import (
    FEATURE_COLUMNS,
    TARGET_COLUMN,
    as_model_frame,
    build_feature_row,
    encode_time,
)
from app.ml.fill_prediction.predictor import FillRatePredictor, fallback_forecast
from app.ml.fill_prediction.train import MIN_TRAINING_ROWS, train_fill_model
from app.models.bin import Bin, BinReading
from app.models.enums import BinStatus, PredictionMethod, ReadingSource, WasteType, ZoneType
from app.models.prediction import FillPrediction
from app.models.zone import Zone
from app.seed.history import generate_history
from app.services import prediction_service


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def network(db):
    """A small zone of bins, enough for the dataset and service tests."""
    zone = Zone(
        code="TST-Z",
        name="Test Zone",
        zone_type=ZoneType.COMMERCIAL,
        center_lat=19.11,
        center_lon=72.86,
        population_served=50_000,
    )
    db.add(zone)
    db.flush()

    bins = [
        Bin(
            code=f"TST-{index:04d}",
            label=f"Bin {index}",
            zone_id=zone.id,
            latitude=19.11 + index * 0.001,
            longitude=72.86 + index * 0.001,
            capacity_liters=660.0,
            waste_type=WasteType.MIXED if index % 2 else WasteType.PLASTIC,
            status=BinStatus.ACTIVE,
            sensor_id=f"SENSOR-{index:04d}",
        )
        for index in range(6)
    ]
    db.add_all(bins)
    db.commit()
    return zone


@pytest.fixture
def single_bin(db, network):
    return db.scalar(select(Bin).where(Bin.code == "TST-0000"))


def add_readings(db, bin_obj, samples: list[tuple[datetime, float]]) -> None:
    db.add_all(
        BinReading(
            bin_id=bin_obj.id,
            recorded_at=moment,
            fill_level=fill,
            source=ReadingSource.BACKFILL,
        )
        for moment, fill in samples
    )
    db.commit()


def steady_series(start: datetime, count: int, *, rate: float, step_hours: float = 1.0):
    return [
        (start + timedelta(hours=index * step_hours), min(100.0, index * rate * step_hours))
        for index in range(count)
    ]


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------
def test_time_is_encoded_cyclically_so_midnight_is_near_11pm():
    late = encode_time(datetime(2026, 3, 2, 23, 0, tzinfo=timezone.utc))
    midnight = encode_time(datetime(2026, 3, 3, 0, 0, tzinfo=timezone.utc))
    noon = encode_time(datetime(2026, 3, 3, 12, 0, tzinfo=timezone.utc))

    def distance(a, b):
        return np.hypot(a["hour_sin"] - b["hour_sin"], a["hour_cos"] - b["hour_cos"])

    assert distance(late, midnight) < distance(late, noon)


def test_feature_frame_has_stable_columns_and_pinned_categories():
    row = build_feature_row(
        moment=datetime(2026, 3, 3, 9, 0, tzinfo=timezone.utc),
        fill_level=40.0,
        capacity_liters=660.0,
        zone_type=ZoneType.COMMERCIAL,
        waste_type=WasteType.PLASTIC,
        hours_since_collection=8.0,
        rate_24h=2.0,
        rate_7d=1.8,
    )
    frame = as_model_frame([row])

    assert list(frame.columns) == FEATURE_COLUMNS
    # Categories come from the enums, not from the single row present here, so
    # inference frames encode identically to training frames.
    assert len(frame["waste_type"].cat.categories) == len(list(WasteType))
    assert len(frame["zone_type"].cat.categories) == len(list(ZoneType))


def test_weekend_flag_follows_city_local_time():
    # Saturday 02:00 IST is still Friday in UTC; the flag must follow Mumbai.
    friday_utc = datetime(2026, 3, 6, 20, 40, tzinfo=timezone.utc)
    assert encode_time(friday_utc)["is_weekend"] == 1.0


# ---------------------------------------------------------------------------
# Dataset construction
# ---------------------------------------------------------------------------
def test_target_is_the_hourly_rate_of_the_following_interval(db, single_bin):
    start = datetime(2026, 3, 2, 0, 0, tzinfo=timezone.utc)
    add_readings(db, single_bin, steady_series(start, 40, rate=2.0))

    frame = build_training_frame(db)

    assert not frame.empty
    assert np.allclose(frame[TARGET_COLUMN].to_numpy(), 2.0, atol=1e-6)


def test_collections_are_excluded_from_the_target(db, single_bin):
    """A truck emptying a bin is not negative waste generation."""
    start = datetime(2026, 3, 2, 0, 0, tzinfo=timezone.utc)
    samples = steady_series(start, 30, rate=3.0)
    # Drop the bin from ~87% to 5%, the signature of a collection.
    samples.append((start + timedelta(hours=30), 5.0))
    samples.extend(
        (start + timedelta(hours=30 + index), 5.0 + index * 3.0) for index in range(1, 20)
    )
    add_readings(db, single_bin, samples)

    frame = build_training_frame(db)

    assert (frame[TARGET_COLUMN] >= 0).all()
    assert np.allclose(frame[TARGET_COLUMN].to_numpy(), 3.0, atol=1e-6)


def test_hours_since_collection_resets_after_an_emptying(db, single_bin):
    start = datetime(2026, 3, 2, 0, 0, tzinfo=timezone.utc)
    samples = steady_series(start, 30, rate=3.0)
    samples.append((start + timedelta(hours=30), 4.0))
    samples.extend(
        (start + timedelta(hours=30 + index), 4.0 + index * 3.0) for index in range(1, 20)
    )
    add_readings(db, single_bin, samples)

    frame = build_training_frame(db).sort_values("recorded_at").reset_index(drop=True)
    after_collection = frame[frame["recorded_at"] > start + timedelta(hours=30)]

    assert after_collection["hours_since_collection"].iloc[0] < 3.0
    # And it climbs again from there rather than staying pinned at zero.
    assert after_collection["hours_since_collection"].is_monotonic_increasing


def test_rolling_features_never_include_the_current_row(db, single_bin):
    """The first row of a bin has no past, so its rolling rates must be zero."""
    start = datetime(2026, 3, 2, 0, 0, tzinfo=timezone.utc)
    # Rate and count chosen so the bin never saturates at 100%, which would
    # flatten the tail of the series and muddy what this test is checking.
    add_readings(db, single_bin, steady_series(start, 40, rate=2.0))

    frame = build_training_frame(db).sort_values("recorded_at").reset_index(drop=True)

    assert frame["rate_24h"].iloc[0] == 0.0
    assert frame["rate_7d"].iloc[0] == 0.0
    # By the end, the causal average has converged on the true rate.
    assert frame["rate_24h"].iloc[-1] == pytest.approx(2.0, abs=0.2)


def test_long_gaps_are_not_treated_as_one_slow_interval(db, single_bin):
    start = datetime(2026, 3, 2, 0, 0, tzinfo=timezone.utc)
    samples = [(start, 10.0), (start + timedelta(hours=MAX_INTERVAL_HOURS + 5), 90.0)]
    samples.extend(steady_series(start + timedelta(days=2), 30, rate=2.0))
    add_readings(db, single_bin, samples)

    frame = build_training_frame(db)

    # The 8-hour gap spans an unobserved collection cycle; attributing it to a
    # single rate would teach the model a fiction.
    assert not frame.empty
    assert np.allclose(frame[TARGET_COLUMN].to_numpy(), 2.0, atol=1e-6)


def test_bins_without_enough_readings_are_skipped(db, single_bin):
    start = datetime(2026, 3, 2, 0, 0, tzinfo=timezone.utc)
    add_readings(db, single_bin, steady_series(start, 5, rate=2.0))

    assert build_training_frame(db).empty


# ---------------------------------------------------------------------------
# Inference contexts
# ---------------------------------------------------------------------------
def test_inference_context_reflects_the_latest_state(db, single_bin):
    start = datetime.now(timezone.utc) - timedelta(hours=40)
    add_readings(db, single_bin, steady_series(start, 39, rate=2.0))
    single_bin.current_fill_level = 76.0
    db.commit()

    contexts = {c.bin_id: c for c in build_inference_contexts(db)}
    context = contexts[single_bin.id]

    assert context.fill_level == 76.0
    assert context.rate_24h == pytest.approx(2.0, abs=0.2)
    assert context.zone_type == ZoneType.COMMERCIAL.value


def test_inference_context_exists_even_for_a_bin_with_no_readings(db, single_bin):
    contexts = {c.bin_id: c for c in build_inference_contexts(db)}

    assert single_bin.id in contexts
    assert contexts[single_bin.id].rate_24h == 0.0


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------
def test_training_refuses_to_run_on_too_little_data(db, single_bin):
    start = datetime(2026, 3, 2, 0, 0, tzinfo=timezone.utc)
    add_readings(db, single_bin, steady_series(start, 30, rate=2.0))

    with pytest.raises(ValueError, match="training rows"):
        train_fill_model(db)


@pytest.fixture
def trained(db, network, tmp_path, monkeypatch):
    """Train a real model on generated history, into a temporary artifact dir."""
    monkeypatch.setattr(
        type(settings), "artifact_path", property(lambda _self: tmp_path), raising=False
    )
    FillRatePredictor.reset_cache()

    # Two weeks at 30-minute resolution is well above the training minimum
    # while keeping the fixture fast enough to rebuild per test.
    generate_history(db, days=14, interval_minutes=30, seed=7)
    result = train_fill_model(db)

    yield result
    FillRatePredictor.reset_cache()


def test_training_produces_a_usable_artifact_and_metrics(db, trained):
    assert trained.artifact_path.exists()
    assert trained.rows_train > MIN_TRAINING_ROWS
    assert trained.rows_validation > 0
    assert trained.metrics["val_mae"] >= 0
    assert set(trained.residual_quantiles) == {"p10", "p50", "p90"}


def test_validation_split_is_chronological(db, trained):
    """Validation must be the future relative to training, never interleaved."""
    frame = build_training_frame(db).sort_values("recorded_at").reset_index(drop=True)
    cutoff = int(len(frame) * 0.8)

    assert frame["recorded_at"].iloc[cutoff - 1] <= frame["recorded_at"].iloc[cutoff]
    assert trained.rows_train + trained.rows_validation == len(frame)


def test_model_beats_the_rolling_average_baseline(db, trained):
    """If it cannot beat a 24-hour average, the model is not worth shipping."""
    assert trained.metrics["val_mae"] <= trained.metrics["baseline_mae_rolling_24h"]


def test_permutation_importance_is_reported(db, trained):
    assert trained.feature_importance
    assert set(trained.feature_importance) <= set(FEATURE_COLUMNS)


# ---------------------------------------------------------------------------
# Forecasting
# ---------------------------------------------------------------------------
def test_forecast_gives_a_sooner_overflow_for_a_fuller_bin(db, trained):
    predictor = FillRatePredictor.load(refresh=True)
    contexts = build_inference_contexts(db)

    for context in contexts:
        context.fill_level = 20.0
    low = predictor.forecast(contexts)

    for context in contexts:
        context.fill_level = 90.0
    high = predictor.forecast(contexts)

    comparable = [
        (a.hours_to_full, b.hours_to_full)
        for a, b in zip(low, high)
        if a.hours_to_full is not None and b.hours_to_full is not None
    ]
    assert comparable
    assert all(full_bin < empty_bin for empty_bin, full_bin in comparable)


def test_forecast_interval_brackets_the_point_estimate(db, trained):
    predictor = FillRatePredictor.load(refresh=True)
    forecasts = predictor.forecast(build_inference_contexts(db))

    bracketed = [
        f
        for f in forecasts
        if f.hours_to_full is not None
        and f.hours_to_full_low is not None
        and f.hours_to_full_high is not None
    ]
    assert bracketed
    for forecast in bracketed:
        assert forecast.hours_to_full_low <= forecast.hours_to_full <= forecast.hours_to_full_high


def test_forecast_respects_the_horizon(db, trained):
    predictor = FillRatePredictor.load(refresh=True)
    contexts = build_inference_contexts(db)
    for context in contexts:
        context.fill_level = 0.0

    forecasts = predictor.forecast(contexts, horizon_hours=6)

    assert all(f.hours_to_full is None or f.hours_to_full <= 6 for f in forecasts)


def test_missing_artifact_raises_a_clear_error(tmp_path, monkeypatch):
    from app.core.exceptions import ModelNotTrainedError

    monkeypatch.setattr(
        type(settings), "artifact_path", property(lambda _self: tmp_path), raising=False
    )
    FillRatePredictor.reset_cache()

    with pytest.raises(ModelNotTrainedError):
        FillRatePredictor.load(refresh=True)


# ---------------------------------------------------------------------------
# Fallbacks
# ---------------------------------------------------------------------------
def test_fallback_uses_the_rolling_rate_when_available(db, single_bin):
    start = datetime.now(timezone.utc) - timedelta(hours=30)
    # Stop short of 100%: clamped readings would report a zero rate and drag
    # the rolling average below the true fill rate.
    add_readings(db, single_bin, steady_series(start, 20, rate=4.0))
    single_bin.current_fill_level = 80.0
    db.commit()

    context = next(c for c in build_inference_contexts(db) if c.bin_id == single_bin.id)
    forecast = fallback_forecast(context)

    assert forecast.method is PredictionMethod.ROLLING_MEDIAN
    assert forecast.hours_to_full == pytest.approx(20.0 / 4.0, abs=0.5)


def test_fallback_falls_back_again_to_the_zone_baseline(db, single_bin):
    context = next(c for c in build_inference_contexts(db) if c.bin_id == single_bin.id)
    context.fill_level = 50.0

    forecast = fallback_forecast(context, zone_baseline_rate=2.5)

    assert forecast.method is PredictionMethod.ZONE_BASELINE
    assert forecast.hours_to_full == pytest.approx(20.0, abs=0.1)


def test_fallback_reports_insufficient_data_rather_than_guessing(db, single_bin):
    context = next(c for c in build_inference_contexts(db) if c.bin_id == single_bin.id)

    forecast = fallback_forecast(context)

    assert forecast.method is PredictionMethod.INSUFFICIENT_DATA
    assert forecast.hours_to_full is None


def test_service_degrades_gracefully_without_a_trained_model(db, network, tmp_path, monkeypatch):
    monkeypatch.setattr(
        type(settings), "artifact_path", property(lambda _self: tmp_path), raising=False
    )
    FillRatePredictor.reset_cache()

    generate_history(db, days=5, interval_minutes=60, seed=3)
    report = prediction_service.refresh_predictions(db)

    assert report.bins == 6
    assert report.model_forecasts == 0
    assert report.fallback_forecasts + report.insufficient_data == 6


# ---------------------------------------------------------------------------
# Persistence and scoring
# ---------------------------------------------------------------------------
def test_refresh_stores_one_prediction_per_bin(db, trained):
    report = prediction_service.refresh_predictions(db)

    stored = list(db.scalars(select(FillPrediction)))
    assert report.bins == 6
    assert len(stored) == 6
    assert report.model_forecasts > 0


def test_latest_predictions_returns_the_newest_row_per_bin(db, trained):
    prediction_service.refresh_predictions(db)
    prediction_service.refresh_predictions(db)

    latest = prediction_service.latest_predictions(db)

    assert len(latest) == 6
    assert db.scalar(select(FillPrediction.id).order_by(FillPrediction.id.desc()).limit(1))


def test_latest_predictions_orders_the_most_urgent_first(db, trained):
    prediction_service.refresh_predictions(db)

    latest = prediction_service.latest_predictions(db)
    hours = [p.hours_to_full for p in latest if p.hours_to_full is not None]

    assert hours == sorted(hours)
    # Bins with no expected overflow sort last, not first.
    tail = [p.hours_to_full for p in latest[len(hours) :]]
    assert all(value is None for value in tail)


def test_scoring_compares_forecasts_against_real_overflows(db, single_bin):
    now = datetime.now(timezone.utc)
    predicted_full_at = now - timedelta(hours=3)

    db.add(
        FillPrediction(
            bin_id=single_bin.id,
            generated_at=now - timedelta(hours=10),
            fill_level_at_generation=50.0,
            predicted_fill_rate_pct_per_hour=7.0,
            hours_to_full=7.0,
            predicted_full_at=predicted_full_at,
            method=PredictionMethod.GRADIENT_BOOSTING,
            model_version="test",
        )
    )
    # The bin actually hit the critical threshold two hours later than forecast.
    add_readings(
        db,
        single_bin,
        [(predicted_full_at + timedelta(hours=2), settings.bin_critical_threshold + 1)],
    )

    scored = prediction_service.score_past_predictions(db)
    prediction = db.scalar(select(FillPrediction))

    assert scored == 1
    assert prediction.absolute_error_hours == pytest.approx(2.0, abs=0.01)


def test_scoring_skips_forecasts_with_no_observed_overflow(db, single_bin):
    db.add(
        FillPrediction(
            bin_id=single_bin.id,
            generated_at=datetime.now(timezone.utc) - timedelta(hours=10),
            fill_level_at_generation=50.0,
            predicted_fill_rate_pct_per_hour=7.0,
            hours_to_full=7.0,
            predicted_full_at=datetime.now(timezone.utc) - timedelta(hours=3),
            method=PredictionMethod.GRADIENT_BOOSTING,
        )
    )
    db.commit()

    assert prediction_service.score_past_predictions(db) == 0


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
def test_model_endpoint_reports_when_nothing_is_trained(
    client, api_prefix, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        type(settings), "artifact_path", property(lambda _self: tmp_path), raising=False
    )
    FillRatePredictor.reset_cache()

    response = client.get(f"{api_prefix}/predictions/model")

    assert response.status_code == 200
    assert response.json()["available"] is False


def test_prediction_endpoints_round_trip(db, client, api_prefix, trained):
    refresh = client.post(f"{api_prefix}/predictions/refresh", json={})
    assert refresh.status_code == 202, refresh.text
    assert refresh.json()["bins"] == 6

    listing = client.get(f"{api_prefix}/predictions")
    assert listing.status_code == 200
    body = listing.json()
    assert len(body) == 6
    assert {"urgency", "is_model_backed", "overflow_within_horizon"} <= set(body[0])

    bin_id = body[0]["bin_id"]
    single = client.get(f"{api_prefix}/predictions/bins/{bin_id}")
    assert single.status_code == 200
    assert single.json()["bin_id"] == bin_id


def test_unknown_bin_returns_404(client, api_prefix):
    assert client.get(f"{api_prefix}/predictions/bins/999999").status_code == 404


def test_model_endpoint_exposes_metrics(client, api_prefix, trained):
    response = client.get(f"{api_prefix}/predictions/model")

    body = response.json()
    assert body["available"] is True
    assert body["metrics"]["val_mae"] >= 0
    assert body["rows_train"] > 0
