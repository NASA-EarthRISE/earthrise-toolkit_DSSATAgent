"""
Pytest config for the live end-to-end integration tests.

These tests (all ``@pytest.mark.live``) exercise the full running stack —
real Ollama, real Redis, and the real, already-migrated database of the
running container with its seeded reference data (cultivars, soil profiles,
ingested knowledge documents, loaded weather data). They are the pytest
promotion of the former ``scripts/test_*.py`` smoke scripts and are only
meaningful against a live, provisioned environment (they are deselected by
default via ``-m "not live and not slow"``).

Accordingly they must run against the REAL database, not an ephemeral,
empty pytest test database — otherwise the knowledge search finds no
documents, the wizard finds no reference data, etc. We therefore:

  * make ``django_db_setup`` a no-op so pytest-django does NOT create or
    destroy a test database (it uses ``settings.DATABASES`` as-is), and
  * unblock DB access for every test here (so no per-test ``django_db``
    mark is needed).

This only affects tests under ``tests/integration/``; the default suite
deselects them, so the override is never instantiated for normal CI runs.
"""

import pytest


@pytest.fixture(scope="session")
def django_db_setup():
    """Use the running container's real database; do not create a test DB."""
    yield


@pytest.fixture(autouse=True)
def _use_real_db(django_db_blocker):
    """Allow these live tests to touch the real database (they need its
    seeded data), without requiring a per-test ``django_db`` mark."""
    with django_db_blocker.unblock():
        yield
