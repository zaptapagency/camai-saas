# CamAI cloud control plane (FastAPI). Build from the REPO ROOT so the shared
# schema package is in context:
#   docker build -f packages/cloud/Dockerfile -t camai-cloud .
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Shared event contract first (installed editable so imports resolve everywhere).
COPY packages/schema /schema
RUN pip install -e /schema

# Cloud deps, then the app.
COPY packages/cloud/requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt
COPY packages/cloud/ /app/

# Dev SQLite store by default so the container runs out of the box; set
# CAMAI_STORE=postgres + CAMAI_PG_DSN for production (see DEPLOY.md).
ENV CAMAI_STORE=sqlite \
    CAMAI_SNAPSHOT_DIR=/data/snaps \
    CAMAI_SNAPSHOT_DB=/data/snaps.db

RUN mkdir -p /data
EXPOSE 8000

# Single worker keeps the SQLite dev store coherent; scale out on Postgres.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
