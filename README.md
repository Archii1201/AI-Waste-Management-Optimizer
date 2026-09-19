# AI-Powered Waste Management & Recycling Optimizer

Monitors waste bins in real time, forecasts when each bin will overflow, classifies
waste from images, prioritises collections, and plans capacity-constrained vehicle
routes — with an operations dashboard over OpenStreetMap.

Built for problem statement **PS-11**. Seeded network is modelled on **Mumbai**.

---

## Architecture

```
IoT bins ──MQTT──┐
                 ├──> Ingest service ──> PostgreSQL
REST /telemetry ─┘                            │
                                              ├──> Fill-level forecaster   (scikit-learn)
                                              ├──> Waste image classifier  (PyTorch CNN)
                                              ├──> Prioritisation engine   (weighted scoring)
                                              ├──> Route optimizer         (OR-Tools CVRP)
                                              ├──> Alert engine            (rules + anomaly detection)
                                              └──> Analytics engine        (patterns, recommendations)
                                                        │
                        Next.js dashboard <──REST/WS────┘  (MapLibre + OSM, Recharts)
```

## Technology

| Layer | Choice | Why |
|---|---|---|
| Backend | FastAPI + SQLAlchemy 2.0 + Alembic | Async-capable, auto-generated OpenAPI docs, typed ORM |
| Database | PostgreSQL (cloud) | Window functions and time-bucketed aggregation for analytics |
| Fill prediction | scikit-learn `HistGradientBoostingRegressor` | Strong on mixed tabular features, trains in seconds on CPU |
| Waste classification | PyTorch + MobileNetV3 transfer learning | Small dataset rules out training from scratch; runs on CPU |
| Routing | Google OR-Tools | Industry-standard CVRP solver with capacity and time windows |
| Frontend | Next.js 14 + TypeScript + Tailwind | Server components, strong typing, fast iteration |
| Maps | MapLibre GL + OpenStreetMap | Free, no API key, vector rendering for hundreds of markers |
| IoT | amqtt broker + paho-mqtt client | Pure Python, so no system-level broker install |

## Repository layout

```
backend/
  app/
    api/v1/endpoints/   HTTP route handlers, one module per resource
    core/               config, database session, logging, error handling
    models/             SQLAlchemy ORM models (the schema)
    schemas/            Pydantic request/response contracts
    services/           business logic, framework-independent
  alembic/              database migrations
ml/                     training pipelines and model artifacts
frontend/               Next.js dashboard
requirements.txt        every Python dependency, in one file
```

## Database schema

| Table | Purpose |
|---|---|
| `zones` | Wards/neighbourhoods; the baseline unit for anomaly detection |
| `bins` | Location, capacity, waste type, and latest telemetry snapshot |
| `bin_readings` | Append-only sensor time-series; trains the forecaster |
| `collection_events` | Every emptying, with recyclable/non-recyclable split |
| `vehicles` | Capacity (volume and weight), depot, shift window, live position |
| `routes` / `route_stops` | Optimizer output with ordered stops and ETAs |
| `fill_predictions` | Cached forecasts, later scored against what actually happened |
| `waste_classifications` | Image classifier results and human corrections |
| `alerts` | Overflow warnings and generation anomalies, with deduplication |
| `users` | Role-based access: admin, dispatcher, driver, viewer |

## Setup

