"""
Reference raster loader for dssat_agent.

Loads static categorical/scalar reference rasters (e.g. in-house soil-type
lookup, typical planting date) declared in
``dssat_agent/data/sources.yaml`` under the ``reference_rasters[]`` section.

Distinct from the existing weather raster pipeline:
  - One PostGIS table per raster (all bands intact, queried via
    ``data_agent.services.sample_raster_at_point``).
  - Idempotent by SHA-256 file hash, not just by name — re-runs only when
    the underlying file content has changed.
  - Optional ``soil_profiles_file`` (a DSSAT .SOL) gets seeded into
    ``StoredSoilProfile`` alongside raster registration so the legend's
    ``soil_code`` column resolves to real profiles. Reuses the existing
    ``parse_sol_file`` / ``_create_profile_from_parsed`` helpers from
    ``seed_soils`` so we don't reinvent the parser.
  - Optional ``legend`` block (CSV today, embedded RAT in the future) is
    loaded into ``SoilTypeRasterLegend`` for the categorical lookup case.
    Cross-reference verification logs orphans (legend codes with no
    matching ``StoredSoilProfile``).

Called from ``dssat_agent/apps.py:_do_register()`` inside the existing
post_migrate advisory lock, so concurrent process startup is already safe.
"""

import csv
import hashlib
import logging
import math
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
import zipfile
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


REFERENCE_RASTERS_SUBDIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),  # dssat_agent/
    'data',
)


def register_reference_rasters(sources: Dict) -> None:
    """
    Iterate ``sources['reference_rasters']`` and load each entry.

    For each raster:
      1. Compute SHA-256 of the TIFF, optional legend CSV, and optional
         soil_profiles_file.
      2. Compare to the hashes stored on the existing ``RasterDataset`` row
         (if any). Skip work that hasn't changed.
      3. If the soil_profiles_file changed: seed via parse_sol_file +
         _create_profile_from_parsed, with skip-existing semantics.
      4. If the TIFF changed: drop and re-create the PostGIS table via
         raster2pgsql with all bands intact.
      5. If the legend changed: bulk-replace SoilTypeRasterLegend rows for
         this raster, then run a cross-reference orphan check.
      6. Persist all hashes + metadata into RasterDataset.dataset_information.
    """
    entries = sources.get('reference_rasters') or []
    if not entries:
        logger.debug("No reference_rasters declared in sources.yaml")
        return

    for entry in entries:
        try:
            _register_one(entry)
        except Exception as e:
            logger.warning(
                "Failed to register reference raster %s: %s",
                entry.get('name', '<unknown>'),
                e,
                exc_info=True,
            )


