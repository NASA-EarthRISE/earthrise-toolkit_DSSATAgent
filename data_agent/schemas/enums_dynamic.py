"""
Runtime-built enums for the data_agent.

These functions query Django models at call time and return a list of
allowed string values. They are invoked by `chat/schemas/tool_schema.py`
when building a tool's JSON schema per request, so each request sees the
current catalog — no inter-worker cache coherency problem.
"""

from __future__ import annotations

from typing import List


def build_dataset_enum() -> List[str]:
    """
    Return the list of registered raster-dataset identifiers.

    These are the `dataset_subtype` values that consumers can pass as
    `source` to `query_data` / `check_availability`. Count is typically
    10–30 (all registered weather products plus any combined sources).
    """
    # Lazy import keeps Django ORM out of module-load time.
    from data_agent.models import RasterDataset

    codes: List[str] = []
    for ds in RasterDataset.objects.all().only("dataset_subtype"):
        if ds.dataset_subtype and ds.dataset_subtype != "Unknown_Dataset_Subtype":
            codes.append(ds.dataset_subtype)
    return sorted(set(codes))
