# EcoFlow AI — production image (CPU).
# Stage 1 builds the Vite dashboard. Stage 2 runs FastAPI with one uvicorn worker
# (Live Simulation is in-process memory). Same origin: / → SPA, /api/v1 → API.

FROM node:20-bookworm-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# Leave VITE_API_BASE_URL unset so the production bundle calls same-origin /api/v1.
RUN npm run build

FROM python:3.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ENVIRONMENT=production \
    DEBUG=false \
    LOG_LEVEL=INFO \
    HOST=0.0.0.0 \
    PORT=8000 \
    PYTHONPATH=/app/backend

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY ml ./ml
COPY ml/artifacts/fill_rate_gbr.joblib ./ml/artifacts/fill_rate_gbr.joblib
COPY ml/artifacts/waste_mobilenetv3.pt ./ml/artifacts/waste_mobilenetv3.pt
COPY --from=frontend /frontend/dist ./frontend/dist

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --app-dir backend"]
