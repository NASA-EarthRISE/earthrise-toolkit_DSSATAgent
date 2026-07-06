"""This product-shell app defines no database models — it only brands the
platform and registers the DSSAT knowledge tenant from ``data/tenants.yaml``.

This module exists (intentionally empty) so the app has a ``models_module``.
Django's ``migrate`` only emits the ``post_migrate`` signal for app configs
whose ``models_module`` is not ``None`` (see
``django.core.management.sql.emit_post_migrate_signal``). Without this file the
shell's ``post_migrate`` handler in ``apps.py`` — which registers the knowledge
tenant/sources — would never fire on a fresh ``migrate``, leaving a freshly
initialized deployment with no registered tenant. Keeping this empty module
ensures that handler runs. It adds no models and therefore no migrations.
"""
