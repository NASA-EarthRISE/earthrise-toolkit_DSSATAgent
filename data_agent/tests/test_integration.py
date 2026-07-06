"""
Integration tests for the config-driven ETL pipeline.

Each test exercises the FULL Discover -> Extract -> Transform -> Load flow
for one configured data source, with mocked HTTP/API responses and a
real PostGIS database.

Mark: @pytest.mark.integration
Run:  docker compose exec dataagent python -m pytest data_agent/tests/test_integration.py -v -m integration
"""
import datetime
import gzip
import io
import json
import os
import tempfile
import zipfile

import numpy as np
import pandas as pd
import psycopg2
import pytest
import rasterio
import xarray as xr
from rasterio.transform import from_bounds
from unittest.mock import MagicMock, patch, PropertyMock

from data_agent.etl.etl_pipeline import ETL_Pipeline
from data_agent.tests.conftest import (
    BBOX, START_DATE, END_DATE,
    create_test_tiff, create_test_tiff_bytes,
    make_nasa_power_response, make_open_meteo_response,
    chirps_fetch_config, chirts_fetch_config, prism_fetch_config,
    nasa_power_fetch_config, nasa_power_subroutines,
    open_meteo_fetch_config, open_meteo_subroutines,
    nmme_fetch_config, nmme_subroutines,
    seas5_fetch_config, seas5_subroutines,
    cfsv2_fetch_config, cfsv2_subroutines,
)


# ---------------------------------------------------------------------------
# Integration test marker applied to entire module
# ---------------------------------------------------------------------------
pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Database fixtures
# ---------------------------------------------------------------------------
TEST_SCHEMA = 'test_etl'


def _get_test_connection():
    """Connect to the test PostGIS database using env vars or defaults."""
    return psycopg2.connect(
        host=os.environ.get('POSTGRES_HOST', 'postgres'),
        port=os.environ.get('POSTGRES_PORT', '5432'),
        dbname=os.environ.get('POSTGRES_DB', 'dssatserv'),
        user=os.environ.get('POSTGRES_USER', 'postgres'),
        password=os.environ.get('POSTGRES_PASSWORD', 'postgres'),
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

    # Ensure PostGIS extension exists
    cur.execute('CREATE EXTENSION IF NOT EXISTS postgis')
    # Create clean test schema
    cur.execute(f'DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE')
    cur.execute(f'CREATE SCHEMA {TEST_SCHEMA}')
    cur.close()
    con.autocommit = False

    yield con

    # Teardown: drop entire test schema
    con.autocommit = True
    cur = con.cursor()
    cur.execute(f'DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE')
    cur.close()
    con.close()


@pytest.fixture(autouse=True)
def clean_tables(db_con):
    """
    Per-test cleanup: drop all tables in the test schema so each
    test starts fresh.
    """
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
# Pipeline factory fixture
# ---------------------------------------------------------------------------
@pytest.fixture
def make_pipeline():
    """
    Factory that returns an ETL_Pipeline instance with configurable
    dataset_information. Uses mock dataset + stdout/style.
    """
    def _factory(fetch_config, subroutines=None, table_prefix='test',
                 start_date=None, end_date=None, bbox=None):
        ds = MagicMock()
        ds.dataset_name = 'Integration Test'
        ds.tds_product_name = 'TEST'
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
            start_date=start_date or START_DATE,
            end_date=end_date or END_DATE,
            bbox=bbox or BBOX,
        )
    return _factory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _count_rows(con, schema, table):
    """Return the number of rows in schema.table, or 0 if table doesn't exist."""
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