def _register_one(entry: Dict) -> None:
    """Process a single reference_rasters entry. Idempotent by file hash."""
    from data_agent.models import RasterDataset
    from data_agent.services import invalidate_prefix_cache
    from django.db import transaction

    name = entry.get('name')
    if not name:
        logger.warning("Skipping reference raster entry with no name")
        return

    tiff_rel = entry.get('file')
    if not tiff_rel:
        logger.warning("Skipping reference raster %s: no 'file' specified", name)
        return

    tiff_path = os.path.join(REFERENCE_RASTERS_SUBDIR, tiff_rel)
    if not os.path.isfile(tiff_path):
        # Try the optional auto_download path before giving up. Each
        # provider is responsible for landing a clipped tif at tiff_path;
        # we never keep the intermediate (tile-level) downloads on disk.
        if not _try_auto_download(entry, tiff_path):
            logger.warning(
                "Reference raster %s: file %s not found on disk; skipping",
                name, tiff_path,
            )
            return

    legend_cfg = entry.get('legend') or {}
    legend_rel = legend_cfg.get('file') if legend_cfg else None
    legend_path = os.path.join(REFERENCE_RASTERS_SUBDIR, legend_rel) if legend_rel else None

    sol_rel = entry.get('soil_profiles_file')
    sol_path = os.path.join(REFERENCE_RASTERS_SUBDIR, sol_rel) if sol_rel else None

    tiff_hash = _sha256(tiff_path)
    legend_hash = _sha256(legend_path) if legend_path and os.path.isfile(legend_path) else None
    sol_hash = _sha256(sol_path) if sol_path and os.path.isfile(sol_path) else None

    table_name = (
        entry.get('subroutines', {}).get('reference_lookup', {}).get('table_name')
    )
    if not table_name:
        logger.warning(
            "Reference raster %s: missing subroutines.reference_lookup.table_name",
            name,
        )
        return

    # Look up or create the RasterDataset row. dataset_name is the
    # human-readable label shown in the explorer / map UI; default to
    # the yaml's `dataset_name`, falling back to `domain`, then `name`.
    display_name = entry.get('dataset_name') or entry.get('domain') or name
    with transaction.atomic():
        ds, _created = RasterDataset.objects.select_for_update().get_or_create(
            dataset_subtype=name,
            defaults={
                'dataset_name': display_name,
                'data_category': 'observational',
                'data_type': 'raster',
                'is_pipeline_enabled': False,
                'dataset_information': {},
            },
        )
        # Refresh display name on every run so yaml edits propagate
        # without requiring a wipe of the existing row.
        if ds.dataset_name != display_name:
            ds.dataset_name = display_name

        existing_info = ds.dataset_information or {}
        prior_tiff_hash = existing_info.get('tiff_hash')
        prior_legend_hash = existing_info.get('legend_hash')
        prior_sol_hash = existing_info.get('soil_profiles_hash')

        # Step 1: Seed soil profiles from associated .SOL if its hash changed.
        if sol_path and sol_hash and sol_hash != prior_sol_hash:
            seeded, skipped, failed = _seed_soil_profiles(sol_path)
            logger.info(
                "[ref-raster %s] Seeded %d soil profiles from %s "
                "(skipped %d existing, %d failed)",
                name, seeded, os.path.basename(sol_path), skipped, failed,
            )
        elif sol_path:
            logger.debug(
                "[ref-raster %s] soil_profiles_hash unchanged, skipping seed",
                name,
            )

        # Step 2: Reload the raster into PostGIS if the TIFF changed.
        if tiff_hash != prior_tiff_hash:
            ingest_cfg = entry.get('ingest') or {}
            tile_size = ingest_cfg.get('tile_size', '10x10')
            size_mb = os.path.getsize(tiff_path) / (1024 * 1024)
            logger.info(
                "[ref-raster %s] Loading %.1f MB into PostGIS via raster2pgsql "
                "(tile_size=%s) — this can take several minutes for large rasters…",
                name, size_mb, tile_size,
            )
            _load_raster_to_postgis(tiff_path, table_name, tile_size=tile_size)
            logger.info(
                "[ref-raster %s] Loaded %s into %s (tiff_hash=%s)",
                name, os.path.basename(tiff_path), table_name, tiff_hash[:12],
            )
        else:
            logger.debug("[ref-raster %s] tiff_hash unchanged, skipping reload", name)

        # Step 3: Reload the legend if its hash changed (categorical rasters only).
        # Categorical legends are owned by data_agent (CategoricalRasterLegend),
        # so reload through data_agent's loader.
        legend_loaded = False
        if legend_path and legend_hash and legend_hash != prior_legend_hash:
            from data_agent.services import load_raster_legend
            count = load_raster_legend(name, legend_path, legend_cfg)
            # Also load into the legacy dssat_agent table for backward compat
            _load_soil_legend(name, legend_path, legend_cfg)
            legend_loaded = True
            logger.info(
                "[ref-raster %s] Loaded legend from %s (%d entries, legend_hash=%s)",
                name, os.path.basename(legend_path), count, legend_hash[:12],
            )
        elif legend_path:
            logger.debug(
                "[ref-raster %s] legend_hash unchanged, skipping legend reload",
                name,
            )

        # Step 4: Cross-reference verification (orphan check) — runs whenever
        # the legend was just loaded OR when the soil profiles were just
        # re-seeded, to catch any drift between the two.
        if legend_path and (
            legend_loaded or (sol_path and sol_hash != prior_sol_hash)
        ):
            _verify_legend_cross_reference(name)

        # Step 5: Persist all metadata into dataset_information.
        new_info = dict(existing_info)
        new_info.update({
            'tiff_hash': tiff_hash,
            'legend_hash': legend_hash,
            'soil_profiles_hash': sol_hash,
            'temporal_resolution': entry.get('temporal_resolution', 'static'),
            'domain': entry.get('domain'),
            'purpose': entry.get('purpose'),
            'value_type': entry.get('value_type'),
            'band_meanings': entry.get('band_meanings'),
            'variables': entry.get('variables'),
            'no_data_value': entry.get('no_data_value'),
            'coverage_extent': entry.get('coverage_extent'),
            'subroutines': {
                'reference_lookup': {'table_name': table_name},
            },
            'units': entry.get('units'),
        })
        # Drop None-valued top-level keys to keep the JSON tidy.
        new_info = {k: v for k, v in new_info.items() if v is not None}

        ds.dataset_information = new_info
        ds.dataset_type = entry.get('dataset_type', 'static')
        if entry.get('spatial_resolution'):
            ds.tds_spatial_resolution = entry['spatial_resolution']
        ds.save(update_fields=[
            'dataset_information', 'dataset_type', 'tds_spatial_resolution',
            'dataset_name',
        ])

    invalidate_prefix_cache()


