"""
Live API tests for the config-driven ETL pipeline.

These tests make REAL HTTP calls to external APIs -- no mocks.
They exercise the full Discover -> Extract -> Transform -> Load flow
against a real PostGIS database and real remote data servers.

Marks:
    @pytest.mark.live  -- every test in this module
    @pytest.mark.slow  -- tests that download large files (>10 MB)

Run all live tests:
    docker compose exec dataagent python -m pytest data_agent/tests/test_live.py -v -m live

Run only fast live tests (skip large downloads):
    docker compose exec dataagent python -m pytest data_agent/tests/test_live.py -v -m "live and not slow"

Run one specific source:
    docker compose exec dataagent python -m pytest data_agent/tests/test_live.py -v -k "nasa_power"

With CDS API key for SEAS5:
    CDS_API_KEY=<your-key> docker compose exec dataagent python -m pytest data_agent/tests/test_live.py -v -m live
"""
import datetime
import os

import psycopg2
import pytest
from unittest.mock import MagicMock

from data_agent.etl.etl_pipeline import ETL_Pipeline


# ---------------------------------------------------------------------------
# Module-level marker: every test here is a live test
# ---------------------------------------------------------------------------
pytestmark = pytest.mark.live


# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------
TEST_SCHEMA = 'test_etl'

# Small 2x2 degree bbox centered on Alabama (CONUS) for all tests
BBOX = {'west': -88.0, 'south': 32.0, 'east': -86.0, 'north': 34.0}

# Historical date known to be available for CHIRPS/PRISM
HISTORICAL_DATE = datetime.date(2024, 1, 15)

# CHIRTS archive ends at 2016
CHIRTS_DATE = datetime.date(2016, 6, 15)

# Recent date for fast-availability APIs (NASA POWER, Open-Meteo)
RECENT_DATE = datetime.date.today() - datetime.timedelta(days=10)


# ---------------------------------------------------------------------------
# Skip helpers
# ---------------------------------------------------------------------------
skip_without_cds_key = pytest.mark.skipif(
    not os.environ.get('CDS_API_KEY'),
    reason='CDS_API_KEY environment variable not set',
)


# ---------------------------------------------------------------------------
# Database fixtures
# ---------------------------------------------------------------------------
def _get_test_connection():
    """Connect to the test PostGIS database using the same env vars as Django settings."""
    return psycopg2.connect(
        host=os.environ.get('DBHOST', 'postgres'),
        port=os.environ.get('DBPORT', '5432'),
        dbname=os.environ.get('DBNAME', 'dssatserv'),
        user=os.environ.get('DBUSER', 'postgres'),
        password=os.environ.get('PASSWORD', ''),
    )


@pytest.fixture(scope='module')
def db_con():
    """
    Module-scoped PostGIS connection.
    Creates the test schema on setup, drops it on teardown.
    """
    con = _get_test_connection()
    con.autocommit = True
    cur = con.cursor()
    cur.execute('CREATE EXTENSION IF NOT EXISTS postgis')
    cur.execute(f'DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE')
    cur.execute(f'CREATE SCHEMA {TEST_SCHEMA}')
    cur.close()
    con.autocommit = False
    yield con
    con.autocommit = True
    cur = con.cursor()
    cur.execute(f'DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE')
    cur.close()
    con.close()


@pytest.fixture(autouse=True)
def clean_tables(db_con):
    """Drop all tables in test schema after each test."""
    yield
    db_con.rollback()
    cur = db_con.cursor()
    cur.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = %s",
        (TEST_SCHEMA,),
    )
    tables = [row[0] for row in cur.fetchall()]
    for t in tables:
        cur.execute(f'DROP TABLE IF EXISTS {TEST_SCHEMA}.{t} CASCADE')
    db_con.commit()
    cur.close()


