"""
Management command to check available data dates and spatial extent
for weather sources in PostGIS.

Usage:
    python manage.py check_dates power
    python manage.py check_dates power --variable tmax
    python manage.py check_dates power --start 2023-01-01 --end 2023-12-31
    python manage.py check_dates --all
"""

import datetime

from django.conf import settings
from django.core.management.base import BaseCommand

from data_agent.etl.etl_pipeline import ETL_Pipeline

SCHEMA = getattr(settings, 'DATAAGENT_SCHEMA', 'dataagent')

# Source name → PostGIS table prefix, derived from the RasterDataset
# table at command run-time. Product-agnostic — whichever sources the
# product has registered via ``register_raster_source`` are the sources
# this command works over. Not a module-level constant so the DB doesn't
# get hit at import time.
def _prefix_map():
    from data_agent.services import _get_prefix_map
    return _get_prefix_map()

VARIABLES = ['tmax', 'tmin', 'rain', 'srad']


class Command(BaseCommand):
    help = 'Check available data dates and spatial extent for weather sources in PostGIS'

    def add_arguments(self, parser):
        parser.add_argument(
            'source', nargs='?', default=None,
            help='Source prefix (e.g. power, era5, agera5). Omit with --all.',
        )
        parser.add_argument(
            '--variable', type=str, default=None,
            choices=VARIABLES,
            help='Check a single variable instead of all four.',
        )
        parser.add_argument(
            '--start', type=str, default=None,
            help='Start date filter (YYYY-MM-DD).',
        )
        parser.add_argument(
            '--end', type=str, default=None,
            help='End date filter (YYYY-MM-DD).',
        )
        parser.add_argument(
            '--all', action='store_true',
            help='Check all sources.',
        )
        parser.add_argument(
            '--schema', type=str, default=SCHEMA,
            help=f'Database schema (default: {SCHEMA}).',
        )

    def _query_extent(self, cur, schema, table):
        """Query spatial extent and resolution for a raster table."""
        try:
            cur.execute(f"""
                SELECT
                    ST_XMin(ext) AS west,
                    ST_YMin(ext) AS south,
                    ST_XMax(ext) AS east,
                    ST_YMax(ext) AS north,
                    pw AS pixel_width,
                    ph AS pixel_height
                FROM (
                    SELECT
                        ST_Extent(ST_Envelope(rast)) AS ext,
                        AVG(ST_PixelWidth(rast)) AS pw,
                        AVG(ABS(ST_PixelHeight(rast))) AS ph
                    FROM {schema}.{table}
                    LIMIT 5000
                ) sub
            """)
            row = cur.fetchone()
            if row and row[0] is not None:
                return {
                    'west': round(row[0], 4),
                    'south': round(row[1], 4),
                    'east': round(row[2], 4),
                    'north': round(row[3], 4),
                    'pixel_width': round(row[4], 4) if row[4] else None,
                    'pixel_height': round(row[5], 4) if row[5] else None,
                }
        except Exception:
            cur.connection.rollback()
        return None

    def _query_tile_count(self, cur, schema, table):
        """Query the number of raster tiles in a table."""
        try:
            cur.execute(f"SELECT COUNT(*) FROM {schema}.{table}")
            row = cur.fetchone()
            return row[0] if row else 0
        except Exception:
            cur.connection.rollback()
            return 0

    def handle(self, *args, **options):
        source = options['source']
        check_all = options['all']
        schema = options['schema']
        variable = options['variable']
        start_date = options['start']
        end_date = options['end']

        if start_date:
            start_date = datetime.date.fromisoformat(start_date)
        if end_date:
            end_date = datetime.date.fromisoformat(end_date)

        prefix_map = _prefix_map()

        if check_all:
            sources = list(prefix_map.keys())
        elif source:
            if source not in prefix_map:
                self.stderr.write(
                    f"Unknown source: {source}\n"
                    f"Available: {', '.join(sorted(prefix_map.keys()))}"
                )
                return
            sources = [source]
        else:
            self.stderr.write(
                "Provide a source name or use --all.\n"
                f"Available: {', '.join(sorted(prefix_map.keys()))}"
            )
            return

        variables = [variable] if variable else VARIABLES

        pipeline = ETL_Pipeline(None, None, None, None, None)
        con = pipeline._get_postgis_connection()

        try:
            cur = con.cursor()
            for src in sources:
                prefix = prefix_map[src]
                self.stdout.write(self.style.SUCCESS(
                    f"\n{'=' * 60}"
                    f"\n  {src} (prefix: {prefix})"
                    f"\n{'=' * 60}"
                ))

                for var in variables:
                    table = f"{prefix}_{var}"

                    # Date availability
                    try:
                        dates = pipeline.check_availability(
                            con, schema, prefix, var, start_date, end_date,
                        )
                    except Exception:
                        con.rollback()
                        self.stdout.write(f"\n  {var.upper()}: (table {schema}.{table} not found)")
                        continue

                    self.stdout.write(f"\n  {var.upper()} ({schema}.{table})")

                    if dates:
                        self.stdout.write(
                            f"    Dates : {len(dates):,} days "
                            f"({dates[0]} to {dates[-1]})"
                        )
                    else:
                        self.stdout.write(f"    Dates : 0 days")
                        continue

                    # Spatial extent
                    extent = self._query_extent(cur, schema, table)
                    if extent:
                        self.stdout.write(
                            f"    Extent: "
                            f"W {extent['west']}, S {extent['south']}, "
                            f"E {extent['east']}, N {extent['north']}"
                        )
                        if extent['pixel_width'] and extent['pixel_height']:
                            self.stdout.write(
                                f"    Res   : "
                                f"{extent['pixel_width']}\u00b0 x "
                                f"{extent['pixel_height']}\u00b0"
                            )

                    # Tile count
                    count = self._query_tile_count(cur, schema, table)
                    if count:
                        self.stdout.write(f"    Tiles : {count:,}")

            self.stdout.write('')
        finally:
            con.close()
