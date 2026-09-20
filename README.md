# EcoFlow AI

Intelligent Waste Operations.

Monitors waste bins, forecasts overflow, classifies waste from images, prioritises
collections, and plans capacity-constrained vehicle routes — with an operations
dashboard over OpenStreetMap. Seeded network is modelled on **Mumbai**.

The application is **not** deployed from this repository by default. The notes
below prepare a production release; they do not mean a public instance exists.

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
                                              └──> Analytics / What-If / Live Simulation
                                                        │
                 React + Vite dashboard <──REST─────────┘  (Leaflet + OSM, Recharts)
```

## Technology

| Layer | Choice |
|---|---|
| Backend | FastAPI + SQLAlchemy 2.0 + Alembic |
| Database | PostgreSQL |
| Fill prediction | scikit-learn `HistGradientBoostingRegressor` |
| Waste classification | PyTorch + MobileNetV3 (CPU) |
| Routing | Google OR-Tools CVRP |
| Frontend | React + Vite + JavaScript + Tailwind |
| Maps | Leaflet + OpenStreetMap |
| IoT | amqtt broker + paho-mqtt (optional; dashboard Live Simulation uses HTTP) |

## Repository layout

```
backend/                 FastAPI app, Alembic, CLI
frontend/                 React + Vite dashboard
ml/artifacts/             model weights (not committed) and metrics.json
requirements.txt          every Python dependency, in one file
Dockerfile                production CPU image for the API
Procfile                  Railway/Render-style web process
```

---

## A. Local development

**Prerequisites:** Python 3.11, Node 20+, PostgreSQL.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

Copy-Item .env.example .env
# set DATABASE_URL and SECRET_KEY in .env

cd backend
alembic upgrade head
python -m app.cli seed
python -m app.cli generate-history --days 90
python -m app.cli train-fill-model
python -m app.cli predict

# API (local default port 8000)
uvicorn app.main:app --reload --app-dir backend --host 127.0.0.1 --port 8000

# Dashboard (second terminal)
cd frontend
npm install
npm run dev
```

Vite (`http://localhost:5173`) proxies `/api` to `http://127.0.0.1:8000`. Leave
`VITE_API_BASE_URL` empty locally.

Interactive API docs (development only): <http://localhost:8000/docs>

`python -m app.cli init-db` still exists for local SQLite/create_all. Prefer
Alembic even locally so the schema matches production.

MQTT broker/bridge/CLI simulator remain available; the dashboard Live Simulation
does not need them.

---

## B. Required environment variables

| Variable | Purpose |
|---|---|
| `ENVIRONMENT` | `production` disables debug, docs, and localhost CORS fallbacks |
| `DEBUG` | Forced false in production |
| `LOG_LEVEL` | `INFO` in production |
| `SECRET_KEY` | Required; never commit a real value |
| `DATABASE_URL` | PostgreSQL URL (`postgres://` is rewritten to `postgresql+psycopg://`) |
| `CORS_ORIGINS` | Deployed frontend origin(s). Empty = same-origin. No `*` |
| `APP_NAME` | `EcoFlow AI` |
| `HOST` / `PORT` | Local default `127.0.0.1:8000`. Production binds `0.0.0.0` and `$PORT` |
| `OSRM_ENABLED` | Road distances; falls back to haversine if false/unreachable |
| `ML_ARTIFACT_DIR` | Directory for `.joblib` / `.pt` files |
| `ROUTE_SOLVER_TIME_LIMIT_SECONDS` | OR-Tools time limit |
| `VITE_API_BASE_URL` | Frontend only. API origin when UI is hosted separately |

See `.env.example` and `frontend/.env.example`. Do not commit `.env` files.

---

## C. Database setup

Use PostgreSQL in production (Neon, Railway, Render, or similar).

After the URL is set:

```powershell
cd backend
alembic upgrade head
python -m app.cli seed
```

Optional demo richness (large): `python -m app.cli generate-history --days 90`.

---

## D. Alembic migration

Production **must** apply the versioned schema:

```powershell
cd backend
alembic upgrade head
```

Do not rely on `init-db` / `create_all` for a deployed database.

If an existing database was created with `init-db` and already matches this
schema, stamp instead of re-running create:

```powershell
cd backend
alembic stamp head
```

---

## E. Model artifact requirements

Trained weights are **gitignored**. Classification and gradient-boosting forecasts
need these files under `ML_ARTIFACT_DIR` (default `ml/artifacts/`):