# ======================================================================
# 1. CHIRPS — file_download, tif_gz
# ======================================================================
class TestCHIRPSIntegration:

    def test_full_pipeline(self, make_pipeline, db_con):
        """CHIRPS: download tif.gz -> decompress -> clip -> load to PostGIS."""
        fc = chirps_fetch_config()
        pipeline = make_pipeline(fc, table_prefix='chirps')

        # Build a small test GeoTIFF compressed as .tif.gz
        tiff_bytes = create_test_tiff_bytes(bbox=BBOX)
        compressed = gzip.compress(tiff_bytes)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = compressed

        with patch('data_agent.etl.etl_pipeline.requests.get', return_value=mock_resp):
            pipeline._run_pipeline(
                fc, db_con, TEST_SCHEMA, 'chirps', BBOX,
                {'load_to_postgis': {'schema': TEST_SCHEMA, 'table_prefix': 'chirps'}},
            )

        # Should have loaded 3 days of rain data
        rows = _count_rows(db_con, TEST_SCHEMA, 'chirps_rain')
        assert rows > 0, 'No raster rows loaded for chirps_rain'

        dates = _get_dates(db_con, TEST_SCHEMA, 'chirps_rain')
        assert len(dates) == 3, f'Expected 3 dates, got {len(dates)}: {dates}'

    def test_idempotent(self, make_pipeline, db_con):
        """CHIRPS: running pipeline twice should not duplicate data."""
        fc = chirps_fetch_config()
        pipeline = make_pipeline(fc, table_prefix='chirps')

        tiff_bytes = create_test_tiff_bytes(bbox=BBOX)
        compressed = gzip.compress(tiff_bytes)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = compressed
        subs = {'load_to_postgis': {'schema': TEST_SCHEMA, 'table_prefix': 'chirps'}}

        with patch('data_agent.etl.etl_pipeline.requests.get', return_value=mock_resp):
            pipeline._run_pipeline(fc, db_con, TEST_SCHEMA, 'chirps', BBOX, subs)
            rows_first = _count_rows(db_con, TEST_SCHEMA, 'chirps_rain')

            pipeline._run_pipeline(fc, db_con, TEST_SCHEMA, 'chirps', BBOX, subs)
            rows_second = _count_rows(db_con, TEST_SCHEMA, 'chirps_rain')

        assert rows_second == rows_first, (
            f'Idempotency failed: first={rows_first}, second={rows_second}'
        )


# ======================================================================
# 2. CHIRTS — file_download, tif
# ======================================================================
class TestCHIRTSIntegration:

    def test_full_pipeline(self, make_pipeline, db_con):
        """CHIRTS: download plain .tif for tmax and tmin -> load to PostGIS."""
        fc = chirts_fetch_config()
        pipeline = make_pipeline(fc, table_prefix='chirts')

        tiff_bytes = create_test_tiff_bytes(bbox=BBOX)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = tiff_bytes

        subs = {'load_to_postgis': {'schema': TEST_SCHEMA, 'table_prefix': 'chirts'}}

        with patch('data_agent.etl.etl_pipeline.requests.get', return_value=mock_resp):
            pipeline._run_pipeline(fc, db_con, TEST_SCHEMA, 'chirts', BBOX, subs)

        # CHIRTS has 2 variables: tmax and tmin
        for var in ['tmax', 'tmin']:
            rows = _count_rows(db_con, TEST_SCHEMA, f'chirts_{var}')
            assert rows > 0, f'No raster rows loaded for chirts_{var}'

        tmax_dates = _get_dates(db_con, TEST_SCHEMA, 'chirts_tmax')
        tmin_dates = _get_dates(db_con, TEST_SCHEMA, 'chirts_tmin')
        assert len(tmax_dates) == 3
        assert len(tmin_dates) == 3


