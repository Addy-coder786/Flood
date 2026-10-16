# FloodNet API (FastAPI) for any container host with a STATIC outbound IP (IMD keys are IP-bound).
# Build from the repository root:  docker build -t floodnet-api .
# Run:  docker run -p 8000:8000 --env-file .env floodnet-api      (.env is never copied into the image)
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY backend/pyproject.toml backend/pyproject.toml
COPY backend/floodnet backend/floodnet
# Editable install: floodnet/config.py resolves the data directory relative to the SOURCE tree.
RUN pip install --no-cache-dir -e "./backend[radar]"

# Pilot data the API loads at start-up (paths are resolved relative to the repo root, not the CWD).
COPY data/processed/pilot data/processed/pilot

# Run as an unprivileged user; data/interim holds the recreatable radar-frame / last-good caches.
RUN useradd --system --uid 10001 floodnet && mkdir -p /app/data/interim && chown -R floodnet /app/data/interim
USER floodnet

WORKDIR /app/backend
EXPOSE 8000
# /health answers without touching the pilot or any upstream service.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3   CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT','8000'), timeout=4).status==200 else 1)"
CMD ["sh", "-c", "uvicorn floodnet.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
