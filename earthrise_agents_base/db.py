"""
Shared raw-database-connection helpers.

Several sub-agents open a raw psycopg2 (or psycopg v3) connection to the
same PostgreSQL/PostGIS instance Django is configured against — bypassing
the ORM for PostGIS raster/vector SQL, tile rendering, LangGraph
checkpointing, etc. This module centralizes the ``settings.DATABASES``
→ connection-params translation so every call site resolves host/port/
dbname/user/password identically.

- ``get_db_params(overrides=None)`` — resolve the connection params dict
  (for subprocess callers like ``raster2pgsql | psql``).
- ``get_raw_connection(...)`` — open a live raw connection with either
  psycopg2 (default) or psycopg v3 (for LangGraph's PostgresSaver).
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def get_db_params(overrides: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """Resolve raw connection params from ``settings.DATABASES['default']``.

    Returns a dict with the keys ``host``, ``port``, ``dbname``, ``user``,
    ``password`` — the shape ``psycopg2.connect(**params)`` expects, and
    the values a subprocess (``raster2pgsql | psql``) needs.

    ``overrides`` is an optional dict merged over the resolved Django
    ``DATABASES['default']`` entry (Django's upper-case keys: ``HOST``,
    ``PORT``, ``NAME``, ``USER``, ``PASSWORD``), letting callers point at
    a different target DB without re-implementing the defaults.
    """
    from django.conf import settings

    db: Dict[str, Any] = dict(settings.DATABASES["default"])
    if overrides:
        db.update(overrides)

    return {
        "host": db.get("HOST", "localhost"),
        "port": str(db.get("PORT", "5432")),
        "dbname": db.get("NAME", "postgres"),
        "user": db.get("USER", "postgres"),
        "password": db.get("PASSWORD", ""),
    }


def get_raw_connection(
    *,
    driver: str = "psycopg2",
    autocommit: bool = False,
    overrides: Optional[Dict[str, Any]] = None,
):
    """Open a raw DB connection using ``settings.DATABASES['default']``.

    ``driver``:
        - ``"psycopg2"`` (default) — ``psycopg2.connect(**params)``; sets
          ``conn.autocommit = True`` when ``autocommit`` is requested.
        - ``"psycopg"`` — psycopg v3; builds a ``postgresql://`` DSN and
          connects with the given ``autocommit`` (LangGraph's
          ``PostgresSaver`` case).

    ``autocommit`` toggles autocommit on the returned connection.
    ``overrides`` is forwarded to :func:`get_db_params`.
    """
    params = get_db_params(overrides)

    if driver == "psycopg":
        import psycopg  # psycopg v3

        dsn = (
            f"postgresql://{params['user']}:{params['password']}@"
            f"{params['host']}:{params['port']}/{params['dbname']}"
        )
        return psycopg.connect(dsn, autocommit=autocommit)

    import psycopg2

    conn = psycopg2.connect(**params)
    if autocommit:
        conn.autocommit = True
    return conn