# ---------------------------------------------------------------------------
# Soil profile seeding (delegates to existing seed_soils helpers)
# ---------------------------------------------------------------------------

def _seed_soil_profiles(sol_path: str) -> Tuple[int, int, int]:
    """
    Parse a DSSAT .SOL file and create matching StoredSoilProfile rows.

    Skip-existing semantics: if a soil_id already exists, leave it alone.
    Returns (created, skipped, failed) counts.

    Reuses parse_sol_file and _create_profile_from_parsed from the existing
    seed_soils management command so we don't duplicate the parser.
    """
    from dssat_agent.management.commands.seed_soils import (
        parse_sol_file,
        _create_profile_from_parsed,
    )
    from dssat_agent.models import StoredSoilProfile

    profiles = parse_sol_file(sol_path)
    created = skipped = failed = 0
    for p in profiles:
        soil_id = p.get('soil_id')
        if not soil_id:
            failed += 1
            continue
        if StoredSoilProfile.objects.filter(soil_id=soil_id).exists():
            skipped += 1
            continue
        try:
            _create_profile_from_parsed(p)
            created += 1
        except Exception as e:
            logger.warning("Failed to seed soil %s: %s", soil_id, e)
            failed += 1
    return created, skipped, failed


# ---------------------------------------------------------------------------
# Raster loading (delegates to raster2pgsql)
# ---------------------------------------------------------------------------

def _load_raster_to_postgis(tiff_path: str, table_name: str,
                            tile_size: str = '10x10') -> None:
    """
    Run ``raster2pgsql -d`` to drop and re-create a PostGIS table from a TIFF.

    Uses the same shell-out pattern as the weather ETL pipeline at
    data_agent/etl/etl_pipeline.py:2130-2141. The ``-d`` flag drops any
    existing table before loading; this is correct for our hash-changed
    re-load semantics (we only call this when the file has actually changed).

    ``tile_size`` controls the raster2pgsql ``-t WIDTHxHEIGHT`` argument.
    Default 10×10 matches the small categorical lookup rasters (5 km
    resolution → 10×10 = 50 km tiles, manageable row counts). Continuous
    high-resolution rasters (e.g. 90 m SRTM) MUST override this — at
    10×10 a 10°×10° SE-US clip produces ~1.2M rows, and any ST_Union over
    the table from a tile renderer or zonal-stats query OOM-kills Postgres.
    256×256 keeps the row count to ~2k for the same clip.
    """
    from django.conf import settings

    db = settings.DATABASES['default']
    host = db.get('HOST', 'localhost')
    port = db.get('PORT', '5432')
    user = db.get('USER', 'postgres')
    password = db.get('PASSWORD', '')
    dbname = db.get('NAME', 'dssatserv')

    # `set -o pipefail` is critical: without it, a missing raster2pgsql
    # binary (rc=127) is masked by psql's success on empty input (rc=0),
    # producing a false "load succeeded" that gets cached as a hash and
    # blocks all future retries until the cached hash is manually cleared.
    cmd = (
        f'set -o pipefail; '
        f'raster2pgsql -d -s 4326 -t {tile_size} -I -F '
        f'{tiff_path} {table_name}'
        f' | psql -h {host} -p {port} -U {user} -d {dbname}'
    )
    env = os.environ.copy()
    env['PGPASSWORD'] = password

    proc = subprocess.run(
        cmd, shell=True, executable='/bin/bash',
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env=env,
        check=False,
    )
    if proc.returncode != 0:
        output = proc.stdout.decode('utf-8', errors='replace') if proc.stdout else ''
        raise RuntimeError(
            f"raster2pgsql failed (rc={proc.returncode}): {output[:500]}"
        )


