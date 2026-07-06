"""
Enqueue backfill fetch jobs for every registered time-series raster source.

Intended to run on container startup so that a baseline window (by default
January 1 2024 through today) is populated for all time-series datasets
without any manual intervention. Jobs are dispatched via Celery
(fetch_data_task) so this command returns quickly; the workers do the
actual downloading in the background.

The ETL pipeline's existing date-dedup logic (check_availability) keeps
re-runs cheap — only missing dates are fetched.

Env vars:
    STARTUP_FETCH_BBOX   "west,south,east,north" floats. Overrides
                         settings.DATA_AGENT_DEFAULT_BBOX.
    STARTUP_FETCH_START  ISO date; overrides the 2024-01-01 default.
    STARTUP_FETCH_END    ISO date; overrides today's date.

Per-source overrides (sources.yaml):
    available_from / available_to    Sources whose archive window doesn't
                                     overlap [start, end] are skipped.
    backfill_start / backfill_end    Force a specific window for just this
                                     source (useful to test one source
                                     against dates unaffected by a remote
                                     rate-limit).
"""

import datetime
import logging
import os

from django.conf import settings
from django.core.management.base import BaseCommand

from data_agent import services as data_svc
from data_agent.models import RasterDataset

logger = logging.getLogger(__name__)

DEFAULT_START = '2024-01-01'

# The default backfill region is a domain/deployment concern, not a data_agent
# concern — it is read from settings.DATA_AGENT_DEFAULT_BBOX (set in the domain
# root project). If unset and no --bbox / STARTUP_FETCH_BBOX is given, the
# backfill is skipped rather than defaulting to a hardcoded region.


def _parse_bbox(raw):
    if not raw:
        return None
    parts = [p.strip() for p in raw.split(',')]
    if len(parts) != 4:
        return None
    try:
        w, s, e, n = (float(p) for p in parts)
    except ValueError:
        return None
    return {'west': w, 'south': s, 'east': e, 'north': n}


def _is_window_already_loaded(source, start_iso, end_iso):
    """Return True if PostGIS already has data covering [start_iso, end_iso].

    Uses data_agent.services.check_availability(), which returns per-variable
    {'count', 'first_date', 'last_date'}. We treat the window as fully
    covered if at least one variable has non-zero rows AND its date range
    spans the request. This matches the dedup semantics of the underlying
    ETL (variables for a source are always loaded together) without doing
    a per-day gap scan, which is overkill for the startup short-circuit.
    """
    try:
        result = data_svc.check_availability(
            source=source, start_date=start_iso, end_date=end_iso,
        )
    except Exception:
        logger.exception('check_availability failed for %s; will queue fetch', source)
        return False

    if not isinstance(result, dict) or 'availability' not in result:
        return False

    try:
        start = datetime.date.fromisoformat(start_iso)
        end = datetime.date.fromisoformat(end_iso)
    except ValueError:
        return False

    for info in result['availability'].values():
        count = info.get('count') or 0
        first = info.get('first_date')
        last = info.get('last_date')
        if not count or not first or not last:
            continue
        try:
            first_d = datetime.date.fromisoformat(first)
            last_d = datetime.date.fromisoformat(last)
        except ValueError:
            continue
        if first_d <= start and last_d >= end:
            return True
    return False


def _filter_by_availability(datasets, start_iso, end_iso):
    """Drop datasets whose available_from/available_to don't overlap [start,end].

    Availability is optional — sources without it are treated as always
    available. Returns (kept, skipped) where skipped is a list of
    (dataset_subtype, reason) pairs.
    """
    try:
        start = datetime.date.fromisoformat(start_iso)
        end = datetime.date.fromisoformat(end_iso)
    except ValueError:
        return datasets, []

    kept, skipped = [], []
    for ds in datasets:
        info = ds.dataset_information or {}
        a_from = info.get('available_from')
        a_to = info.get('available_to')
        try:
            a_from = datetime.date.fromisoformat(a_from) if a_from else None
            a_to = datetime.date.fromisoformat(a_to) if a_to else None
        except ValueError:
            a_from = a_to = None

        if a_to and a_to < start:
            skipped.append((ds.dataset_subtype,
                            f'archive ends {a_to} before requested start {start}'))
            continue
        if a_from and a_from > end:
            skipped.append((ds.dataset_subtype,
                            f'archive starts {a_from} after requested end {end}'))
            continue
        kept.append(ds)
    return kept, skipped


