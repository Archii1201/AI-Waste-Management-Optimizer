# EcoFlow AI — production API image (CPU).
# Live Simulation is in-process memory: run exactly one uvicorn worker.
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
# Fail the build if production weights are missing from the build context.
COPY ml/artifacts/fill_rate_gbr.joblib ./ml/artifacts/fill_rate_gbr.joblib
COPY ml/artifacts/waste_mobilenetv3.pt ./ml/artifacts/waste_mobilenetv3.pt

WORKDIR /app/backend

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