# ---------------------------------------------------------------------------
# Legend loader
# ---------------------------------------------------------------------------

def _load_soil_legend(raster_name: str, legend_path: str, legend_cfg: Dict) -> None:
    """
    Bulk-replace SoilTypeRasterLegend rows for a raster from a CSV legend.

    Future enhancement: detect a sidecar GDAL Raster Attribute Table
    (.aux.xml with `<GDALRasterAttributeTable>`) and prefer that over the
    CSV. Not implemented today; the stakeholder's current pipeline ships
    the CSV.
    """
    from dssat_agent.models import SoilTypeRasterLegend

    legend_type = (legend_cfg.get('type') or 'csv').lower()
    if legend_type != 'csv':
        logger.warning(
            "Legend type '%s' not yet supported (only 'csv'). Skipping for %s.",
            legend_type, raster_name,
        )
        return

    value_col = legend_cfg.get('value_column', 'band_value')
    key_col = legend_cfg.get('key_column', 'soil_code')
    label_col = legend_cfg.get('label_column', 'description')

    SoilTypeRasterLegend.objects.filter(raster_dataset_name=raster_name).delete()

    rows: List[SoilTypeRasterLegend] = []
    with open(legend_path, 'r', newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                band_value = int(row[value_col])
            except (KeyError, ValueError, TypeError):
                continue
            soil_code = (row.get(key_col) or '').strip()
            description = (row.get(label_col) or '').strip()
            if not soil_code or soil_code.upper() == 'NODATA':
                continue
            rows.append(SoilTypeRasterLegend(
                raster_dataset_name=raster_name,
                band_value=band_value,
                soil_code=soil_code[:10],
                description=description,
            ))

    if rows:
        SoilTypeRasterLegend.objects.bulk_create(rows, batch_size=2000)


def _verify_legend_cross_reference(raster_name: str) -> None:
    """
    Log how many legend entries point to soil_codes that aren't in
    StoredSoilProfile. A non-zero orphan count usually means the legend
    and the associated SOIL.SOL have drifted out of sync, or seeding
    partially failed.
    """
    from dssat_agent.models import SoilTypeRasterLegend, StoredSoilProfile

    legend_codes = set(
        SoilTypeRasterLegend.objects
        .filter(raster_dataset_name=raster_name)
        .values_list('soil_code', flat=True)
    )
    if not legend_codes:
        return
    existing = set(
        StoredSoilProfile.objects
        .filter(soil_id__in=legend_codes)
        .values_list('soil_id', flat=True)
    )
    orphans = legend_codes - existing
    if orphans:
        sample = sorted(orphans)[:5]
        logger.warning(
            "[ref-raster %s] Legend has %d soil_codes with no matching "
            "StoredSoilProfile (out of %d total). Sample: %s",
            raster_name, len(orphans), len(legend_codes), sample,
        )
    else:
        logger.info(
            "[ref-raster %s] Legend cross-reference OK: all %d soil_codes "
            "resolve to StoredSoilProfile rows.",
            raster_name, len(legend_codes),
        )


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def _sha256(path: Optional[str]) -> Optional[str]:
    """Compute the SHA-256 of a file. Returns None if path is None or missing."""
    if not path or not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Auto-download (first-boot bootstrap for missing reference rasters)
# ---------------------------------------------------------------------------

def _try_auto_download(entry: Dict, target_path: str) -> bool:
    """Attempt to materialise ``target_path`` from the entry's auto_download
    block. Returns True on success, False otherwise (the caller falls back
    to the existing "missing file → skip" warning).

    Each provider mosaics + clips into a single tif at ``target_path`` and
    cleans up its intermediate per-tile downloads, so the on-disk footprint
    of a successful run is just the clipped result.
    """
    cfg = entry.get('auto_download') or {}
    provider = cfg.get('provider')
    if not provider:
        return False

    coverage = entry.get('coverage_extent') or {}
    if coverage.get('type') != 'bbox':
        logger.warning(
            "auto_download requested for %s but coverage_extent is not bbox; "
            "cannot determine clip area",
            entry.get('name'),
        )
        return False
    bbox = (
        float(coverage['min_lon']),
        float(coverage['min_lat']),
        float(coverage['max_lon']),
        float(coverage['max_lat']),
    )

    if provider == 'cgiar_csi_srtm_v4_1':
        target_res = cfg.get('target_resolution_deg')
        try:
            return _download_cgiar_srtm(
                target_path, bbox,
                target_resolution_deg=target_res,
            )
        except Exception as e:
            logger.warning(
                "CGIAR SRTM auto-download failed for %s: %s",
                entry.get('name'), e, exc_info=True,
            )
            return False

    logger.warning(
        "auto_download provider %r is not supported (entry=%s)",
        provider, entry.get('name'),
    )
    return False


# CGIAR-CSI hosts SRTM 90m as 5°×5° GeoTIFF tiles at this URL pattern:
#
#   https://srtm.csi.cgiar.org/wp-content/uploads/files/srtm_5x5/TIFF/srtm_<col>_<row>.zip
#
# Tiles are indexed from 1, with column 1 starting at -180° lon (west) and
# row 1 starting at +60° lat (north). 5° per step in either direction.
_CGIAR_BASE_URL = (
    'https://srtm.csi.cgiar.org/wp-content/uploads/files/srtm_5x5/TIFF/'
)


def _cgiar_tile_indices(bbox: Tuple[float, float, float, float]) -> List[Tuple[int, int]]:
    """List of (col, row) CGIAR tile indices that intersect ``bbox``.

    bbox = (min_lon, min_lat, max_lon, max_lat).
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    col_w = math.floor((min_lon + 180) / 5) + 1
    col_e = math.floor((max_lon + 180) / 5) + 1
    # Latitude indices count downward — row 1 is north.
    row_n = math.floor((60 - max_lat) / 5) + 1
    row_s = math.floor((60 - min_lat) / 5) + 1
    return [
        (c, r)
        for c in range(col_w, col_e + 1)
        for r in range(row_n, row_s + 1)
    ]


def _download_cgiar_srtm(target_path: str,
                         bbox: Tuple[float, float, float, float],
                         target_resolution_deg: Optional[float] = None) -> bool:
    """Download the CGIAR-CSI SRTM 5×5 tiles covering ``bbox``, mosaic +
    clip them with gdalwarp, write the result to ``target_path``, and
    discard the per-tile downloads.

    ``target_resolution_deg`` (when supplied) resamples to that pixel size
    via gdalwarp's ``-tr`` flag. SRTM source is 90 m (~0.00083°); for
    crop-modeling elevation we typically resample to ~1 km (0.01°) to
    avoid materialising a 90 m raster at all.

    Returns True on success. Tiles that 404 (typically all-water cells)
    are skipped with a debug log; a successful run requires at least one
    tile to land.
    """
    tiles = _cgiar_tile_indices(bbox)
    if not tiles:
        logger.warning("CGIAR auto-download: bbox %s yielded no tiles", bbox)
        return False

    logger.info(
        "CGIAR auto-download: fetching %d SRTM 5x5 tiles for bbox %s",
        len(tiles), bbox,
    )
    target_dir = os.path.dirname(target_path)
    os.makedirs(target_dir, exist_ok=True)

    # Use a tempdir under target_dir's parent so the cleanup happens
    # even if the process is killed mid-download (most container restarts
    # don't survive a crash, but it costs nothing to put the temp work
    # somewhere predictable).
    with tempfile.TemporaryDirectory(prefix='srtm_download_') as tmpdir:
        tile_tifs: List[str] = []
        for col, row in tiles:
            tile_name = f'srtm_{col:02d}_{row:02d}'
            url = f'{_CGIAR_BASE_URL}{tile_name}.zip'
            zip_path = os.path.join(tmpdir, f'{tile_name}.zip')
            try:
                _http_download(url, zip_path)
            except urllib.error.HTTPError as e:
                if e.code in (403, 404):
                    # Many ocean / no-coverage tiles legitimately return 404.
                    logger.debug(
                        "CGIAR tile %s: HTTP %d (likely ocean / no coverage)",
                        tile_name, e.code,
                    )
                    continue
                raise
            try:
                with zipfile.ZipFile(zip_path) as zf:
                    members = [m for m in zf.namelist()
                               if m.lower().endswith('.tif')]
                    if not members:
                        logger.warning("CGIAR tile %s: no .tif in zip", tile_name)
                        continue
                    zf.extract(members[0], tmpdir)
                    tile_tifs.append(os.path.join(tmpdir, members[0]))
            finally:
                # Drop the zip immediately — we only need the .tif inside.
                try: os.remove(zip_path)
                except OSError: pass

        if not tile_tifs:
            logger.warning(
                "CGIAR auto-download: no usable tiles fetched for bbox %s",
                bbox,
            )
            return False

        # gdalwarp can mosaic + clip + reproject in one shot. -te is the
        # target extent (clip box), -of GTiff forces the output format,
        # and the -co flags emit a tiled DEFLATE-compressed GeoTIFF —
        # without compression the SE-US Int16 clip is ~290 MB; with it
        # we get ~50-70 MB at no read-time cost (raster2pgsql + GDAL both
        # decompress on the fly).
        min_lon, min_lat, max_lon, max_lat = bbox
        cmd = [
            'gdalwarp',
            '-overwrite',
            '-of', 'GTiff',
            '-co', 'COMPRESS=DEFLATE',
            '-co', 'PREDICTOR=2',
            '-co', 'TILED=YES',
            '-co', 'BLOCKXSIZE=256',
            '-co', 'BLOCKYSIZE=256',
            '-t_srs', 'EPSG:4326',
            '-te', str(min_lon), str(min_lat), str(max_lon), str(max_lat),
        ]
        if target_resolution_deg:
            # ``-r average`` is the correct choice for downsampling
            # continuous data: each output pixel becomes the arithmetic
            # mean of every source pixel that falls inside it (equivalent
            # to a box low-pass before resample, which is what you need
            # to avoid aliasing). Bilinear only samples 4 source pixels
            # regardless of the downsample factor — fine for upsampling
            # or mild downsampling, wrong here where 90 m → 1 km is a
            # 12× downsample (~144 source pixels per output pixel).
            cmd += [
                '-tr', str(target_resolution_deg), str(target_resolution_deg),
                '-r', 'average',
            ]
        cmd += [*tile_tifs, target_path]
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
        )
        if proc.returncode != 0:
            output = proc.stdout.decode('utf-8', errors='replace') if proc.stdout else ''
            logger.warning(
                "gdalwarp failed (rc=%d) during SRTM auto-download: %s",
                proc.returncode, output[:500],
            )
            # Make sure we don't leave a half-written target around.
            if os.path.isfile(target_path):
                try: os.remove(target_path)
                except OSError: pass
            return False

    logger.info(
        "CGIAR auto-download complete: %s (%.1f MB)",
        target_path, os.path.getsize(target_path) / (1024 * 1024),
    )
    return True


def _http_download(url: str, dest_path: str, *, timeout: int = 120) -> None:
    """Download ``url`` to ``dest_path`` with a sensible UA so CGIAR's
    edge doesn't 403 us. Streams in 64 KB chunks to keep memory bounded.
    """
    req = urllib.request.Request(
        url,
        headers={
            'User-Agent': 'EarthRISEAgents-dssat/1.0 reference-raster-loader',
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp, \
            open(dest_path, 'wb') as out:
        shutil.copyfileobj(resp, out, length=65536)
