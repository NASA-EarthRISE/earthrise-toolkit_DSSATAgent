FROM postgis/postgis:15-3.4

# Install pgvector extension
RUN apt-get update -qq \
    && apt-get install -y --no-install-recommends postgresql-15-pgvector \
    && rm -rf /var/lib/apt/lists/*

# Init script to enable extensions on first start
COPY init-extensions.sh /docker-entrypoint-initdb.d/20-extensions.sh