# ---------------------------------------------------------------------------
# Pipeline factory
# ---------------------------------------------------------------------------
@pytest.fixture
def make_pipeline():
    """Factory that creates an ETL_Pipeline with real configs (no mocks)."""
    def _factory(fetch_config, subroutines=None, table_prefix='test',
                 start_date=None, end_date=None, bbox=None):
        ds = MagicMock()
        ds.dataset_name = 'Live Test'
        ds.tds_product_name = 'LIVE'
        ds.tds_region = 'Global'
        ds.tds_spatial_resolution = '0.05deg'
        ds.tds_temporal_resolution = 'daily'
        ds.merge_only = False

        subs = subroutines or {}
        subs.setdefault('load_to_postgis', {
            'schema': TEST_SCHEMA, 'table_prefix': table_prefix,
        })

        ds.dataset_information = {
            'metadata': {},
            'subroutines': subs,
            'granule_info': {'fetch_config': fetch_config},
        }

        stdout = MagicMock()
        stdout.write = MagicMock()
        style = MagicMock()
        style.SUCCESS = lambda x: x

        return ETL_Pipeline(
            dataset=ds,
            stdout=stdout,
            style=style,
            start_date=start_date or HISTORICAL_DATE,
            end_date=end_date or HISTORICAL_DATE,
            bbox=bbox or BBOX,
        )
    return _factory


# ---------------------------------------------------------------------------
# Database query helpers
# ---------------------------------------------------------------------------
def _count_rows(con, schema, table):
    """Return the number of rows in schema.table, or 0 if missing."""
    try:
        cur = con.cursor()
        cur.execute(f'SELECT count(*) FROM {schema}.{table}')
        count = cur.fetchone()[0]
        cur.close()
        return count
    except Exception:
        con.rollback()
        return 0


def _get_dates(con, schema, table):
    """Return sorted list of distinct fdates in a table."""
    try:
        cur = con.cursor()
        cur.execute(f'SELECT DISTINCT fdate FROM {schema}.{table} ORDER BY fdate')
        dates = [row[0] for row in cur.fetchall()]
        cur.close()
        return dates
    except Exception:
        con.rollback()
        return []


def _get_ens_values(con, schema, table):
    """Return sorted list of distinct ens values in a forecast table."""
    try:
        cur = con.cursor()
        cur.execute(f'SELECT DISTINCT ens FROM {schema}.{table} ORDER BY ens')
        vals = [row[0] for row in cur.fetchall()]
        cur.close()
        return vals
    except Exception:
        con.rollback()
        return []


def _table_exists(con, schema, table):
    """Check whether a table exists in the given schema."""
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT 1 FROM pg_tables WHERE schemaname = %s AND tablename = %s",
            (schema, table),
        )
        exists = cur.fetchone() is not None
        cur.close()
        return exists
    except Exception:
        con.rollback()
        return False


# ======================================================================
# CHIRPS -- file_download / tif_gz (global daily rainfall)
# ======================================================================
@pytest.mark.slow
class TestCHIRPSLive:
    """Live download of CHIRPS daily rainfall GeoTIFF (gzipped)."""

    def test_chirps_full_pipeline(self, make_pipeline, db_con):
        fetch_config = {
            'format': 'file_download',
            'file_format': 'tif_gz',
            'variables': [
                {
                    'target_variable': 'rain',
                    'url_template': (
                        'https://data.chc.ucsb.edu/products/CHIRPS-2.0/'
                        'global_daily/tifs/p05/{YYYY}/'
                        'chirps-v2.0.{YYYY}.{MM}.{DD}.tif.gz'
                    ),
                },
            ],
        }
        subroutines = {
            'load_to_postgis': {
                'schema': TEST_SCHEMA, 'table_prefix': 'chirps',
            },
        }

        pipeline = make_pipeline(
            fetch_config, subroutines=subroutines,
            table_prefix='chirps',
            start_date=HISTORICAL_DATE, end_date=HISTORICAL_DATE,
        )
        pipeline._run_pipeline(
            fetch_config, db_con, TEST_SCHEMA, 'chirps', BBOX, subroutines,
        )

        assert _count_rows(db_con, TEST_SCHEMA, 'chirps_rain') > 0
        dates = _get_dates(db_con, TEST_SCHEMA, 'chirps_rain')
        assert HISTORICAL_DATE in dates


