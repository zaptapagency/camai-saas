# CamAI edge agent — runs ON-SITE on the customer's box (not part of the cloud
# deploy). Pulls the ML stack (torch/ultralytics/opencv), so the image is large;
# add an NVIDIA base + runtime for GPU. Build from the REPO ROOT:
#   docker build -f packages/edge-agent/Dockerfile -t camai-edge .
# Run against a site config + source (RTSP url or mounted video):
#   docker run --rm -v $PWD/site.yaml:/config/site.yaml camai-edge
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
# OpenCV runtime libs.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY packages/schema /schema
RUN pip install -e /schema
COPY packages/edge-agent/ /app/
RUN pip install -e .

# Mount a site config at /config/site.yaml (cameras, zones, cloud.ingest_url).
# yolo weights auto-download on first run; vendor fine-tuned weights for prod.
ENTRYPOINT ["camai-edge", "--config", "/config/site.yaml"]
