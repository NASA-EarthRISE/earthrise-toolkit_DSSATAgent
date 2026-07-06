"""
Startup hooks for dssat_agent.

This package contains modules called by dssat_agent/apps.py during the
post_migrate signal to initialize subsystems that depend on the database
being migrated and other apps' models being loaded.
"""