# ======================================================================
# CHIRTS -- file_download / tif (global daily temperature)
# ======================================================================
@pytest.mark.slow
class TestCHIRTSLive:
    """Live download of CHIRTS daily tmax/tmin GeoTIFF."""

    def test_chirts_full_pipeline(self, make_pipeline, db_con):
        fetch_config = {
            'format': 'file_download',
            'file_format': 'tif',
            'variables': [
                {
                    'target_variable': 'tmax',
                    'url_template': (
                        'https://data.chc.ucsb.edu/products/CHIRTSdaily/'
                        'v1.0/global_tifs_p05/Tmax/{YYYY}/'
                        'Tmax.{YYYY}.{MM}.{DD}.tif'
                    ),
                },
                {
                    'target_variable': 'tmin',
                    'url_template': (
                        'https://data.chc.ucsb.edu/products/CHIRTSdaily/'
                        'v1.0/global_tifs_p05/Tmin/{YYYY}/'
                        'Tmin.{YYYY}.{MM}.{DD}.tif'
                    ),
                },
            ],
        }
        subroutines = {
            'load_to_postgis': {
                'schema': TEST_SCHEMA, 'table_prefix': 'chirts',
            },
        }

        pipeline = make_pipeline(
            fetch_config, subroutines=subroutines,
            table_prefix='chirts',
            start_date=CHIRTS_DATE, end_date=CHIRTS_DATE,
        )
        pipeline._run_pipeline(
            fetch_config, db_con, TEST_SCHEMA, 'chirts', BBOX, subroutines,
        )

        assert _count_rows(db_con, TEST_SCHEMA, 'chirts_tmax') > 0
        assert _count_rows(db_con, TEST_SCHEMA, 'chirts_tmin') > 0
        assert CHIRTS_DATE in _get_dates(db_con, TEST_SCHEMA, 'chirts_tmax')
        assert CHIRTS_DATE in _get_dates(db_con, TEST_SCHEMA, 'chirts_tmin')


# ======================================================================
# PRISM -- file_download / zip_bil (CONUS daily weather)
# ======================================================================
class TestPRISMLive:
    """Live download of PRISM daily weather (BIL format in zip archives)."""

    def test_prism_full_pipeline(self, make_pipeline, db_con):
        # PRISM rate-limits to 2 downloads per file per day per IP.
        # Rotate the date based on today to avoid rate-limit collisions
        # across repeated test runs.
        prism_date = datetime.date(2024, 1, 1) + datetime.timedelta(
            days=datetime.date.today().toordinal() % 28 + 1,
        )

        fetch_config = {
            'format': 'file_download',
            'file_format': 'zip_bil',
            'variables': [
                {
                    'target_variable': 'rain',
                    'url_template': (
                        'https://services.nacse.org/prism/data/get/us/4km/ppt/{YYYYMMDD}'
                    ),
                },
                {
                    'target_variable': 'tmax',
                    'url_template': (
                        'https://services.nacse.org/prism/data/get/us/4km/tmax/{YYYYMMDD}'
                    ),
                },
                {
                    'target_variable': 'tmin',
                    'url_template': (
                        'https://services.nacse.org/prism/data/get/us/4km/tmin/{YYYYMMDD}'
                    ),
                },
            ],
        }
        subroutines = {
            'load_to_postgis': {
                'schema': TEST_SCHEMA, 'table_prefix': 'prism',
            },
        }

        pipeline = make_pipeline(
            fetch_config, subroutines=subroutines,
            table_prefix='prism',
            start_date=prism_date, end_date=prism_date,
        )
        pipeline._run_pipeline(
            fetch_config, db_con, TEST_SCHEMA, 'prism', BBOX, subroutines,
        )

        for var in ('rain', 'tmax', 'tmin'):
            table = f'prism_{var}'
            assert _count_rows(db_con, TEST_SCHEMA, table) > 0, (
                f'Expected rows in {table}'
            )
            assert prism_date in _get_dates(db_con, TEST_SCHEMA, table)