- `fill_rate_gbr.joblib`
- `waste_mobilenetv3.pt`

Upload or copy them onto the API host. Fill prediction degrades to rolling /
zone fallbacks without the joblib file. Image classification is unavailable
without the `.pt` file. Do not commit `*.joblib`, `*.pt`, or `*.pth`.

---

## F. Frontend production build

```powershell
cd frontend
# Split hosting: set the API origin before build
#   $env:VITE_API_BASE_URL="https://your-api-domain.example.com"
npm ci
npm run build
```

Output is `frontend/dist/`. Empty `VITE_API_BASE_URL` keeps same-origin `/api/v1`
(combined deploy or a reverse proxy).

---

## G. Backend production start command

Use the platform `PORT`. **One worker** is required.

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1 --app-dir backend
```

PowerShell local equivalent:

```powershell
uvicorn app.main:app --host 0.0.0.0 --port $env:PORT --workers 1 --app-dir backend
```

---

## H. Docker usage

The `Dockerfile` is a CPU image for the API (Python 3.11, `requirements.txt`,
no CUDA). It does not bake a frontend build.

```powershell
docker build -t ecoflow-ai .
docker run -p 8000:8000 `
  -e PORT=8000 `
  -e ENVIRONMENT=production `
  -e DATABASE_URL="postgresql+psycopg://..." `
  -e SECRET_KEY="..." `
  -e CORS_ORIGINS="https://your-frontend-domain.example.com" `
  ecoflow-ai
```

Run migrations against the same `DATABASE_URL` before serving traffic
(`alembic upgrade head` from `backend/`). Copy model artifacts into
`ml/artifacts` on the host or into the image at runtime.

---

## I. Deployment architecture

Recommended public demo:

1. **PostgreSQL** — Neon, Railway, or Render Postgres
2. **Always-on FastAPI** — Railway or Render Web Service (not serverless). Use
   the `Procfile` / Docker start command with `--workers 1`
3. **Frontend** — Vercel/Netlify static `dist/` with `VITE_API_BASE_URL`, **or**
   copy `frontend/dist` next to the API so FastAPI serves the SPA at `/` and
   the API at `/api/v1` (same origin; leave `CORS_ORIGINS` empty)

Do not host the API on Vercel/Netlify functions. Live Simulation, OR-Tools, and
PyTorch need a persistent process and enough RAM (about 2 GB if the classifier
stays enabled).

---

## J. Important Live Simulation limitation

Live Simulation is **in-process memory**. Therefore:

- use **one** uvicorn worker (`--workers 1`)
- restarting the service **resets** simulation state
- multiple workers must **not** be used (each worker would have its own state)

No Redis or shared store is used.

---

## K. Health check endpoint

| Method | Path | Meaning |
|---|---|---|
| GET | `/api/v1/health` | Process is up (no database, no ML) |
| GET | `/api/v1/health/ready` | Database `SELECT 1` (503 if unreachable) |

Point the platform health check at `/api/v1/health`.

Swagger and ReDoc are enabled in development and disabled when
`ENVIRONMENT=production`.

---

## CLI reference

| Command | Purpose |
|---|---|
| `init-db` | Create tables from models (local only; production uses Alembic) |
| `seed` | Mumbai zones, bins and vehicles |
| `generate-history` | Backfill readings and collection events |
| `train-fill-model` / `predict` | Fill-rate model and forecasts |
| `train-classifier` | Optional image classifier |
| `priorities` / `optimize-routes` | Priority ranking and CVRP plan |
| `detect-alerts` / `analytics` / `recommendations` | Ops jobs |

---

## Deliverable coverage

| Requirement | Where it lives |
|---|---|
| Bin monitoring | `models/bin.py`, `/api/v1/bins` |
| Fill-level prediction | `ml/fill_prediction/`, `/api/v1/predictions` |
| Waste classification | `ml/classification/`, `/api/v1/classify` |
| Collection prioritisation | `services/prioritization.py`, `/api/v1/priorities` |
| Route optimization | `services/routing/`, `/api/v1/routes/optimize` |
| Dashboard | `frontend/` |
| Live Simulation | `/api/v1/simulation` |
| What-If | `/api/v1/scenarios/what-if` |
| Alerts | `/api/v1/alerts` |
| Analytics & recommendations | `/api/v1/analytics` |