# ======================================================================
# 3. PRISM — file_download, zip_bil
# ======================================================================
class TestPRISMIntegration:

    def test_full_pipeline(self, make_pipeline, db_con):
        """PRISM: download .zip containing .bil -> convert -> clip -> load."""
        fc = prism_fetch_config()
        pipeline = make_pipeline(fc, table_prefix='prism')

        # Build a zip containing a GeoTIFF masquerading as .bil
        # (pipeline uses _convert_to_geotiff which handles zip_bil)
        tiff_bytes = create_test_tiff_bytes(bbox=BBOX)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr('prism_data.bil', tiff_bytes)
            # Write minimal .hdr to avoid errors — _convert_to_geotiff
            # looks for .bil inside the zip
            zf.writestr('prism_data.hdr', '')
        zip_bytes = buf.getvalue()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = zip_bytes

        subs = {'load_to_postgis': {'schema': TEST_SCHEMA, 'table_prefix': 'prism'}}

        with patch('data_agent.etl.etl_pipeline.requests.get', return_value=mock_resp):
            pipeline._run_pipeline(fc, db_con, TEST_SCHEMA, 'prism', BBOX, subs)

        # PRISM has rain, tmax, tmin
        for var in ['rain', 'tmax', 'tmin']:
            rows = _count_rows(db_con, TEST_SCHEMA, f'prism_{var}')
            assert rows > 0, f'No raster rows loaded for prism_{var}'