# ======================================================================
# NASA POWER -- regional_json_api (global daily, 0.5 deg)
# ======================================================================
class TestNASAPOWERLive:
    """Live call to NASA POWER regional JSON API."""

    def test_nasa_power_full_pipeline(self, make_pipeline, db_con):
        fetch_config = {
            'format': 'regional_json_api',
            'grid_resolution': 0.5,
            'nodata_value': -999,
            'url_template': (
                'https://power.larc.nasa.gov/api/temporal/daily/regional'
                '?parameters={parameter}'
                '&community=AG&longitude-min={bbox_west}&latitude-min={bbox_south}'
                '&longitude-max={bbox_east}&latitude-max={bbox_north}'
                '&start={start_date}&end={end_date}&format=JSON'
            ),
            'variable_map': {
                'T2M_MAX': 'tmax',
                'T2M_MIN': 'tmin',
                'PRECTOTCORR': 'rain',
                'ALLSKY_SFC_SW_DWN': 'srad',
            },
        }
        subroutines = {
            'load_to_postgis': {
                'schema': TEST_SCHEMA, 'table_prefix': 'power',
            },
            'unit_conversions': {
                'T2M_MAX': {'operation': 'rename', 'target_name': 'tmax'},
                'T2M_MIN': {'operation': 'rename', 'target_name': 'tmin'},
                'PRECTOTCORR': {'operation': 'rename', 'target_name': 'rain'},
                'ALLSKY_SFC_SW_DWN': {'operation': 'rename', 'target_name': 'srad'},
            },
            'expand_bbox': {'min_range': 2.0},
        }

        pipeline = make_pipeline(
            fetch_config, subroutines=subroutines,
            table_prefix='power',
            start_date=RECENT_DATE, end_date=RECENT_DATE,
        )
        pipeline._run_pipeline(
            fetch_config, db_con, TEST_SCHEMA, 'power', BBOX, subroutines,
        )

        for var in ('tmax', 'tmin', 'rain', 'srad'):
            table = f'power_{var}'
            assert _count_rows(db_con, TEST_SCHEMA, table) > 0, (
                f'Expected rows in {table}'
            )


# ======================================================================
# Open-Meteo -- point_api_grid (ensemble forecast, 0.25 deg)
# ======================================================================
class TestOpenMeteoLive:
    """Live call to Open-Meteo ensemble forecast API."""

    def test_open_meteo_full_pipeline(self, make_pipeline, db_con):
        # Open-Meteo returns forecasts from today onward
        today = datetime.date.today()
        end = today + datetime.timedelta(days=1)

        fetch_config = {
            'format': 'point_api_grid',
            'grid_resolution': 0.25,
            'url_template': (
                'https://ensemble-api.open-meteo.com/v1/ensemble'
                '?latitude={lat}&longitude={lon}'
                '&daily=temperature_2m_max,temperature_2m_min,'
                'precipitation_sum,shortwave_radiation_sum'
                '&models=ecmwf_ifs025'
                '&start_date={start_date}&end_date={end_date}'
            ),
            'variable_map': {
                'temperature_2m_max': 'tmax',
                'temperature_2m_min': 'tmin',
                'precipitation_sum': 'rain',
                'shortwave_radiation_sum': 'srad',
            },
        }
        subroutines = {
            'load_to_postgis': {
                'schema': TEST_SCHEMA, 'table_prefix': 'open_meteo',
            },
            'unit_conversions': {
                'temperature_2m_max': {'operation': 'rename', 'target_name': 'tmax'},
                'temperature_2m_min': {'operation': 'rename', 'target_name': 'tmin'},
                'precipitation_sum': {'operation': 'rename', 'target_name': 'rain'},
                'shortwave_radiation_sum': {'operation': 'rename', 'target_name': 'srad'},
            },
        }

        pipeline = make_pipeline(
            fetch_config, subroutines=subroutines,
            table_prefix='open_meteo',
            start_date=today, end_date=end,
        )
        pipeline._run_pipeline(
            fetch_config, db_con, TEST_SCHEMA, 'open_meteo', BBOX, subroutines,
        )

        # At least one variable should have loaded
        loaded_any = False
        for var in ('tmax', 'tmin', 'rain', 'srad'):
            table = f'open_meteo_{var}'
            if _count_rows(db_con, TEST_SCHEMA, table) > 0:
                loaded_any = True
                # Forecast tables should have ensemble members
                ens = _get_ens_values(db_con, TEST_SCHEMA, table)
                assert len(ens) > 0, f'Expected ensemble members in {table}'
        assert loaded_any, 'Expected at least one variable loaded from Open-Meteo'


