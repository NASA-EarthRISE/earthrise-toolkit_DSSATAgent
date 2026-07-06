#!/bin/bash
# ===========================================================================
# build_venv.sh — install Python deps into the PVC-resident venv.
#
# Run by:
#   - K8s `venv-builder` initContainer (after `repo-updater` syncs source)
#
# NOTE: This script is K8s-only. docker-compose does NOT use it — the compose
# stack builds from `Dockerfile.dev`, which bakes deps into the image (there is
# no `venv-builder` service in docker-compose.yml).
#
# Behavior:
#   - Skips reinstall if requirements.txt hash matches stored hash.
#   - Always reinstalls the local DSSATTools fork (small, may be edited
#     between deploys without bumping requirements.txt).
#   - Installs the build toolchain on the fly using the running container's
#     apt — assumes a python:3.11-bookworm-based image (see deployment.yml).
#
# Env:
#   APP_DIR     — source tree (default /earthrise/app)
#   VENV_DIR    — venv location (default /earthrise/venv)
#   FORCE       — set to 1 to rebuild regardless of hash
# ===========================================================================
set -euo pipefail

APP_DIR="${APP_DIR:-/earthrise/app}"
VENV_DIR="${VENV_DIR:-/earthrise/venv}"
REQ_FILE="$APP_DIR/requirements.txt"
DSSATTOOLS_DIR="$APP_DIR/dssat_agent/DSSATTools"
HASH_FILE="$VENV_DIR/.req-hash"

if [ ! -f "$REQ_FILE" ]; then
    echo "[build_venv] ERROR: requirements.txt not found at $REQ_FILE" >&2
    echo "[build_venv]        Did the repo-updater initContainer run first?" >&2
    exit 1
fi

NEW_HASH=$(sha256sum "$REQ_FILE" | cut -d' ' -f1)
OLD_HASH=$(cat "$HASH_FILE" 2>/dev/null || echo "")

# Toolchain needed to build wheels for rasterio / psycopg2 / cfgrib /
# DSSATTools etc. Installed at runtime so the image itself doesn't carry
# ~500 MB of -dev headers it never uses post-build.
install_build_toolchain() {
    apt-get update -qq
    apt-get install -y --no-install-recommends \
        gcc g++ gfortran \
        libgdal-dev libpq-dev libeccodes-dev \
        > /dev/null
}

install_requirements() {
    echo "[build_venv] requirements.txt changed (or first run); rebuilding venv at $VENV_DIR"
    install_build_toolchain
    rm -rf "$VENV_DIR"
    python -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" install --upgrade pip
    "$VENV_DIR/bin/pip" install --no-cache-dir -r "$REQ_FILE"
    echo "$NEW_HASH" > "$HASH_FILE"
}

install_dssattools() {
    echo "[build_venv] (Re)installing local DSSATTools fork from $DSSATTOOLS_DIR"
    if [ ! -d "$DSSATTOOLS_DIR" ]; then
        echo "[build_venv] ERROR: DSSATTools source not found at $DSSATTOOLS_DIR" >&2
        exit 1
    fi
    # DSSATTools setup.py declares no ext_modules — it's a pure-Python
    # package + a precompiled binary in package_data, so no toolchain is
    # needed here. (If that ever changes, the install will fail loudly
    # and we add the toolchain back.)
    "$VENV_DIR/bin/pip" install --no-cache-dir --force-reinstall --no-deps "$DSSATTOOLS_DIR"
}

if [ "${FORCE:-0}" = "1" ] || [ "$NEW_HASH" != "$OLD_HASH" ] || [ ! -x "$VENV_DIR/bin/python" ]; then
    install_requirements
else
    echo "[build_venv] requirements.txt unchanged (hash $NEW_HASH); skipping pip install"
fi

install_dssattools

echo "[build_venv] Done. venv at $VENV_DIR"
"$VENV_DIR/bin/python" --version
# Use sed instead of `head` — head closes the pipe after 20 lines, which
# triggers SIGPIPE in pip and a non-zero exit under `pipefail`. sed reads
# the whole stream and just suppresses output past the limit, so pip
# always exits cleanly.
"$VENV_DIR/bin/pip" list --format=columns | sed -n '1,20p'
