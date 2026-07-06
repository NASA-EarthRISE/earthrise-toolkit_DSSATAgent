# ===========================================================================
# Single-stage Dockerfile for EarthRISEAgents
#
# IMPORTANT — Python packages are NOT baked into this image.
#
# In K8s, a `venv-builder` initContainer runs `scripts/build_venv.sh` to
# install requirements.txt + the local DSSATTools fork into a venv on the
# `earthrise` PVC at /earthrise/venv. All pod containers mount the PVC and
# inherit PYTHONPATH/PATH (set below) pointing at that venv.
#
# NOTE: This Dockerfile is the K8s/production build and is NOT used by
# docker-compose. Local docker-compose dev builds from `Dockerfile.dev`
# (which bakes deps into the image) — there is no `venv-builder` service or
# /earthrise/venv volume in docker-compose.yml. The venv-builder flow is
# K8s-only.
#
# Knowledge documents live on the PVC at $KNOWLEDGE_DOCUMENTS_DIR (also not baked).
# ===========================================================================

FROM --platform=linux/amd64 python:3.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Runtime-only system deps:
#   gdal-bin / postgresql-client — CLI tools used by management commands.
#   libgdal/libpq runtimes are pulled transitively. libeccodes0 for cfgrib.
#   libgfortran5 + libquadmath0 — the precompiled DSSAT (dscsm048) Fortran
#   binary dynamically links against them at exec time.
RUN apt-get update && apt-get install -y --no-install-recommends \
        gdal-bin \
        postgresql-client \
        postgis \
        libeccodes0 \
        libgfortran5 \
        libquadmath0 \
    && rm -rf /var/lib/apt/lists/*

# venv lives on the PVC; image only declares where to find it. If the
# PVC is not mounted (or venv-builder hasn't run yet), Python will fail
# to import third-party packages — that's the intended failure mode.
ENV VENV_DIR=/earthrise/venv \
    PATH=/earthrise/venv/bin:$PATH \
    PYTHONPATH=/earthrise/venv/lib/python3.11/site-packages

WORKDIR /app

COPY manage.py .
COPY requirements.txt .
COPY pytest.ini .
COPY dssat_chat_project/ dssat_chat_project/
COPY accounts/ accounts/
COPY earthrise_agents_base/ earthrise_agents_base/
COPY dssat_chat_agent/ dssat_chat_agent/
COPY data_agent/ data_agent/
COPY dssat_agent/ dssat_agent/
COPY knowledge_agent/ knowledge_agent/
COPY tests/ tests/

RUN mkdir -p /tmp/dataagent /tmp/simulation /app/staticfiles /app/media

COPY scripts/entrypoint.sh /entrypoint.sh
COPY scripts/build_venv.sh /usr/local/bin/build_venv.sh
RUN chmod +x /entrypoint.sh /usr/local/bin/build_venv.sh

# NOTE: collectstatic is intentionally NOT run at image-build time —
# it requires Django (only available after the venv is built on the PVC).
# entrypoint.sh handles it on first boot.

# ---------------------------------------------------------------------------
# Stage: web — Django ASGI server (K8s / production)
# ---------------------------------------------------------------------------
FROM runtime AS web

EXPOSE 8100
ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "dssat_chat_project.asgi:application", "--host", "0.0.0.0", "--port", "8100"]

# ---------------------------------------------------------------------------
# Stage: celery — Celery worker (K8s / production)
# ---------------------------------------------------------------------------
FROM runtime AS celery

ENTRYPOINT ["/entrypoint.sh"]
CMD ["celery", "-A", "dssat_chat_project", "worker", \
     "--loglevel=info", \
     "--concurrency=4", \
     "-Q", "dssat_chat_project,data_agent,dssat_agent,knowledge_agent"]