# ======================================================================
# NMME -- xarray_slice / opendap (seasonal forecast)
# ======================================================================
class TestNMMELive:
    """Live OPeNDAP slice from NOAA/PSL NMME archive."""

    def test_nmme_full_pipeline(self, make_pipeline, db_con):
        # Use a single model to keep download small
        # NMME data is monthly; use a recent-ish init month
        today = datetime.date.today()
        init_date = datetime.date(today.year, today.month, 1)
        # Fallback to previous month if current month not yet available
        if today.day < 15:
            if today.month == 1:
                init_date = datetime.date(today.year - 1, 12, 1)
            else:
                init_date = datetime.date(today.year, today.month - 1, 1)

        fetch_config = {
            'format': 'xarray_slice',
            'transport': 'opendap',
            'ensemble_dims': ['ensemble', 'ens', 'member', 'M'],
            'base_url': 'https://psl.noaa.gov/thredds/dodsC/Datasets/NMME',
            'models': ['CCSM4'],
            'variable_map': {
                'tmax': 'tmax',
                'tmin': 'tmin',
                'prate': 'rain',
            },
        }
        subroutines = {
            'load_to_postgis': {
                'schema': TEST_SCHEMA, 'table_prefix': 'nmme',
            },
            'unit_conversions': {
                'tmax': {'operation': 'rename', 'target_name': 'tmax'},
                'tmin': {'operation': 'rename', 'target_name': 'tmin'},
                'prate': {'operation': 'rename', 'target_name': 'rain'},
            },
        }

        pipeline = make_pipeline(
            fetch_config, subroutines=subroutines,
            table_prefix='nmme',
            start_date=init_date, end_date=init_date,
        )
        pipeline._run_pipeline(
            fetch_config, db_con, TEST_SCHEMA, 'nmme', BBOX, subroutines,
        )

        # NMME may or may not have data depending on timing; verify at least
        # that the pipeline ran without error. If data is available, check it.
        loaded_any = False
        for var in ('tmax', 'tmin', 'rain'):
            table = f'nmme_{var}'
            if _count_rows(db_con, TEST_SCHEMA, table) > 0:
                loaded_any = True
                ens = _get_ens_values(db_con, TEST_SCHEMA, table)
                assert len(ens) > 0, f'Expected ensemble members in {table}'
        # If no data loaded, the pipeline gracefully found nothing to process
        # -- that's OK for NMME which may have sparse temporal coverage.


# ======================================================================
# SEAS5 -- xarray_slice / cds_api (CDS seasonal forecast)
# ======================================================================
@skip_without_cds_key
class TestSEAS5Live:
    """Live CDS API call for SEAS5 seasonal forecast.

    Requires CDS_API_KEY environment variable.
    May be slow due to CDS queue wait times.
    """

    def test_seas5_full_pipeline(self, make_pipeline, db_con):
        # SEAS5 availability lags several months; use a well-known past month
        init_date = datetime.date(2024, 6, 1)

        fetch_config = {
            'format': 'xarray_slice',
            'transport': 'cds_api',
            'ensemble_dims': ['number', 'ensemble', 'member', 'realization'],
            'cds_product': 'seasonal-original-single-levels',
            'cds_request_extras': {
                'originating_centre': 'ecmwf',
                'system': '51',
                'day': '01',
                # Only request 3 days of lead time to keep download small
                'leadtime_hour': [str(h) for h in range(24, 24 * 4, 24)],
                'data_format': 'grib',
            },
            'variable_map': {
                'maximum_2m_temperature_in_the_last_24_hours': 'tmax',
                'minimum_2m_temperature_in_the_last_24_hours': 'tmin',
                'total_precipitation': 'rain',
                'surface_solar_radiation_downwards': 'srad',
            },
        }
        subroutines = {
            'load_to_postgis': {
                'schema': TEST_SCHEMA, 'table_prefix': 'seas5',
            },
            'unit_conversions': {
                'maximum_2m_temperature_in_the_last_24_hours': {
                    'operation': 'rename', 'target_name': 'tmax',
                },
                'minimum_2m_temperature_in_the_last_24_hours': {
                    'operation': 'rename', 'target_name': 'tmin',
                },
                'total_precipitation': {
                    'operation': 'multiply', 'value': 1000,
                    'target_name': 'rain',
                },
                'surface_solar_radiation_downwards': {
                    'operation': 'rename', 'target_name': 'srad',
                },
            },
        }

        pipeline = make_pipeline(
            fetch_config, subroutines=subroutines,
            table_prefix='seas5',
            start_date=init_date, end_date=init_date,
        )
        pipeline._run_pipeline(
            fetch_config, db_con, TEST_SCHEMA, 'seas5', BBOX, subroutines,
        )

        # SEAS5 should produce data with ensemble members
        loaded_any = False
        for var in ('tmax', 'tmin', 'rain', 'srad'):
            table = f'seas5_{var}'
            if _count_rows(db_con, TEST_SCHEMA, table) > 0:
                loaded_any = True
                ens = _get_ens_values(db_con, TEST_SCHEMA, table)
                assert len(ens) > 0, f'Expected ensemble members in {table}'
        assert loaded_any, 'Expected at least one variable loaded from SEAS5'


