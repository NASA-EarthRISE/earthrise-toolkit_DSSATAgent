"""
Batch experiment service for multi-location simulation.

Resolves spatial selections to discrete locations, dispatches child
experiments via existing workflow services, and aggregates results
across locations.
"""

import logging
import math

from dssat_agent.models import BatchExperiment, ExperimentSession

logger = logging.getLogger(__name__)

MAX_BATCH_LOCATIONS = 200


def resolve_batch_locations(location_config):
    """
    Resolve a batch location config to a list of discrete locations.

    Parameters
    ----------
    location_config : dict
        Must include 'mode' (admin_boundary, points, or bbox_grid)
        plus mode-specific fields.

    Returns
    -------
    list of dict
        [{'name': str, 'lat': float, 'lon': float}, ...]
    """
    mode = location_config.get('mode', 'admin_boundary')

    if mode == 'admin_boundary':
        return _resolve_admin_boundary(location_config)
    elif mode == 'points':
        return _resolve_points(location_config)
    elif mode == 'bbox_grid':
        return _resolve_bbox_grid(location_config)
    else:
        raise ValueError(f"Unknown location mode: {mode}")


def _resolve_admin_boundary(config):
    """Expand an admin boundary parent into child unit centroids."""
    from . import spatial_data_service

    parent_name = config.get('admin_parent', '')
    parent_level = config.get('admin_parent_level', 'admin1')
    child_level = config.get('admin_child_level', 'admin2')

    if not parent_name:
        raise ValueError("admin_parent is required for admin_boundary mode")

    locations = spatial_data_service.get_admin_sub_unit_centroids(
        parent_name=parent_name,
        parent_level=parent_level,
        child_level=child_level,
    )

    if not locations:
        raise ValueError(
            f"No {child_level} sub-units found for '{parent_name}' "
            f"(parent_level={parent_level}). Check that GADM data is loaded."
        )

    # Optional filtering by selected_units
    selected = config.get('selected_units')
    if selected:
        selected_set = {name.lower() for name in selected}
        locations = [loc for loc in locations if loc['name'].lower() in selected_set]

    return locations


def _resolve_points(config):
    """Validate a user-provided list of named points."""
    points = config.get('points', [])
    if not points:
        raise ValueError("At least one point is required for points mode")

    locations = []
    for i, pt in enumerate(points):
        lat = pt.get('lat')
        lon = pt.get('lon')
        if lat is None or lon is None:
            raise ValueError(f"Point {i} missing lat or lon: {pt}")
        locations.append({
            'name': pt.get('name', f'Point {i + 1}'),
            'lat': float(lat),
            'lon': float(lon),
        })
    return locations


def _resolve_bbox_grid(config):
    """Generate a regular grid of named points within a bounding box."""
    from . import spatial_data_service

    bbox = config.get('bbox', {})
    spacing = config.get('spacing_deg', 0.5)

    points = spatial_data_service.generate_grid_from_bbox(
        min_lat=bbox.get('min_lat', 0),
        max_lat=bbox.get('max_lat', 0),
        min_lon=bbox.get('min_lon', 0),
        max_lon=bbox.get('max_lon', 0),
        spacing_deg=spacing,
    )

    return [
        {'name': f'Grid {i + 1} ({lat:.2f}, {lon:.2f})', 'lat': lat, 'lon': lon}
        for i, (lon, lat) in enumerate(points)
    ]


def create_batch_children_from_pairs(batch, pair_params,
                                      sub_experiment_type='single'):
    """Create one child ``ExperimentSession`` per pre-built per-pair params dict.

    This is the wizard-draft batch path: instead of merging a single shared
    ``template_params`` with N locations, each ``pair_params[i]`` is the
    fully-resolved params for one ``(Field, Treatment)`` pair (already
    contains crop/cultivar/planting/management/lat/lon/soil/weather). Used
    when ``services.draft_submit._build_batch`` hands a pair list down
    rather than a template.

    Returns ``list[(child_id, per_pair_params)]`` matching the shape that
    ``run_batch_child`` consumes.
    """
    children = []
    for i, raw in enumerate(pair_params):
        per_pair = dict(raw)
        per_pair['experiment_type'] = sub_experiment_type
        loc_label = (per_pair.get('location_name')
                     or per_pair.get('location_label')
                     or f'Pair {i + 1}')
        child = ExperimentSession.objects.create(
            status='queued',
            experiment_type=sub_experiment_type,
            crop_code=per_pair.get('crop_code', ''),
            cultivar_code=per_pair.get('cultivar_code', ''),
            batch=batch,
            location_label=loc_label,
            label=f"{batch.label} — {loc_label}"[:200],
            chat_id=batch.chat_id,
            raw_params=per_pair,
        )
        children.append((str(child.pk), per_pair))
    return children


