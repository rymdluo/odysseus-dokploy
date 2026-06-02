# Self-contained Dockerfile for Odysseus — clones from GitHub at build time.
# Pin ODYSSEUS_REF to a commit SHA in production; "main" is a moving target.
#
# Local test build:  docker build --build-arg ODYSSEUS_REF=main -t odysseus:custom .
# Real deploys go through the companion docker-compose.yml — NOT this Dockerfile
# alone — so /app/data lands on a named volume that survives redeploys. Building
# the image standalone (e.g. as a Dokploy "Application") gets anonymous volumes;
# see the VOLUME note below.

FROM python:3.12-slim

ARG ODYSSEUS_REPO=https://github.com/pewdiepie-archdaemon/odysseus.git
ARG ODYSSEUS_REF=main

# System deps (same set as upstream Dockerfile — tmux for Cookbook, openssh
# for remote serving, build tools for llama.cpp, node/npm for the Browser MCP,
# gosu for clean PUID/PGID drop in the entrypoint).
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    cmake \
    curl \
    git \
    nodejs \
    npm \
    tmux \
    openssh-client \
    gosu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Clone the repo at the requested ref. Using --depth=1 keeps the image small;
# drop it if you need the full history at runtime.
RUN git clone --depth=1 --branch "${ODYSSEUS_REF}" "${ODYSSEUS_REPO}" /tmp/odysseus \
    && cp -a /tmp/odysseus/. /app/ \
    && rm -rf /tmp/odysseus /app/.git

# Bust the layer cache when the upstream branch moves. Pass a fresh value
# (a date, a SHA, anything) to force a re-clone without bumping ODYSSEUS_REF.
ARG CACHEBUST=1
RUN echo "Build cache bust: ${CACHEBUST}"

RUN pip install --no-cache-dir -r requirements.txt

# Ensure runtime dirs exist before the volume is mounted on top of them.
RUN mkdir -p /app/data /app/logs /app/services/cache/search

# Make the entrypoint executable (it lives in docker/entrypoint.sh inside the repo).
RUN chmod +x /app/docker/entrypoint.sh

# NOTE: intentionally no VOLUME instruction. The companion docker-compose.yml
# mounts NAMED volumes onto /app/data and /app/logs. A Dockerfile VOLUME forces
# ANONYMOUS volumes (random 64-hex names) whenever the image runs without those
# explicit mounts — e.g. when deployed as a Dokploy "Application" instead of a
# "Compose" service — silently breaking managed persistence and Volume Backups.
# Always deploy via Compose so the named volumes apply.

# --- tracker-capture skill seed (wrapper-level; no upstream fork) ---
# Bundle seed assets OUTSIDE /app so the /app/data named volume can't mask
# them. seed-entrypoint.sh copies them into the data volume on boot (only if
# absent), then execs Odysseus's real entrypoint (PUID/PGID chown+drop, CMD).
COPY seed/skills/ /opt/odysseus-seed/skills/
COPY seed-entrypoint.sh /usr/local/bin/seed-entrypoint.sh
RUN chmod +x /usr/local/bin/seed-entrypoint.sh

EXPOSE 7000

ENTRYPOINT ["/usr/local/bin/seed-entrypoint.sh"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7000"]