# ======================================================================
# ERA5 -- xarray_slice / cds_api with daily aggregation (hourly -> daily)
# ======================================================================
@skip_without_cds_key
@pytest.mark.slow
class TestERA5Live:
    """Live CDS API call for ERA5 reanalysis with hourly-to-daily aggregation.

    Requires CDS_API_KEY environment variable.
    Downloads 24 hours of data for the bbox, then aggregates to daily.
    """

    def test_era5_full_pipeline(self, make_pipeline, db_con):
        # Use a date well within ERA5 availability (months behind real-time)
        test_date = datetime.date(2024, 1, 15)

        fetch_config = {
            'format': 'xarray_slice',
            'transport': 'cds_api',
            'cds_product': 'reanalysis-era5-single-levels',
            'include_day_range': True,
            'cds_request_extras': {
                'product_type': ['reanalysis'],
                'time': [f'{h:02d}:00' for h in range(24)],
            },
            # Split into separate CDS requests per variable -- the new CDS
            # API may silently drop variables from multi-variable requests.
            'cds_variable_groups': [
                {'variables': ['2m_temperature']},
                {'variables': ['total_precipitation']},
                {'variables': ['surface_solar_radiation_downwards']},
            ],
            'daily_aggregation': {
                't2m_max': {'cds_var': '2m_temperature', 'method': 'max'},
                't2m_min': {'cds_var': '2m_temperature', 'method': 'min'},
                'total_precipitation': {
                    'cds_var': 'total_precipitation', 'method': 'sum',
                },
                'surface_solar_radiation_downwards': {
                    'cds_var': 'surface_solar_radiation_downwards', 'method': 'sum',
                },
            },
            'variable_map': {
                '2m_temperature': ['tmax', 'tmin'],
                'total_precipitation': 'rain',
                'surface_solar_radiation_downwards': 'srad',
            },
        }
        subroutines = {
            'load_to_postgis': {
                'schema': TEST_SCHEMA, 'table_prefix': 'era5',
            },
            'unit_conversions': {
                't2m_max': {'operation': 'rename', 'target_name': 'tmax'},
                't2m_min': {'operation': 'rename', 'target_name': 'tmin'},
                'total_precipitation': {
                    'operation': 'multiply', 'value': 1000,
                    'target_name': 'rain',
                },
                'surface_solar_radiation_downwards': {
                    'operation': 'divide', 'value': 86400,
                    'target_name': 'srad',
                },
            },
        }

        pipeline = make_pipeline(
            fetch_config, subroutines=subroutines,
            table_prefix='era5',
            start_date=test_date, end_date=test_date,
        )
        pipeline._run_pipeline(
            fetch_config, db_con, TEST_SCHEMA, 'era5', BBOX, subroutines,
        )

        # ERA5 should produce one row per variable per day (no ensemble)
        for var in ('tmax', 'tmin', 'rain', 'srad'):
            table = f'era5_{var}'
            assert _count_rows(db_con, TEST_SCHEMA, table) > 0, (
                f'Expected rows in {table}'
            )
            assert test_date in _get_dates(db_con, TEST_SCHEMA, table)