class Command(BaseCommand):
    help = (
        'Enqueue backfill fetch jobs for every time-series raster source, '
        'from --start (default 2024-01-01) through --end (default today). '
        'Bbox comes from --bbox or the STARTUP_FETCH_BBOX env var '
        '("west,south,east,north"). Exits as soon as jobs are queued — '
        'downloads run asynchronously via Celery workers.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--start', default=None)
        parser.add_argument('--end', default=None)
        parser.add_argument('--bbox', default=None)
        parser.add_argument(
            '--sync', action='store_true',
            help='Run fetches synchronously (blocks). Default is async via Celery.',
        )

    def handle(self, *args, **opts):
        bbox_raw = opts['bbox'] or os.environ.get('STARTUP_FETCH_BBOX')
        default_bbox = getattr(settings, 'DATA_AGENT_DEFAULT_BBOX', None)
        bbox = _parse_bbox(bbox_raw) if bbox_raw else default_bbox
        if not bbox:
            msg = (
                'No backfill region: --bbox/STARTUP_FETCH_BBOX not given (or '
                'malformed, expected "west,south,east,north" floats) and '
                'settings.DATA_AGENT_DEFAULT_BBOX is unset. Skipping time-series backfill.'
            )
            self.stdout.write(self.style.WARNING(msg))
            logger.warning(msg)
            return

        start_date = opts['start'] or os.environ.get('STARTUP_FETCH_START') or DEFAULT_START
        end_date = opts['end'] or os.environ.get('STARTUP_FETCH_END') or \
            datetime.date.today().isoformat()

        # Only observational sources make sense for a historical backfill.
        # Forecast-only endpoints (e.g. Open-Meteo ensemble, SEAS5, NMME, CFSv2)
        # reject dates outside their forecast window; skip them here.
        datasets = list(
            RasterDataset.objects
            .filter(dataset_type='time_series')
            .filter(data_category__in=['observational', 'observational_and_forecast'])
        )
        if not datasets:
            self.stdout.write(self.style.WARNING(
                'No observational time-series raster datasets registered; '
                'nothing to fetch.'
            ))
            return

        # Drop sources whose availability window doesn't overlap the requested
        # range (e.g. CHIRTS v1.0 ends 2016-12-31 — no point asking for 2024).
        in_range, skipped = _filter_by_availability(datasets, start_date, end_date)
        for source, reason in skipped:
            self.stdout.write(self.style.WARNING(
                f'  {source}: skipped ({reason})'
            ))
        datasets = in_range
        if not datasets:
            self.stdout.write(self.style.WARNING(
                'All time-series sources are out-of-range for the requested '
                'window; nothing to fetch.'
            ))
            return

        self.stdout.write(self.style.SUCCESS(
            f'Backfilling {len(datasets)} time-series sources '
            f'{start_date} → {end_date} '
            f'bbox=({bbox["west"]}, {bbox["south"]}, {bbox["east"]}, {bbox["north"]})'
        ))

        fetch = data_svc.fetch_data_sync if opts['sync'] else data_svc.fetch_data
        queued = 0
        skipped_loaded = 0
        for ds in datasets:
            source = ds.dataset_subtype
            info = ds.dataset_information or {}
            # Per-source override (e.g. PRISM test dates to avoid NACSE
            # rate-limits on previously-requested days).
            src_start = info.get('backfill_start') or start_date
            src_end = info.get('backfill_end') or end_date
            if (src_start, src_end) != (start_date, end_date):
                self.stdout.write(self.style.WARNING(
                    f'  {source}: using per-source override window '
                    f'{src_start} → {src_end}'
                ))

            # Skip if PostGIS already has data covering this window. The
            # underlying ETL would dedup anyway, but short-circuiting here
            # avoids the celery round-trip and the per-day "already loaded"
            # log spam — important on container startup where this command
            # blocks the entrypoint before uvicorn starts.
            if _is_window_already_loaded(source, src_start, src_end):
                self.stdout.write(self.style.SUCCESS(
                    f'  {source}: already covers {src_start} → {src_end}, skipping'
                ))
                skipped_loaded += 1
                continue

            try:
                result = fetch(
                    source=source,
                    bbox=bbox,
                    start_date=src_start,
                    end_date=src_end,
                )
            except Exception as e:
                logger.exception('Failed to queue backfill for %s', source)
                self.stdout.write(self.style.ERROR(f'  {source}: {e}'))
                continue

            if isinstance(result, dict) and result.get('error'):
                self.stdout.write(self.style.ERROR(
                    f'  {source}: {result["error"]}'
                ))
            else:
                queued += 1
                job_id = result.get('job_id', '?') if isinstance(result, dict) else '?'
                self.stdout.write(self.style.SUCCESS(
                    f'  {source}: queued (job={job_id})'
                ))

        self.stdout.write(self.style.SUCCESS(
            f'Queued {queued}/{len(datasets)} time-series sources '
            f'({skipped_loaded} already covered).'
        ))
