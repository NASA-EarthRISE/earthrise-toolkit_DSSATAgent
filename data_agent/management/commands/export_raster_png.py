"""
Export a PostGIS raster as a PNG file for visual debugging.

Renders the full extent of a weather variable for a given date using
ST_ColorMap + ST_AsPNG — the same pipeline as the tile endpoint but
without any tiling, resampling, or clipping.

Usage:
    python manage.py export_raster_png power tmin 2024-06-06
    python manage.py export_raster_png power tmin 2024-06-06 --output /tmp/test.png
    python manage.py export_raster_png power tmin 2024-06-06 --ramp pseudocolor
"""

import psycopg2
from django.conf import settings
from django.core.management.base import BaseCommand

SCHEMA = getattr(settings, 'DATAAGENT_SCHEMA', 'dataagent')

VARIABLES = ['tmax', 'tmin', 'rain', 'srad']

COLOR_RAMPS = {
    'tmax': '\n'.join([
        'nv 0 0 0 0',
        '-10 50 100 200 255',
        '0 100 200 255 255',
        '15 255 255 100 255',
        '30 255 100 0 255',
        '45 180 0 0 255',
    ]),
    'tmin': '\n'.join([
        'nv 0 0 0 0',
        '-20 50 100 200 255',
        '-5 100 200 255 255',
        '10 255 255 100 255',
        '20 255 100 0 255',
        '35 180 0 0 255',
    ]),
    'rain': '\n'.join([
        'nv 0 0 0 0',
        '0 240 240 240 255',
        '0.1 255 255 204 255',
        '5 161 218 180 255',
        '10 65 182 196 255',
        '25 44 127 184 255',
        '50 37 52 148 255',
    ]),
    'srad': '\n'.join([
        'nv 0 0 0 0',
        '0 255 255 220 255',
        '5 255 255 150 255',
        '15 255 255 0 255',
        '25 255 150 0 255',
        '35 200 0 0 255',
    ]),
}


class Command(BaseCommand):
    help = 'Export a PostGIS raster as a reference PNG for visual debugging'

    def add_arguments(self, parser):
        parser.add_argument('source', type=str, help='Source prefix (e.g. power, era5)')
        parser.add_argument('variable', type=str, choices=VARIABLES, help='Variable name')
        parser.add_argument('date', type=str, help='Date (YYYY-MM-DD)')
        parser.add_argument(
            '--output', type=str, default=None,
            help='Output file path (default: {source}_{variable}_{date}.png)',
        )
        parser.add_argument(
            '--ramp', type=str, default=None,
            help='Color ramp: "pseudocolor", "fire", "bluered", or use variable default',
        )
        parser.add_argument(
            '--schema', type=str, default=SCHEMA,
            help=f'Database schema (default: {SCHEMA})',
        )

    def _get_connection(self):
        from earthrise_agents_base.db import get_raw_connection
        return get_raw_connection()

    def handle(self, *args, **options):
        source = options['source']
        variable = options['variable']
        date = options['date']
        schema = options['schema']
        output = options['output'] or f'{source}_{variable}_{date}.png'
        ramp = options['ramp'] or COLOR_RAMPS.get(variable, 'pseudocolor')

        table = f"{schema}.{source}_{variable}"

        con = self._get_connection()
        try:
            cur = con.cursor()

            # Stats
            cur.execute(f"""
                SELECT
                    COUNT(*),
                    (ST_SummaryStatsAgg(rast, 1, TRUE)).min,
                    (ST_SummaryStatsAgg(rast, 1, TRUE)).max,
                    (ST_SummaryStatsAgg(rast, 1, TRUE)).mean,
                    (ST_SummaryStatsAgg(rast, 1, TRUE)).count
                FROM {table}
                WHERE fdate = %s
            """, (date,))
            row = cur.fetchone()
            if not row or row[0] == 0:
                self.stderr.write(f"No data found in {table} for {date}")
                return

            tile_count, vmin, vmax, vmean, pixel_count = row
            self.stdout.write(
                f"Source: {table}  Date: {date}\n"
                f"  Raster tiles: {tile_count}\n"
                f"  Pixel stats: min={vmin:.2f}  max={vmax:.2f}  "
                f"mean={vmean:.2f}  count={pixel_count}"
            )

            # Extent
            cur.execute(f"""
                SELECT ST_XMin(ext), ST_YMin(ext), ST_XMax(ext), ST_YMax(ext),
                       ST_Width(rast), ST_Height(rast)
                FROM (
                    SELECT ST_Extent(ST_Envelope(rast)) AS ext, rast
                    FROM (
                        SELECT ST_Union(rast) AS rast
                        FROM {table} WHERE fdate = %s
                    ) u
                ) sub
            """, (date,))
            ext = cur.fetchone()
            if ext:
                self.stdout.write(
                    f"  Extent: W={ext[0]:.4f} S={ext[1]:.4f} "
                    f"E={ext[2]:.4f} N={ext[3]:.4f}\n"
                    f"  Union size: {ext[4]}x{ext[5]} pixels"
                )

            # Export PNG — union all tiles for this date, apply colormap
            self.stdout.write(f"  Color ramp: {ramp[:60]}...")
            cur.execute(f"""
                WITH unioned AS (
                    SELECT ST_Union(rast) AS rast
                    FROM {table}
                    WHERE fdate = %s
                ),
                colored AS (
                    SELECT ST_ColorMap(rast, 1, %s, 'INTERPOLATE') AS rast
                    FROM unioned
                    WHERE rast IS NOT NULL
                )
                SELECT ST_AsPNG(rast, ARRAY[1,2,3,4])
                FROM colored
                WHERE rast IS NOT NULL
            """, (date, ramp))
            png_row = cur.fetchone()

            if not png_row or not png_row[0]:
                self.stderr.write("ST_ColorMap/ST_AsPNG returned NULL")
                return

            png_bytes = bytes(png_row[0])
            with open(output, 'wb') as f:
                f.write(png_bytes)

            self.stdout.write(self.style.SUCCESS(
                f"\nExported {len(png_bytes):,} bytes to {output}"
            ))

        finally:
            con.close()