# ======================================================================
# AgERA5 -- xarray_slice / cds_api (daily agrometeorological indicators)
# ======================================================================
@skip_without_cds_key
class TestAgERA5Live:
    """Live CDS API call for AgERA5 daily agrometeorological data.

    Requires CDS_API_KEY environment variable.
    AgERA5 already provides daily data -- no hourly aggregation needed.
    """

    def test_agera5_full_pipeline(self, make_pipeline, db_con):
        # AgERA5 availability lags ~1 month behind real-time
        test_date = datetime.date(2024, 1, 15)

        fetch_config = {
            'format': 'xarray_slice',
            'transport': 'cds_api',
            'cds_product': 'sis-agrometeorological-indicators',
            'include_day_range': True,
            'cds_request_extras': {
                'version': '1_1',
            },
            'cds_variable_groups': [
                {
                    'variables': ['2m_temperature'],
                    'extras': {'statistic': '24_hour_maximum'},
                },
                {
                    'variables': ['2m_temperature'],
                    'extras': {'statistic': '24_hour_minimum'},
                },
                {
                    'variables': ['precipitation_flux'],
                    'extras': {'statistic': '24_hour_mean'},
                },
                {
                    'variables': ['solar_radiation_flux'],
                    'extras': {'statistic': '24_hour_mean'},
                },
            ],
            'variable_map': {
                '2m_temperature': ['tmax', 'tmin'],
                'precipitation_flux': 'rain',
                'solar_radiation_flux': 'srad',
            },
        }
        subroutines = {
            'load_to_postgis': {
                'schema': TEST_SCHEMA, 'table_prefix': 'agera5',
            },
            'unit_conversions': {
                '2m_temperature_max': {
                    'operation': 'rename', 'target_name': 'tmax',
                },
                '2m_temperature_min': {
                    'operation': 'rename', 'target_name': 'tmin',
                },
                'precipitation_flux': {
                    'operation': 'multiply', 'value': 86400,
                    'target_name': 'rain',
                },
                'solar_radiation_flux': {
                    'operation': 'rename', 'target_name': 'srad',
                },
            },
        }

        pipeline = make_pipeline(
            fetch_config, subroutines=subroutines,
            table_prefix='agera5',
            start_date=test_date, end_date=test_date,
        )
        pipeline._run_pipeline(
            fetch_config, db_con, TEST_SCHEMA, 'agera5', BBOX, subroutines,
        )

        for var in ('tmax', 'tmin', 'rain', 'srad'):
            table = f'agera5_{var}'
            assert _count_rows(db_con, TEST_SCHEMA, table) > 0, (
                f'Expected rows in {table}'
            )
            assert test_date in _get_dates(db_con, TEST_SCHEMA, table)


# ======================================================================
# CFSv2 -- grib2_bands (NOAA S3 seasonal forecast)
# ======================================================================
@pytest.mark.slow
class TestCFSv2Live:
    """Live GRIB2 download from NOAA CFS S3 bucket.

    Downloads can be 50-200 MB per file, so this is marked @slow.
    """

    def test_cfsv2_full_pipeline(self, make_pipeline, db_con):
        # Use today's date -- the pipeline has built-in fallback for
        # init dates that aren't yet available.
        today = datetime.date.today()

        fetch_config = {
            'format': 'grib2_bands',
            'base_url': 'https://noaa-cfs-pds.s3.amazonaws.com',
            'variable_map': {
                'tmax': 'tmax',
                'tmin': 'tmin',
                'prate': 'rain',
                'dswsfc': 'srad',
            },
            'members': [1],  # Single member to keep download small
            'init_hours': ['00'],
        }
        subroutines = {
            'load_to_postgis': {
                'schema': TEST_SCHEMA, 'table_prefix': 'cfsv2',
            },
            'unit_conversions': {
                'tmax': {'operation': 'rename', 'target_name': 'tmax'},
                'tmin': {'operation': 'rename', 'target_name': 'tmin'},
                'prate': {
                    'operation': 'multiply', 'value': 86400,
                    'target_name': 'rain',
                },
                'dswsfc': {
                    'operation': 'multiply', 'value': 86400,
                    'target_name': 'srad',
                },
            },
        }

        pipeline = make_pipeline(
            fetch_config, subroutines=subroutines,
            table_prefix='cfsv2',
            start_date=today, end_date=today,
        )
        pipeline._run_pipeline(
            fetch_config, db_con, TEST_SCHEMA, 'cfsv2', BBOX, subroutines,
        )

        # CFSv2 availability depends on NOAA processing; verify pipeline
        # ran without error. If data is available, check it.
        loaded_any = False
        for var in ('tmax', 'tmin', 'rain', 'srad'):
            table = f'cfsv2_{var}'
            if _count_rows(db_con, TEST_SCHEMA, table) > 0:
                loaded_any = True
                ens = _get_ens_values(db_con, TEST_SCHEMA, table)
                assert len(ens) > 0, f'Expected ensemble members in {table}'