# ======================================================================
# 4. NASA POWER — regional_json_api
# ======================================================================
class TestNASAPOWERIntegration:

    def test_full_pipeline(self, make_pipeline, db_con):
        """NASA POWER: fetch regional JSON API -> grid -> load to PostGIS."""
        fc = nasa_power_fetch_config()
        subs = nasa_power_subroutines()
        pipeline = make_pipeline(fc, subroutines=subs, table_prefix='power')

        # Build date range
        dates = []
        d = START_DATE
        while d <= END_DATE:
            dates.append(d)
            d += datetime.timedelta(days=1)

        # Mock responses for each of the 4 variables
        # The expanded bbox has lons ~ [-89, -85] and lats ~ [31, 35] at 0.5 deg resolution
        api_bbox = pipeline._apply_expand_bbox(BBOX, subs)
        lons = np.arange(float(api_bbox['west']), float(api_bbox['east']) + 0.1, 0.5).tolist()
        lats = np.arange(float(api_bbox['south']), float(api_bbox['north']) + 0.1, 0.5).tolist()

        def mock_get(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            # Determine which source_var from the URL
            for sv in ['T2M_MAX', 'T2M_MIN', 'PRECTOTCORR', 'ALLSKY_SFC_SW_DWN']:
                if sv in url:
                    resp.json = lambda sv=sv: make_nasa_power_response(
                        sv, lons, lats, dates,
                    )
                    break
            return resp

        with patch('data_agent.etl.etl_pipeline.requests.get', side_effect=mock_get):
            pipeline._run_pipeline(fc, db_con, TEST_SCHEMA, 'power', BBOX, subs)

        # All 4 variables should be loaded
        for var in ['tmax', 'tmin', 'rain', 'srad']:
            rows = _count_rows(db_con, TEST_SCHEMA, f'power_{var}')
            assert rows > 0, f'No rows loaded for power_{var}'

        power_dates = _get_dates(db_con, TEST_SCHEMA, 'power_tmax')
        assert len(power_dates) == 3

    def test_skip_loaded_dates(self, make_pipeline, db_con):
        """NASA POWER: already-loaded dates should be skipped via loaded_dates filter."""
        fc = nasa_power_fetch_config()
        subs = nasa_power_subroutines()
        pipeline = make_pipeline(fc, subroutines=subs, table_prefix='power')

        dates = []
        d = START_DATE
        while d <= END_DATE:
            dates.append(d)
            d += datetime.timedelta(days=1)

        api_bbox = pipeline._apply_expand_bbox(BBOX, subs)
        lons = np.arange(float(api_bbox['west']), float(api_bbox['east']) + 0.1, 0.5).tolist()
        lats = np.arange(float(api_bbox['south']), float(api_bbox['north']) + 0.1, 0.5).tolist()

        call_count = {'n': 0}

        def mock_get(url, **kwargs):
            call_count['n'] += 1
            resp = MagicMock()
            resp.status_code = 200
            for sv in ['T2M_MAX', 'T2M_MIN', 'PRECTOTCORR', 'ALLSKY_SFC_SW_DWN']:
                if sv in url:
                    resp.json = lambda sv=sv: make_nasa_power_response(
                        sv, lons, lats, dates,
                    )
                    break
            return resp

        with patch('data_agent.etl.etl_pipeline.requests.get', side_effect=mock_get):
            pipeline._run_pipeline(fc, db_con, TEST_SCHEMA, 'power', BBOX, subs)
            first_calls = call_count['n']

            # Second run should still make API calls (because the API returns
            # full date range) but loaded_dates filter skips loading
            pipeline._run_pipeline(fc, db_con, TEST_SCHEMA, 'power', BBOX, subs)

        # Row count should remain the same
        rows = _count_rows(db_con, TEST_SCHEMA, 'power_tmax')
        assert rows > 0


# ======================================================================
# 5. Open-Meteo — point_api_grid (ensemble)
# ======================================================================
class TestOpenMeteoIntegration:

    def test_full_pipeline(self, make_pipeline, db_con):
        """Open-Meteo: per-point ensemble API -> grid assembly -> load."""
        fc = open_meteo_fetch_config()
        subs = open_meteo_subroutines()
        pipeline = make_pipeline(fc, subroutines=subs, table_prefix='open_meteo')

        dates = []
        d = START_DATE
        while d <= END_DATE:
            dates.append(d)
            d += datetime.timedelta(days=1)

        variable_map = fc['variable_map']
        n_members = 2  # small for test speed

        def mock_get(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.json = lambda: make_open_meteo_response(
                variable_map, dates, n_members=n_members,
            )
            return resp

        with patch('data_agent.etl.etl_pipeline.requests.get', side_effect=mock_get):
            pipeline._run_pipeline(fc, db_con, TEST_SCHEMA, 'open_meteo', BBOX, subs)

        # Unit conversions should rename api vars -> target vars
        for target in ['tmax', 'tmin', 'rain', 'srad']:
            rows = _count_rows(db_con, TEST_SCHEMA, f'open_meteo_{target}')
            assert rows > 0, f'No rows for open_meteo_{target}'

        # Should have ensemble data
        ens_vals = _get_ens_values(db_con, TEST_SCHEMA, 'open_meteo_tmax')
        assert len(ens_vals) == n_members, (
            f'Expected {n_members} ensemble members, got {len(ens_vals)}'
        )

    def test_no_bbox_returns_empty(self, make_pipeline, db_con):
        """Open-Meteo: no bbox -> empty manifest."""
        fc = open_meteo_fetch_config()
        subs = open_meteo_subroutines()
        pipeline = make_pipeline(fc, subroutines=subs, table_prefix='open_meteo', bbox=BBOX)

        manifest = pipeline._discover_point_api_grid(
            fc, None, subs, db_con, TEST_SCHEMA, 'open_meteo',
        )
        assert manifest == []


# ======================================================================
# 6. NMME — xarray_slice, opendap
# ======================================================================
class TestNMMEIntegration:

    def test_full_pipeline(self, make_pipeline, db_con):
        """NMME: open xarray via opendap -> slice -> load ensemble forecasts."""
        fc = nmme_fetch_config()
        subs = nmme_subroutines()
        pipeline = make_pipeline(fc, subroutines=subs, table_prefix='nmme')

        # Create a mock xarray dataset
        times = pd.date_range(START_DATE, END_DATE, freq='D')
        lats = np.arange(32.0, 34.5, 0.5)
        lons = np.arange(-88.0, -85.5, 0.5)
        ens = np.arange(2)

        def make_ds(source_var):
            data = np.random.rand(len(ens), len(times), len(lats), len(lons)).astype(np.float32)
            ds = xr.Dataset(
                {source_var: (['ensemble', 'time', 'lat', 'lon'], data)},
                coords={
                    'ensemble': ens,
                    'time': times,
                    'lat': lats,
                    'lon': lons,
                },
            )
            return ds

        datasets = {
            'tmax': make_ds('tmax'),
            'tmin': make_ds('tmin'),
            'prate': make_ds('prate'),
        }

        def mock_open_dataset(url, **kwargs):
            for var_name, ds in datasets.items():
                if var_name in url:
                    return ds
            raise FileNotFoundError(f'Mock: no dataset for {url}')

        with patch('data_agent.etl.etl_pipeline.xr.open_dataset', side_effect=mock_open_dataset):
            pipeline._run_pipeline(fc, db_con, TEST_SCHEMA, 'nmme', BBOX, subs)

        # Check all 3 target variables
        for target in ['tmax', 'tmin', 'rain']:
            rows = _count_rows(db_con, TEST_SCHEMA, f'nmme_{target}')
            assert rows > 0, f'No rows for nmme_{target}'

        # Check ensemble dimension
        ens_vals = _get_ens_values(db_con, TEST_SCHEMA, 'nmme_tmax')
        assert len(ens_vals) >= 1, 'No ensemble members found'


# ======================================================================
# 7. SEAS5 — xarray_slice, cds_api
# ======================================================================
class TestSEAS5Integration:

    def test_full_pipeline(self, make_pipeline, db_con):
        """SEAS5: CDS API download -> xarray -> ensemble -> load."""
        fc = seas5_fetch_config()
        subs = seas5_subroutines()
        pipeline = make_pipeline(fc, subroutines=subs, table_prefix='seas5')

        # Build mock xarray dataset that CDS would return
        times = pd.date_range(START_DATE, END_DATE, freq='D')
        lats = np.arange(34.0, 31.5, -0.5)  # N->S
        lons = np.arange(-88.0, -85.5, 0.5)
        members = np.arange(2)

        # SEAS5 uses variable names that map via variable_map
        tmax_data = np.random.rand(len(members), len(times), len(lats), len(lons)).astype(np.float32) + 300
        tmin_data = np.random.rand(len(members), len(times), len(lats), len(lons)).astype(np.float32) + 290
        precip_data = np.random.rand(len(members), len(times), len(lats), len(lons)).astype(np.float32) * 0.01
        srad_data = np.random.rand(len(members), len(times), len(lats), len(lons)).astype(np.float32) * 1e6

        ds = xr.Dataset(
            {
                'maximum_2m_temperature_in_the_last_24_hours': (['number', 'time', 'latitude', 'longitude'], tmax_data),
                'minimum_2m_temperature_in_the_last_24_hours': (['number', 'time', 'latitude', 'longitude'], tmin_data),
                'total_precipitation': (['number', 'time', 'latitude', 'longitude'], precip_data),
                'surface_solar_radiation_downwards': (['number', 'time', 'latitude', 'longitude'], srad_data),
            },
            coords={
                'number': members,
                'time': times,
                'latitude': lats,
                'longitude': lons,
            },
        )

        # Mock CDS API client
        mock_cds_client = MagicMock()

        def mock_retrieve(product, params, nc_path):
            ds.to_netcdf(nc_path)

        mock_cds_client.retrieve = mock_retrieve

        # cdsapi is imported lazily inside the pipeline; it's an installed
        # dependency, so patch the real module's Client — the lazy
        # `import cdsapi` then picks up the mock.
        with patch('cdsapi.Client', return_value=mock_cds_client):
            pipeline._run_pipeline(fc, db_con, TEST_SCHEMA, 'seas5', BBOX, subs)

        # Check target variables (unit_conversions renames and multiplies)
        for target in ['tmax', 'tmin', 'rain', 'srad']:
            rows = _count_rows(db_con, TEST_SCHEMA, f'seas5_{target}')
            assert rows > 0, f'No rows for seas5_{target}'

        # Rain should have been multiplied by 1000
        ens_vals = _get_ens_values(db_con, TEST_SCHEMA, 'seas5_rain')
        assert len(ens_vals) >= 1


# ======================================================================
# 8. CFSv2 — grib2_bands
# ======================================================================
class TestCFSv2Integration:

    def test_full_pipeline(self, make_pipeline, db_con):
        """CFSv2: download GRIB2 -> read bands -> clip -> load."""
        fc = cfsv2_fetch_config()
        subs = cfsv2_subroutines()
        pipeline = make_pipeline(fc, subroutines=subs, table_prefix='cfsv2')

        # Create a multi-band GeoTIFF to mock a GRIB2 file
        # (rasterio can't write GRIB2, so we mock rasterio.open to return
        # something that looks like a GRIB2 with multiple bands)
        n_bands = 5
        height, width = 10, 10
        transform = from_bounds(-100, 25, -75, 45, width, height)

        # Build fake GRIB2 content (actually a multi-band GeoTIFF)
        with tempfile.NamedTemporaryFile(suffix='.tif', delete=False) as tmp:
            with rasterio.open(
                tmp.name, 'w', driver='GTiff',
                height=height, width=width,
                count=n_bands, dtype='float32', crs='EPSG:4326',
                transform=transform,
            ) as dst:
                for b in range(1, n_bands + 1):
                    data = np.random.rand(height, width).astype(np.float32) * 10
                    dst.write(data, b)
            grib_bytes = open(tmp.name, 'rb').read()
        os.unlink(tmp.name)

        # Mock iter_content for streaming response
        def mock_iter_content(chunk_size=8192):
            for i in range(0, len(grib_bytes), chunk_size):
                yield grib_bytes[i:i + chunk_size]

        # Mock requests.get: first attempt returns 200 with our fake data
        def mock_get(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.iter_content = mock_iter_content
            return resp

        # We also need to mock rasterio.open to add GRIB_VALID_TIME tags
        # since our GeoTIFF won't have them
        orig_rasterio_open = rasterio.open

        def patched_rasterio_open(path, *args, **kwargs):
            src = orig_rasterio_open(path, *args, **kwargs)
            if path.endswith('.grb2'):
                # Wrap to add GRIB-like tags
                original_tags = src.tags

                def mock_tags(band_idx=None):
                    if band_idx is not None:
                        # Generate valid_time based on band index
                        dt = START_DATE + datetime.timedelta(days=band_idx - 1)
                        ts = int(datetime.datetime.combine(
                            dt, datetime.time()
                        ).timestamp())
                        return {'GRIB_VALID_TIME': f'{ts} sec'}
                    return original_tags()

                src.tags = mock_tags
            return src

        with patch('data_agent.etl.etl_pipeline.requests.get', side_effect=mock_get), \
             patch('data_agent.etl.etl_pipeline.rasterio.open', side_effect=patched_rasterio_open):
            pipeline._run_pipeline(fc, db_con, TEST_SCHEMA, 'cfsv2', BBOX, subs)

        # CFSv2 unit_conversions: prate x 86400 -> rain, dswsfc x 86400 -> srad
        for target in ['tmax', 'tmin', 'rain', 'srad']:
            rows = _count_rows(db_con, TEST_SCHEMA, f'cfsv2_{target}')
            assert rows > 0, f'No rows for cfsv2_{target}'

        # Should have ensemble data (2 members)
        ens_vals = _get_ens_values(db_con, TEST_SCHEMA, 'cfsv2_rain')
        assert len(ens_vals) >= 1


# ======================================================================
# Cross-cutting: Transform correctness
# ======================================================================
class TestTransformIntegration:
    """
    Verify that unit_conversions are correctly applied end-to-end
    by checking actual values in PostGIS.
    """

    def test_multiply_conversion(self, make_pipeline, db_con):
        """Verify multiply operation produces correct values in PostGIS."""
        # Use a simple file_download config with custom unit_conversions
        fc = {
            'format': 'file_download',
            'file_format': 'tif',
            'variables': [{
                'target_variable': 'prate',
                'url_template': 'https://example.com/{YYYY}/{MM}/{DD}.tif',
            }],
        }
        subs = {
            'load_to_postgis': {'schema': TEST_SCHEMA, 'table_prefix': 'conv_test'},
            'unit_conversions': {
                'prate': {'operation': 'multiply', 'value': 86400, 'target_name': 'rain'},
            },
        }

        # Create a tiff with known values
        known_data = np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32)
        tiff_bytes = create_test_tiff_bytes(data=known_data, width=2, height=2, bbox=BBOX)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = tiff_bytes

        pipeline = make_pipeline(
            fc, subroutines=subs, table_prefix='conv_test',
            start_date=START_DATE, end_date=START_DATE,  # 1 day only
        )

        with patch('data_agent.etl.etl_pipeline.requests.get', return_value=mock_resp):
            pipeline._run_pipeline(fc, db_con, TEST_SCHEMA, 'conv_test', BBOX, subs)

        rows = _count_rows(db_con, TEST_SCHEMA, 'conv_test_rain')
        assert rows > 0, 'No rows loaded for conv_test_rain'

    def test_rename_only_conversion(self, make_pipeline, db_con):
        """Verify rename operation does not alter values."""
        fc = {
            'format': 'file_download',
            'file_format': 'tif',
            'variables': [{
                'target_variable': 'T2M_MAX',
                'url_template': 'https://example.com/{YYYY}/{MM}/{DD}.tif',
            }],
        }
        subs = {
            'load_to_postgis': {'schema': TEST_SCHEMA, 'table_prefix': 'rename_test'},
            'unit_conversions': {
                'T2M_MAX': {'operation': 'rename', 'target_name': 'tmax'},
            },
        }

        tiff_bytes = create_test_tiff_bytes(bbox=BBOX)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = tiff_bytes

        pipeline = make_pipeline(
            fc, subroutines=subs, table_prefix='rename_test',
            start_date=START_DATE, end_date=START_DATE,
        )

        with patch('data_agent.etl.etl_pipeline.requests.get', return_value=mock_resp):
            pipeline._run_pipeline(fc, db_con, TEST_SCHEMA, 'rename_test', BBOX, subs)

        rows = _count_rows(db_con, TEST_SCHEMA, 'rename_test_tmax')
        assert rows > 0, 'No rows loaded for rename_test_tmax'


# ======================================================================
# Cross-cutting: Load phase (GeoTIFF -> PostGIS)
# ======================================================================
class TestLoadIntegration:
    """Verify the Load phase correctly writes to PostGIS."""

    def test_load_observational(self, make_pipeline, db_con):
        """Load phase for observational data (ens=None)."""
        fc = chirps_fetch_config()
        pipeline = make_pipeline(fc, table_prefix='load_obs')

        data = np.random.rand(3, 3).astype(np.float32)
        transform = from_bounds(BBOX['west'], BBOX['south'], BBOX['east'], BBOX['north'], 3, 3)
        geo_info = {'transform': transform, 'width': 3, 'height': 3}

        with tempfile.TemporaryDirectory() as tmp_dir:
            pipeline._load_record(
                START_DATE, None, 'rain', data, geo_info,
                db_con, TEST_SCHEMA, 'load_obs', tmp_dir,
            )

        rows = _count_rows(db_con, TEST_SCHEMA, 'load_obs_rain')
        assert rows > 0

        dates = _get_dates(db_con, TEST_SCHEMA, 'load_obs_rain')
        assert START_DATE in dates

    def test_load_forecast(self, make_pipeline, db_con):
        """Load phase for forecast data (ens=integer)."""
        fc = cfsv2_fetch_config()
        pipeline = make_pipeline(fc, table_prefix='load_fc')

        data = np.random.rand(3, 3).astype(np.float32)
        transform = from_bounds(BBOX['west'], BBOX['south'], BBOX['east'], BBOX['north'], 3, 3)
        geo_info = {'transform': transform, 'width': 3, 'height': 3}

        with tempfile.TemporaryDirectory() as tmp_dir:
            pipeline._load_record(
                START_DATE, 1, 'rain', data, geo_info,
                db_con, TEST_SCHEMA, 'load_fc', tmp_dir,
            )
            pipeline._load_record(
                START_DATE, 2, 'rain', data, geo_info,
                db_con, TEST_SCHEMA, 'load_fc', tmp_dir,
            )

        ens_vals = _get_ens_values(db_con, TEST_SCHEMA, 'load_fc_rain')
        assert 1 in ens_vals
        assert 2 in ens_vals

    def test_load_with_lats_lons_geo_info(self, make_pipeline, db_con):
        """Load phase using lat/lon arrays instead of transform."""
        fc = nasa_power_fetch_config()
        pipeline = make_pipeline(fc, table_prefix='load_ll')

        lats = [34.0, 33.5, 33.0, 32.5, 32.0]
        lons = [-88.0, -87.5, -87.0, -86.5, -86.0]
        data = np.random.rand(len(lats), len(lons)).astype(np.float32)
        geo_info = {'lats': lats, 'lons': lons, 'resolution': 0.5}

        with tempfile.TemporaryDirectory() as tmp_dir:
            pipeline._load_record(
                START_DATE, None, 'tmax', data, geo_info,
                db_con, TEST_SCHEMA, 'load_ll', tmp_dir,
            )

        rows = _count_rows(db_con, TEST_SCHEMA, 'load_ll_tmax')
        assert rows > 0


# ======================================================================
# Cross-cutting: Discover -> already loaded filtering
# ======================================================================
class TestDiscoverFiltering:
    """
    Verify that each discover method correctly filters out already-loaded
    data from PostGIS.
    """

    def test_file_download_filters_loaded_dates(self, make_pipeline, db_con):
        """file_download discover should skip dates already in PostGIS."""
        fc = chirps_fetch_config()
        pipeline = make_pipeline(fc, table_prefix='disc_chirps')
        subs = {'load_to_postgis': {'schema': TEST_SCHEMA, 'table_prefix': 'disc_chirps'}}

        # First: discover with empty DB -> should get 3 days
        manifest_full = pipeline._discover_file_download(
            fc, BBOX, subs, db_con, TEST_SCHEMA, 'disc_chirps',
        )
        assert len(manifest_full) == 3  # 3 days x 1 variable

        # Load one day
        data = np.random.rand(3, 3).astype(np.float32)
        transform = from_bounds(BBOX['west'], BBOX['south'], BBOX['east'], BBOX['north'], 3, 3)
        geo_info = {'transform': transform, 'width': 3, 'height': 3}

        with tempfile.TemporaryDirectory() as tmp_dir:
            pipeline._load_record(
                START_DATE, None, 'rain', data, geo_info,
                db_con, TEST_SCHEMA, 'disc_chirps', tmp_dir,
            )

        # Re-discover: should now have 2 items (skip the loaded date)
        manifest_partial = pipeline._discover_file_download(
            fc, BBOX, subs, db_con, TEST_SCHEMA, 'disc_chirps',
        )
        assert len(manifest_partial) == 2

    def test_regional_json_tracks_loaded_dates(self, make_pipeline, db_con):
        """regional_json_api discover includes loaded_dates in manifest."""
        fc = nasa_power_fetch_config()
        subs = nasa_power_subroutines()
        pipeline = make_pipeline(fc, subroutines=subs, table_prefix='disc_power')

        manifest = pipeline._discover_regional_json_api(
            fc, BBOX, subs, db_con, TEST_SCHEMA, 'disc_power',
        )
        assert len(manifest) == 4  # 4 variables
        for item in manifest:
            assert 'loaded_dates' in item
            assert isinstance(item['loaded_dates'], set)
