#!/bin/bash
# PostgreSQL init script — enables all required extensions.
# Runs automatically on first container start (mounted in /docker-entrypoint-initdb.d/).
set -e

echo "=== Enabling PostgreSQL extensions ==="

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    -- Spatial
    CREATE EXTENSION IF NOT EXISTS postgis;
    CREATE EXTENSION IF NOT EXISTS postgis_raster;
    CREATE EXTENSION IF NOT EXISTS postgis_topology;

    -- Vector search (for KnowledgeAgent)
    CREATE EXTENSION IF NOT EXISTS vector;

    -- Text search
    CREATE EXTENSION IF NOT EXISTS pg_trgm;
    CREATE EXTENSION IF NOT EXISTS fuzzystrmatch;
EOSQL

echo "=== Extensions enabled ==="
