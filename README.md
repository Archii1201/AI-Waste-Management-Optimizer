# EcoFlow AI

**Intelligent Waste Operations**

Live operations platform that monitors bins, forecasts overflow, classifies waste from photos, ranks collections, and plans vehicle routes — instead of sending trucks on a fixed round.

Built for **PS-11: AI-Powered Waste Management & Recycling Optimizer** (BIT N BUILD’26). Demo city: **Mumbai**.

| | |
|---|---|
| **Live demo** | [https://web-production-053f40.up.railway.app/](https://web-production-053f40.up.railway.app/) |
| **Health** | [https://web-production-053f40.up.railway.app/api/v1/health](https://web-production-053f40.up.railway.app/api/v1/health) |
| **API** | `https://web-production-053f40.up.railway.app/api/v1/*` |

One Railway service serves the React dashboard at `/` and FastAPI at `/api/v1`.

---

## Problem

Cities still collect waste on a calendar: trucks visit empty bins, miss overflowing ones, and recycling stays mixed. Dispatchers have no live fill picture, no overflow forecast, and no capacity-aware route plan.

## What we built

EcoFlow AI closes that loop:

**live bins → forecast + classify → priority → optimized routes → alerts / analytics / What-If**

Demo network: **9 Mumbai wards, 130 bins, 7 vehicles**.

---

## Features

Each item below is implemented and visible on the live demo.

### Live operations dashboard
OpenStreetMap of Mumbai with every bin coloured by fill. KPI cards (fill, alerts, collections, diversion). Polls the API about every 12 seconds so the board stays current without a full reload.

### Bin monitoring & details
Click a bin for location, capacity, waste stream, latest fill/weight/battery, forecast, and recent readings. The map snapshot is denormalised so hundreds of bins load in one query; history lives in `bin_readings`.

### Fill-level prediction
A real scikit-learn **HistGradientBoostingRegressor** predicts fill-rate (percentage-points per hour) and hours-to-full, with a confidence band. Trained on ~90 days of seeded telemetry (~462k readings). If the model file is missing, the API falls back to rolling/zone estimates instead of inventing numbers.

### Waste image classification
Upload a photo. A **PyTorch MobileNetV3-Small** CNN returns one of six classes (plastic, paper, metal, glass, organic, other), softmax scores, and a review flag when confidence is low. Used to spot contamination vs the bin’s intended stream.

### Collection priority
Bins are ranked P0–P3 with an **explainable** score — not a black box. Weights: current fill 35%, predicted overflow 30%, waste type 15%, location 10%, chronic overflow 10%. Dispatchers can see *why* a bin is first.

### Route optimization
One click runs Google **OR-Tools CVRP**. Respects vehicle volume, weight, shift window, and accepted waste types. Road distances from public OSRM; if OSRM is down, haversine × 1.35. Bins the fleet cannot take are listed as `deferred` with a reason — they do not disappear.

### Alerts
Rule engine for imminent overflow, overflowing bins, generation anomalies, and stale/faulty sensors. Duplicate alerts are suppressed. Operators can acknowledge or resolve from the panel.

### Analytics
Zone table, collection volume/weight over the selected window, recyclable vs residual split, and fill-level charts (Recharts). Window: 24h / 7 / 30 / 90 days.

### Period compare
Toggle “Compare previous” to put this window next to the one before it (same length), so a spike is visible instead of a single snapshot.

### Recommendations
Operational suggestions in plain language (over-serviced zones, bins likely to overflow, contamination). API jargon is stripped for operators.

### Live Simulation
Start / tick / stop a fleet of virtual sensors over HTTP (no MQTT required). Each tick writes real telemetry, can refresh forecasts, and can raise alerts. State is **in this process only** — one uvicorn worker; a restart clears the sim.

### What-If analysis
Change fleet size, collection threshold, or extra load and preview a new plan. The database is **rolled back** — What-If never saves a fake route. Overlay the preview on the map vs the current plan.

### Impact summary & report
Shows the gap vs a naive “visit every bin in ID order” baseline (distance, cost, overflow risk). Generate a downloadable operations report from the current dashboard state.

### Health checks
`GET /api/v1/health` — process up (no ML).  
`GET /api/v1/health/ready` — database reachable.

---

## How it fits together

```
Sensors / Live Sim / REST  →  PostgreSQL
                                ├── Fill-rate model (scikit-learn)
                                ├── Image classifier (PyTorch)
                                ├── Priority scoring
                                ├── OR-Tools routes (+ OSRM)
                                └── Alerts & analytics
                                         ↓
                         React dashboard  (same origin)
```

---

## Tech stack

| Layer | What we used |
|---|---|
| Frontend | React, Vite, JavaScript, Tailwind, Leaflet, Recharts |
| Backend | FastAPI, SQLAlchemy 2.0, Alembic, Pydantic, Uvicorn |
| Database | PostgreSQL |
| Fill ML | scikit-learn HistGradientBoostingRegressor |
| Vision ML | PyTorch MobileNetV3-Small (CPU) |
| Routing | OR-Tools CVRP, OSRM |
| Deploy | Docker (Node build + Python runtime), Railway |

---

## Model results (trained, not dummy)

| Model | Outcome |
|---|---|
| Fill GBR | Val MAE **1.04** fill-pp/hour vs rolling-24h baseline **1.84**; val R² **0.69** |
| Classifier | Val accuracy **90.6%**; macro F1 **0.88**; recyclable vs not **96.1%** |

Artifacts: `ml/artifacts/fill_rate_gbr.joblib`, `ml/artifacts/waste_mobilenetv3.pt`.

---

## Not in this demo

Login/JWT in the UI, driver mobile app, live truck GPS, Redis (multi-worker sim), multi-city production tenants. Those belong in future work — they are not claimed as shipped.

---

## Local development

Python 3.11, Node 20+, PostgreSQL.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
# set DATABASE_URL and SECRET_KEY

cd backend
alembic upgrade head
python -m app.cli seed
python -m app.cli generate-history --days 90   # optional, large
python -m app.cli train-fill-model             # optional if artifacts already present
python -m app.cli predict

uvicorn app.main:app --reload --app-dir backend --host 127.0.0.1 --port 8000
```

```powershell
cd frontend
npm install
npm run dev
```

Vite (`http://localhost:5173`) proxies `/api` to port 8000. Leave `VITE_API_BASE_URL` empty.

Docs (local only): http://localhost:8000/docs  
Production disables Swagger.

`init-db` still exists for local create_all. **Production uses `alembic upgrade head`.**

---

## Environment

See `.env.example`. Important: `DATABASE_URL`, `SECRET_KEY`, `ENVIRONMENT`, `PORT`.  
Same-origin Docker/Railway: leave `CORS_ORIGINS` and `VITE_API_BASE_URL` empty. Do not commit `.env`.

---

## Docker / Railway

One image: Node builds `frontend/dist`, Python serves API + SPA.

```bash
sh -c 'uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --workers 1 --app-dir backend'
```

**Always `--workers 1`** — Live Simulation is in-process memory. A restart resets sim state.

Health check path: `/api/v1/health`.

---

## CLI (from `backend/`)

| Command | Purpose |
|---|---|
| `alembic upgrade head` | Apply schema (production) |
| `seed` | Mumbai zones, bins, vehicles |
| `generate-history` | Telemetry for the fill model |
| `train-fill-model` / `predict` | Fill model and forecasts |
| `train-classifier` | Image model (needs labelled photos) |
| `priorities` / `optimize-routes` | Rank and plan |
| `detect-alerts` / `analytics` / `recommendations` | Ops jobs |

---

## Repository

```
backend/     FastAPI, Alembic, ML, routing, simulation
frontend/     React + Vite dashboard
ml/artifacts/ trained weights + metrics.json
Dockerfile    production CPU image (frontend + API)
```