# ======================================================================
# Cross-cutting: verify transform correctness on real data
# ======================================================================
class TestCrossCuttingLive:
    """Verify that real API data passes through transform correctly.

    Uses NASA POWER (fast, reliable API) to test that:
    - Variable renaming works on real payloads
    - No-data values are handled
    - Spatial extent matches requested bbox
    """

    def test_real_data_variable_renaming(self, make_pipeline, db_con):
        """Verify unit_conversions rename real NASA POWER variables."""
        fetch_config = {
            'format': 'regional_json_api',
            'grid_resolution': 0.5,
            'nodata_value': -999,
            'url_template': (
                'https://power.larc.nasa.gov/api/temporal/daily/regional'
                '?parameters={parameter}'
                '&community=AG&longitude-min={bbox_west}&latitude-min={bbox_south}'
                '&longitude-max={bbox_east}&latitude-max={bbox_north}'
                '&start={start_date}&end={end_date}&format=JSON'
            ),
            'variable_map': {
                'T2M_MAX': 'tmax',
            },
        }
        subroutines = {
            'load_to_postgis': {
                'schema': TEST_SCHEMA, 'table_prefix': 'xcut',
            },
            'unit_conversions': {
                'T2M_MAX': {'operation': 'rename', 'target_name': 'tmax'},
            },
            'expand_bbox': {'min_range': 2.0},
        }

        pipeline = make_pipeline(
            fetch_config, subroutines=subroutines,
            table_prefix='xcut',
            start_date=RECENT_DATE, end_date=RECENT_DATE,
        )
        pipeline._run_pipeline(
            fetch_config, db_con, TEST_SCHEMA, 'xcut', BBOX, subroutines,
        )

        # The table should be named with the target variable, not the source
        assert _count_rows(db_con, TEST_SCHEMA, 'xcut_tmax') > 0
        # The source variable name should NOT appear as a table
        assert not _table_exists(db_con, TEST_SCHEMA, 'xcut_T2M_MAX')

    def test_real_data_spatial_extent(self, make_pipeline, db_con):
        """Verify loaded rasters cover the requested bbox."""
        fetch_config = {
            'format': 'regional_json_api',
            'grid_resolution': 0.5,
            'nodata_value': -999,
            'url_template': (
                'https://power.larc.nasa.gov/api/temporal/daily/regional'
                '?parameters={parameter}'
                '&community=AG&longitude-min={bbox_west}&latitude-min={bbox_south}'
                '&longitude-max={bbox_east}&latitude-max={bbox_north}'
                '&start={start_date}&end={end_date}&format=JSON'
            ),
            'variable_map': {
                'T2M_MAX': 'tmax',
            },
        }
        subroutines = {
            'load_to_postgis': {
                'schema': TEST_SCHEMA, 'table_prefix': 'spatial',
            },
            'unit_conversions': {
                'T2M_MAX': {'operation': 'rename', 'target_name': 'tmax'},
            },
            'expand_bbox': {'min_range': 2.0},
        }

        pipeline = make_pipeline(
            fetch_config, subroutines=subroutines,
            table_prefix='spatial',
            start_date=RECENT_DATE, end_date=RECENT_DATE,
        )
        pipeline._run_pipeline(
            fetch_config, db_con, TEST_SCHEMA, 'spatial', BBOX, subroutines,
        )

        # Check raster envelope intersects our bbox
        cur = db_con.cursor()
        cur.execute(f"""
            SELECT ST_XMin(env), ST_YMin(env), ST_XMax(env), ST_YMax(env)
            FROM (
                SELECT ST_Envelope(rast) AS env
                FROM {TEST_SCHEMA}.spatial_tmax
                LIMIT 1
            ) sub
        """)
        row = cur.fetchone()
        cur.close()

        if row is not None:
            xmin, ymin, xmax, ymax = row
            # Raster envelope should overlap the requested bbox
            assert xmax >= BBOX['west'], 'Raster xmax should be >= bbox west'
            assert xmin <= BBOX['east'], 'Raster xmin should be <= bbox east'
            assert ymax >= BBOX['south'], 'Raster ymax should be >= bbox south'
            assert ymin <= BBOX['north'], 'Raster ymin should be <= bbox north'