**Prerequisites:** Python 3.11, Node 20+, and a PostgreSQL connection string
(a free [Neon](https://neon.tech) database works).

```powershell
# 1. Install Python dependencies
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2. Configure
Copy-Item .env.example .env
# then set DATABASE_URL and SECRET_KEY in .env
#   python -c "import secrets; print(secrets.token_urlsafe(48))"

# 3. Create the schema and seed the Mumbai network
cd backend
python -m app.cli init-db     # or `alembic upgrade head` against PostgreSQL
python -m app.cli seed        # 9 zones, 130 bins, 7 vehicles. Safe to re-run.

# 3b. Backfill the history the fill-level model trains on (~560k readings)
python -m app.cli generate-history --days 90

# 3c. Train the fill-level model and produce the first forecasts
python -m app.cli train-fill-model
python -m app.cli predict

# 3d. Optional: train the waste image classifier (needs a labelled image set,
#     see "Waste image classification" below)
python -m app.cli train-classifier

# 4. Run the API
uvicorn app.main:app --reload
```

Interactive API docs: <http://localhost:8000/docs>

### Running the live IoT simulation

Three processes, one per terminal, all from `backend/`:

```powershell
python -m app.cli broker      # 1. embedded MQTT broker on :1883
python -m app.cli bridge      # 2. MQTT -> database ingestion
python -m app.cli simulate --auto-collect --speed 600   # 3. the bin sensor fleet
```

Simulated time starts 24 hours in the past and races forward until it catches
the wall clock, then continues in real time. `--auto-collect` models the legacy
fixed-schedule crew so bins are actually emptied before the route optimizer
exists; that also gives the baseline this project is measured against.

Useful flags: `--ticks N` to stop after N intervals, `--zone-id` to simulate one
ward, `--dropout-rate 0.02` to make sensors occasionally fail to transmit, and
`--seed 7` for a byte-for-byte reproducible run.

Check what landed with `python -m app.cli status`.

### CLI reference

| Command | Purpose |
|---|---|
| `init-db` | Create tables directly from the models |
| `seed` | Create the Mumbai zones, bins and vehicles |
| `generate-history` | Backfill months of readings and collection events |
| `status` | Row counts and average fill level |
| `broker` | Run the embedded MQTT broker |
| `bridge` | Ingest MQTT telemetry into the database |
| `simulate` | Run the bin sensor fleet |
| `train-fill-model` | Train the fill-rate model on stored history |
| `predict` | Refresh stored overflow forecasts for every bin |
| `score-predictions` | Grade past forecasts against observed overflows |
| `dataset-info` | Count usable training images per waste category |
| `train-classifier` | Fine-tune MobileNetV3 on the waste image dataset |
| `classify-image` | Classify one photo from the command line |
| `export-reviewed` | Fold human-corrected images back into the training set |

### Waste image classification

The classifier needs labelled photos, which are not in the repository. Point it
at any folder with one subdirectory per category:

```
ml/datasets/waste/
  plastic/   paper/   metal/   glass/   organic/   other/
```

Common public datasets work unmodified — [TrashNet](https://github.com/garythung/trashnet)
and the Kaggle *Garbage Classification* sets are both folder-per-class. Their
folder names are mapped onto our six categories automatically, so `cardboard`
lands in `paper` and `trash` in `other`.

```powershell
python -m app.cli dataset-info        # check what was found before training
python -m app.cli train-classifier    # ~10 minutes on CPU for ~2.5k images
python -m app.cli classify-image path\to\photo.jpg --bin-id 42
```

Training prints per-category precision and recall, not just overall accuracy,
because a model that is excellent at paper and useless at metal would look fine
on accuracy alone. Passing `--bin-id` also checks the item against what that bin
is meant to hold, which is how stream contamination is detected.

Predictions below the confidence threshold are flagged for human review rather
than trusted. Corrections made through `POST /api/v1/classify/{id}/review` can
be exported back into the dataset with `export-reviewed`, so the model improves
on the cases it actually got wrong.

**Optional GPU:** to train the image classifier on an NVIDIA card, after step 1 run
`pip install --force-reinstall torch torchvision --index-url https://download.pytorch.org/whl/cu121`.

## Deliverable coverage

| Requirement | Where it lives |
|---|---|
| Bin monitoring (location, capacity, fill, type) | `models/bin.py`, `/api/v1/bins` |
| Fill-level prediction | `ml/fill_prediction/`, `/api/v1/predictions` |
| Waste classification (6 categories) | `ml/classification/`, `/api/v1/classify` |
| Collection prioritisation | `services/prioritization.py`, `/api/v1/priorities` |
| Route optimization | `services/routing/`, `/api/v1/routes/optimize` |
| Dashboard | `frontend/` |
| Alerts | `services/alerts.py`, `/api/v1/alerts` |
| Analytics & recommendations | `services/analytics.py`, `/api/v1/analytics` |
| Recyclable/non-recyclable estimation | `collection_events`, `/api/v1/analytics/waste-estimation` |