def create_batch_children(batch, locations, template_params, sub_experiment_type):
    """
    Create child ExperimentSession records for each location.

    Returns list of (child_id, per_location_params) tuples.
    """
    children = []
    for loc in locations:
        # Merge template with location-specific overrides
        per_loc_params = dict(template_params)
        per_loc_params['latitude'] = loc['lat']
        per_loc_params['longitude'] = loc['lon']
        per_loc_params['location_name'] = loc['name']
        per_loc_params['experiment_type'] = sub_experiment_type

        child = ExperimentSession.objects.create(
            status='queued',
            experiment_type=sub_experiment_type,
            crop_code=template_params.get('crop_code', ''),
            cultivar_code=template_params.get('cultivar_code', ''),
            batch=batch,
            location_label=loc['name'],
            label=f"{batch.label} — {loc['name']}",
            chat_id=batch.chat_id,
            raw_params=per_loc_params,
        )
        children.append((str(child.pk), per_loc_params))

    return children


def aggregate_batch_results(batch_id):
    """
    Compute cross-location statistics for a completed batch.

    Reads child SimulationResult summaries and produces yield/maturity
    statistics plus a per-location results table.

    Returns the aggregate_summary dict.
    """
    from dssat_agent.models import SimulationResult

    batch = BatchExperiment.objects.get(pk=batch_id)
    children = batch.children.all()

    per_location = []
    yields = []
    maturities = []

    for child in children:
        result = child.results.order_by('-completed_at').first()
        entry = {
            'name': child.location_label,
            'lat': None,
            'lon': None,
            'experiment_id': str(child.pk),
            'status': child.status,
            'yield_kg_ha': None,
            'maturity_days': None,
        }

        # Extract lat/lon from raw_params
        if child.raw_params:
            entry['lat'] = child.raw_params.get('latitude')
            entry['lon'] = child.raw_params.get('longitude')

        if result and result.summary:
            s = result.summary
            # Handle both single results and MC quantile results
            yield_val = (
                s.get('yield_kg_ha')
                or s.get('hwam')
                or (s.get('yield_kg_ha', {}).get('mean') if isinstance(s.get('yield_kg_ha'), dict) else None)
            )
            mat_val = (
                s.get('maturity_days')
                or s.get('mat')
                or (s.get('maturity_days', {}).get('mean') if isinstance(s.get('maturity_days'), dict) else None)
            )

            if yield_val is not None:
                try:
                    yield_val = float(yield_val)
                    entry['yield_kg_ha'] = round(yield_val, 1)
                    yields.append(yield_val)
                except (TypeError, ValueError):
                    pass

            if mat_val is not None:
                try:
                    mat_val = float(mat_val)
                    entry['maturity_days'] = round(mat_val, 1)
                    maturities.append(mat_val)
                except (TypeError, ValueError):
                    pass

        per_location.append(entry)

    summary = {
        'total_locations': batch.total_locations,
        'completed': batch.completed_count,
        'failed': batch.failed_count,
        'per_location': per_location,
    }

    if yields:
        summary['yield_stats'] = _compute_stats(yields)
    if maturities:
        summary['maturity_stats'] = _compute_stats(maturities)

    # Track failed location names
    failed_names = [e['name'] for e in per_location if e['status'] == 'failed']
    if failed_names:
        summary['failed_locations'] = failed_names

    return summary


def _compute_stats(values):
    """Compute basic statistics for a list of numeric values."""
    if not values:
        return {}
    n = len(values)
    sorted_vals = sorted(values)
    mean = sum(sorted_vals) / n
    stats = {
        'mean': round(mean, 1),
        'median': round(sorted_vals[n // 2], 1),
        'min': round(sorted_vals[0], 1),
        'max': round(sorted_vals[-1], 1),
        'count': n,
    }
    if n > 1:
        variance = sum((v - mean) ** 2 for v in sorted_vals) / (n - 1)
        stats['std'] = round(math.sqrt(variance), 1)
    else:
        stats['std'] = 0.0
    return stats
