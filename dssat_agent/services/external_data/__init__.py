"""
External data clients for in-situ soil/planting reference lookups.

Modules:
  - base.py: ExternalSoilSource ABC with caching, coverage check, error handling
  - soilgrids_client.py: ISRIC SoilGrids 2.0 REST client (global)
  - ssurgo_client.py: USDA Soil Data Access SQL client (US-only)

These clients live in dssat_agent because they produce DSSAT-domain output
(layered StoredSoilProfile rows). data_agent stays generic and knows nothing
about external soil databases.
"""
