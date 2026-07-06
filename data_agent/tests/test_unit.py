"""
Unit tests for every method in the ETL pipeline's config-driven
Discover -> Extract -> Transform -> Load architecture.

All external I/O (HTTP, PostGIS, filesystem) is mocked.
Run with:  python -m pytest data_agent/tests/test_unit.py -v
"""
import datetime
import gzip
import io
import os
import tempfile
import zipfile

import numpy as np
import pandas as pd
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
    seas5_fetch_config,
    cfsv2_fetch_config, cfsv2_subroutines,
)


# ======================================================================
# _substitute_date_placeholders
# ======================================================================
class TestSubstituteDatePlaceholders:

    def test_all_placeholders(self, pipeline):
        template = 'https://example.com/{YYYY}/{MM}/{DD}/{YYYYMMDD}.tif'
        dt = datetime.date(2024, 3, 15)
        result = pipeline._substitute_date_placeholders(template, dt)
        assert result == 'https://example.com/2024/03/15/20240315.tif'

    def test_partial_template(self, pipeline):
        template = 'https://example.com/{YYYY}/data.tif'
        dt = datetime.date(2024, 1, 1)
        result = pipeline._substitute_date_placeholders(template, dt)
        assert result == 'https://example.com/2024/data.tif'

    def test_no_placeholders(self, pipeline):
        template = 'https://example.com/static.tif'
        dt = datetime.date(2024, 1, 1)
        result = pipeline._substitute_date_placeholders(template, dt)
        assert result == template

    def test_zero_padded_month_day(self, pipeline):
        template = '{MM}-{DD}'
        dt = datetime.date(2024, 1, 5)
        assert pipeline._substitute_date_placeholders(template, dt) == '01-05'


# ======================================================================
# _apply_expand_bbox
# ======================================================================
class TestApplyExpandBbox:

    def test_expands_small_bbox(self, pipeline):
        bbox = {'west': -87.5, 'south': 33.0, 'east': -87.0, 'north': 33.5}
        subroutines = {'expand_bbox': {'min_range': 2.0}}
        result = pipeline._apply_expand_bbox(bbox, subroutines)
        assert float(result['north']) - float(result['south']) >= 2.0
        assert float(result['east']) - float(result['west']) >= 2.0

    def test_no_expansion_large_bbox(self, pipeline):
        bbox = {'west': -90, 'south': 30, 'east': -85, 'north': 35}
        subroutines = {'expand_bbox': {'min_range': 2.0}}
        result = pipeline._apply_expand_bbox(bbox, subroutines)
        assert result == bbox

    def test_none_bbox_returns_none(self, pipeline):
        result = pipeline._apply_expand_bbox(None, {})
        assert result is None

    def test_no_expand_subroutine_defaults_to_2(self, pipeline):
        bbox = {'west': -87.5, 'south': 33.0, 'east': -87.0, 'north': 33.5}
        result = pipeline._apply_expand_bbox(bbox, {})
        assert float(result['north']) - float(result['south']) >= 2.0

    def test_custom_min_range(self, pipeline):
        bbox = {'west': -88, 'south': 32, 'east': -86, 'north': 34}
        subroutines = {'expand_bbox': {'min_range': 5.0}}
        result = pipeline._apply_expand_bbox(bbox, subroutines)
        assert float(result['north']) - float(result['south']) >= 5.0


# ======================================================================
# _is_loaded
# ======================================================================
class TestIsLoaded:

    def test_returns_true_when_loaded(self, pipeline, mock_con):
        con, cur = mock_con
        cur.fetchone.return_value = (1,)
        assert pipeline._is_loaded(con, 'test', 'tbl', datetime.date(2024, 1, 1)) is True

    def test_returns_false_when_not_loaded(self, pipeline, mock_con):
        con, cur = mock_con
        cur.fetchone.return_value = None
        assert pipeline._is_loaded(con, 'test', 'tbl', datetime.date(2024, 1, 1)) is False

    def test_with_ens_parameter(self, pipeline, mock_con):
        con, cur = mock_con
        cur.fetchone.return_value = (1,)
        assert pipeline._is_loaded(con, 'test', 'tbl', datetime.date(2024, 1, 1), ens=3) is True
        # Verify ens was passed in the query
        call_args = cur.execute.call_args
        assert call_args[0][1] == (datetime.date(2024, 1, 1), 3)

    def test_returns_false_on_exception(self, pipeline, mock_con):
        con, cur = mock_con
        cur.execute.side_effect = Exception('DB error')
        assert pipeline._is_loaded(con, 'test', 'tbl', datetime.date(2024, 1, 1)) is False
        con.rollback.assert_called_once()


# ======================================================================
# _get_loaded_dates
# ======================================================================
class TestGetLoadedDates:

    def test_returns_dates(self, pipeline, mock_con):
        con, cur = mock_con
        cur.fetchall.return_value = [
            (datetime.date(2024, 1, 1),),
            (datetime.date(2024, 1, 2),),
        ]
        result = pipeline._get_loaded_dates(con, 'test', 'tbl')
        assert result == {datetime.date(2024, 1, 1), datetime.date(2024, 1, 2)}

    def test_with_ens_filter(self, pipeline, mock_con):
        con, cur = mock_con
        cur.fetchall.return_value = [(datetime.date(2024, 1, 1),)]
        pipeline._get_loaded_dates(con, 'test', 'tbl', ens=2)
        call_args = cur.execute.call_args
        assert call_args[0][1] == (2,)

    def test_returns_empty_on_exception(self, pipeline, mock_con):
        con, cur = mock_con
        cur.execute.side_effect = Exception('DB error')
        result = pipeline._get_loaded_dates(con, 'test', 'tbl')
        assert result == set()


# ======================================================================
# _convert_to_geotiff
# ======================================================================
class TestConvertToGeotiff:

    def test_tif_format(self, pipeline):
        tiff_bytes = create_test_tiff_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            result = pipeline._convert_to_geotiff(tiff_bytes, 'tif', tmp)
            assert os.path.exists(result)
            with rasterio.open(result) as src:
                assert src.count == 1

    def test_tif_gz_format(self, pipeline):
        tiff_bytes = create_test_tiff_bytes()
        gz_buf = io.BytesIO()
        with gzip.GzipFile(fileobj=gz_buf, mode='wb') as gz:
            gz.write(tiff_bytes)
        with tempfile.TemporaryDirectory() as tmp:
            result = pipeline._convert_to_geotiff(gz_buf.getvalue(), 'tif_gz', tmp)
            assert os.path.exists(result)
            with rasterio.open(result) as src:
                assert src.count == 1

    def test_zip_bil_format(self, pipeline):
        # Create a minimal BIL archive using rasterio
        with tempfile.TemporaryDirectory() as work_dir:
            bil_path = os.path.join(work_dir, 'data.bil')
            data = np.arange(9, dtype=np.float32).reshape(3, 3)
            transform = from_bounds(-88, 32, -86, 34, 3, 3)
            with rasterio.open(
                bil_path, 'w', driver='EHdr',
                height=3, width=3, count=1, dtype='float32',
                crs='EPSG:4326', transform=transform,
            ) as dst:
                dst.write(data, 1)

            # Package into a zip archive
            zip_buf = io.BytesIO()
            with zipfile.ZipFile(zip_buf, 'w') as zf:
                for fname in os.listdir(work_dir):
                    zf.write(os.path.join(work_dir, fname), fname)

        with tempfile.TemporaryDirectory() as tmp:
            result = pipeline._convert_to_geotiff(zip_buf.getvalue(), 'zip_bil', tmp)
            assert os.path.exists(result)
            assert result.endswith('.tif')

    def test_unsupported_format_raises(self, pipeline):
        with tempfile.TemporaryDirectory() as tmp:
            with pytest.raises(ValueError, match='Unsupported download format'):
                pipeline._convert_to_geotiff(b'data', 'unknown_fmt', tmp)


# ======================================================================
# _clip_raster
# ======================================================================
class TestClipRaster:

    def test_clips_with_gdalwarp(self, pipeline):
        with tempfile.TemporaryDirectory() as tmp:
            tiff_path = os.path.join(tmp, 'input.tif')
            create_test_tiff(tiff_path, width=10, height=10)
            result = pipeline._clip_raster(tiff_path, BBOX, tmp)
            assert os.path.exists(result)

    @patch('data_agent.etl.etl_pipeline.subprocess.Popen')
    def test_fallback_on_gdalwarp_failure(self, mock_popen, pipeline):
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (b'', None)
        mock_popen.return_value = mock_proc
        with tempfile.TemporaryDirectory() as tmp:
            tiff_path = os.path.join(tmp, 'input.tif')
            create_test_tiff(tiff_path)
            # gdalwarp doesn't create the output file -> falls back to original
            result = pipeline._clip_raster(tiff_path, BBOX, tmp)
            assert result == tiff_path


# ======================================================================
# _transform_record
# ======================================================================
class TestTransformRecord:

    def test_rename_operation(self, pipeline, sample_grid_3x3):
        unit_conversions = {'src': {'operation': 'rename', 'target_name': 'dst'}}
        target, data = pipeline._transform_record('src', sample_grid_3x3.copy(), unit_conversions, {})
        assert target == 'dst'
        np.testing.assert_array_equal(data, sample_grid_3x3)

    def test_multiply_operation(self, pipeline, sample_grid_3x3):
        unit_conversions = {'prate': {'operation': 'multiply', 'value': 86400, 'target_name': 'rain'}}
        target, data = pipeline._transform_record('prate', sample_grid_3x3.copy(), unit_conversions, {})
        assert target == 'rain'
        np.testing.assert_allclose(data, sample_grid_3x3 * 86400)

    def test_divide_operation(self, pipeline, sample_grid_3x3):
        unit_conversions = {'srad': {'operation': 'divide', 'value': 86400, 'target_name': 'srad_out'}}
        target, data = pipeline._transform_record('srad', sample_grid_3x3.copy(), unit_conversions, {})
        assert target == 'srad_out'
        np.testing.assert_allclose(data, sample_grid_3x3 / 86400)

    def test_add_operation(self, pipeline, sample_grid_3x3):
        unit_conversions = {'temp': {'operation': 'add', 'value': -273.15, 'target_name': 'temp_c'}}
        target, data = pipeline._transform_record('temp', sample_grid_3x3.copy(), unit_conversions, {})
        assert target == 'temp_c'
        np.testing.assert_allclose(data, sample_grid_3x3 + (-273.15))

    def test_subtract_operation(self, pipeline, sample_grid_3x3):
        unit_conversions = {'x': {'operation': 'subtract', 'value': 10, 'target_name': 'y'}}
        target, data = pipeline._transform_record('x', sample_grid_3x3.copy(), unit_conversions, {})
        assert target == 'y'
        np.testing.assert_allclose(data, sample_grid_3x3 - 10)

    def test_no_conversion_uses_variable_map(self, pipeline, sample_grid_3x3):
        variable_map = {'api_temp': 'tmax'}
        target, data = pipeline._transform_record('api_temp', sample_grid_3x3.copy(), {}, variable_map)
        assert target == 'tmax'

    def test_no_conversion_no_map_returns_source_var(self, pipeline, sample_grid_3x3):
        target, data = pipeline._transform_record('raw_var', sample_grid_3x3.copy(), {}, {})
        assert target == 'raw_var'

    def test_all_nan_returns_none(self, pipeline, nan_grid_3x3):
        target, data = pipeline._transform_record('var', nan_grid_3x3.copy(), {}, {})
        assert target is None
        assert data is None

    def test_target_name_priority_over_variable_map(self, pipeline, sample_grid_3x3):
        unit_conversions = {'src': {'operation': 'rename', 'target_name': 'from_conv'}}
        variable_map = {'src': 'from_map'}
        target, _ = pipeline._transform_record('src', sample_grid_3x3.copy(), unit_conversions, variable_map)
        assert target == 'from_conv'

    def test_conv_without_target_name_uses_variable_map(self, pipeline, sample_grid_3x3):
        unit_conversions = {'src': {'operation': 'multiply', 'value': 2}}
        variable_map = {'src': 'mapped'}
        target, _ = pipeline._transform_record('src', sample_grid_3x3.copy(), unit_conversions, variable_map)
        assert target == 'mapped'


# ======================================================================
# _discover_file_download
# ======================================================================
class TestDiscoverFileDownload:

    def test_generates_manifest_for_date_range(self, pipeline, mock_con):
        con, cur = mock_con
        cfg = chirps_fetch_config()
        manifest = pipeline._discover_file_download(cfg, BBOX, {}, con, 'test', 'chirps')
        # 3 days x 1 variable = 3 items
        assert len(manifest) == 3
        assert all(item['target_var'] == 'rain' for item in manifest)
        assert all(item['file_format'] == 'tif_gz' for item in manifest)

    def test_skips_loaded_dates(self, pipeline, mock_con):
        con, cur = mock_con
        cfg = chirps_fetch_config()
        # First of the 3 dates already loaded, the other two not. Mock at the
        # _is_loaded level so the test is robust to how many cursor calls the
        # discovery query makes (it also calls _ensure_table_exists per date).
        with patch.object(pipeline, '_is_loaded', side_effect=[True, False, False]):
            manifest = pipeline._discover_file_download(cfg, BBOX, {}, con, 'test', 'chirps')
        assert len(manifest) == 2

    def test_url_formatting(self, pipeline, mock_con):
        con, cur = mock_con
        cfg = chirps_fetch_config()
        manifest = pipeline._discover_file_download(cfg, BBOX, {}, con, 'test', 'chirps')
        url = manifest[0]['url']
        assert '2024' in url
        assert '01' in url

    def test_multiple_variables(self, pipeline, mock_con):
        con, cur = mock_con
        cfg = chirts_fetch_config()
        manifest = pipeline._discover_file_download(cfg, BBOX, {}, con, 'test', 'chirts')
        # 3 days x 2 variables = 6 items
        assert len(manifest) == 6
        target_vars = {item['target_var'] for item in manifest}
        assert target_vars == {'tmax', 'tmin'}

    def test_empty_date_range(self, pipeline, mock_con, mock_dataset, mock_stdout, mock_style):
        con, cur = mock_con
        p = ETL_Pipeline(mock_dataset, mock_stdout, mock_style,
                         start_date=datetime.date(2024, 1, 5),
                         end_date=datetime.date(2024, 1, 3))
        cfg = chirps_fetch_config()
        manifest = p._discover_file_download(cfg, BBOX, {}, con, 'test', 'chirps')
        assert len(manifest) == 0


# ======================================================================
# _discover_regional_json_api
# ======================================================================
class TestDiscoverRegionalJsonApi:

    def test_generates_manifest_per_variable(self, pipeline, mock_con):
        con, cur = mock_con
        cfg = nasa_power_fetch_config()
        subs = nasa_power_subroutines()
        manifest = pipeline._discover_regional_json_api(cfg, BBOX, subs, con, 'test', 'power')
        assert len(manifest) == 4  # 4 variables
        source_vars = {item['source_var'] for item in manifest}
        assert source_vars == {'T2M_MAX', 'T2M_MIN', 'PRECTOTCORR', 'ALLSKY_SFC_SW_DWN'}

    def test_bbox_expansion_applied(self, pipeline, mock_con):
        con, cur = mock_con
        small_bbox = {'west': -87.5, 'south': 33.0, 'east': -87.0, 'north': 33.5}
        cfg = nasa_power_fetch_config()
        subs = nasa_power_subroutines()
        manifest = pipeline._discover_regional_json_api(cfg, small_bbox, subs, con, 'test', 'power')
        api_bbox = manifest[0]['api_bbox']
        assert float(api_bbox['north']) - float(api_bbox['south']) >= 2.0

    def test_no_bbox_returns_empty(self, pipeline, mock_con):
        con, cur = mock_con
        cfg = nasa_power_fetch_config()
        subs = nasa_power_subroutines()
        manifest = pipeline._discover_regional_json_api(cfg, None, subs, con, 'test', 'power')
        assert manifest == []

    def test_url_contains_variable(self, pipeline, mock_con):
        con, cur = mock_con
        cfg = nasa_power_fetch_config()
        subs = nasa_power_subroutines()
        manifest = pipeline._discover_regional_json_api(cfg, BBOX, subs, con, 'test', 'power')
        for item in manifest:
            assert item['source_var'] in item['url']


# ======================================================================
# _discover_point_api_grid
# ======================================================================
class TestDiscoverPointApiGrid:

    def test_generates_grid_points(self, pipeline, mock_con):
        con, cur = mock_con
        cfg = open_meteo_fetch_config()
        manifest = pipeline._discover_point_api_grid(cfg, BBOX, {}, con, 'test', 'om')
        # bbox 2 deg wide x 2 deg tall at 0.25 deg res -> 9 x 9 = 81 points
        assert len(manifest) == 81

    def test_url_contains_lat_lon(self, pipeline, mock_con):
        con, cur = mock_con
        cfg = open_meteo_fetch_config()
        manifest = pipeline._discover_point_api_grid(cfg, BBOX, {}, con, 'test', 'om')
        item = manifest[0]
        assert str(item['lat']) in item['url']
        assert str(item['lon']) in item['url']

    def test_no_bbox_returns_empty(self, pipeline, mock_con):
        con, cur = mock_con
        cfg = open_meteo_fetch_config()
        manifest = pipeline._discover_point_api_grid(cfg, None, {}, con, 'test', 'om')
        assert manifest == []

    def test_custom_grid_resolution(self, pipeline, mock_con):
        con, cur = mock_con
        cfg = open_meteo_fetch_config()
        cfg['grid_resolution'] = 1.0
        manifest = pipeline._discover_point_api_grid(cfg, BBOX, {}, con, 'test', 'om')
        # 2 deg wide at 1.0 deg -> 3 lons, 2 deg tall at 1.0 deg -> 3 lats -> 9 points
        assert len(manifest) == 9


# ======================================================================
# _discover_xarray_slice
# ======================================================================
class TestDiscoverXarraySlice:

    def test_opendap_manifest(self, pipeline, mock_con):
        con, cur = mock_con
        cfg = nmme_fetch_config()
        manifest = pipeline._discover_xarray_slice(cfg, BBOX, {}, con, 'test', 'nmme')
        # 1 model x 3 variables = 3 items
        assert len(manifest) == 3
        assert all(item['transport'] == 'opendap' for item in manifest)

    def test_cds_api_manifest(self, pipeline, mock_con):
        con, cur = mock_con
        cfg = seas5_fetch_config()
        manifest = pipeline._discover_xarray_slice(cfg, BBOX, {}, con, 'test', 'seas5')
        assert len(manifest) == 1
        assert manifest[0]['transport'] == 'cds_api'
        assert 'cds_product' in manifest[0]

    def test_opendap_multiple_models(self, pipeline, mock_con):
        con, cur = mock_con
        cfg = nmme_fetch_config()
        cfg['models'] = ['CCSM4', 'GEM_NEMO']
        manifest = pipeline._discover_xarray_slice(cfg, BBOX, {}, con, 'test', 'nmme')
        assert len(manifest) == 6  # 2 models x 3 vars


# ======================================================================
# _discover_grib2_bands
# ======================================================================
class TestDiscoverGrib2Bands:

    def test_generates_manifest(self, pipeline, mock_con):
        con, cur = mock_con
        cfg = cfsv2_fetch_config()
        manifest = pipeline._discover_grib2_bands(cfg, BBOX, {}, con, 'test', 'cfsv2')
        # 2 members x 1 init_hour x 4 variables = 8 items
        assert len(manifest) == 8

    def test_no_bbox_returns_empty(self, pipeline, mock_con):
        con, cur = mock_con
        cfg = cfsv2_fetch_config()
        manifest = pipeline._discover_grib2_bands(cfg, None, {}, con, 'test', 'cfsv2')
        assert manifest == []

    def test_manifest_item_contents(self, pipeline, mock_con):
        con, cur = mock_con
        cfg = cfsv2_fetch_config()
        manifest = pipeline._discover_grib2_bands(cfg, BBOX, {}, con, 'test', 'cfsv2')
        item = manifest[0]
        assert 'url' in item
        assert 'member' in item
        assert 'grib_var' in item
        assert 'init_date' in item
        assert 'base_url' in item


# ======================================================================
# _extract_file_download
# ======================================================================
class TestExtractFileDownload:

    @patch('data_agent.etl.etl_pipeline.requests.get')
    def test_yields_records(self, mock_get, pipeline):
        tiff_bytes = create_test_tiff_bytes()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = tiff_bytes
        mock_get.return_value = mock_resp

        manifest = [
            {'date': START_DATE, 'target_var': 'rain', 'url': 'http://x', 'file_format': 'tif'},
        ]
        records = list(pipeline._extract_file_download(manifest, {}, None, {}))
        assert len(records) == 1
        date, ens, src_var, data, geo_info = records[0]
        assert date == START_DATE
        assert ens is None
        assert src_var == 'rain'
        assert data.shape == (3, 3)

    @patch('data_agent.etl.etl_pipeline.requests.get')
    def test_skips_http_errors(self, mock_get, pipeline):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_get.return_value = mock_resp

        manifest = [
            {'date': START_DATE, 'target_var': 'rain', 'url': 'http://x', 'file_format': 'tif'},
        ]
        records = list(pipeline._extract_file_download(manifest, {}, None, {}))
        assert len(records) == 0

    @patch('data_agent.etl.etl_pipeline.requests.get')
    def test_continues_on_exception(self, mock_get, pipeline):
        mock_get.side_effect = Exception('Network error')
        manifest = [
            {'date': START_DATE, 'target_var': 'rain', 'url': 'http://x', 'file_format': 'tif'},
        ]
        records = list(pipeline._extract_file_download(manifest, {}, None, {}))
        assert len(records) == 0


# ======================================================================
# _extract_regional_json_api
# ======================================================================
class TestExtractRegionalJsonApi:

    @patch('data_agent.etl.etl_pipeline.requests.get')
    def test_yields_grid_records(self, mock_get, pipeline):
        dates = [START_DATE, START_DATE + datetime.timedelta(days=1)]
        resp_data = make_nasa_power_response(
            'T2M_MAX',
            lons=[-88.0, -87.5, -87.0],
            lats=[34.0, 33.5, 33.0],
            dates=dates,
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = resp_data
        mock_get.return_value = mock_resp

        cfg = nasa_power_fetch_config()
        manifest = [{
            'source_var': 'T2M_MAX', 'url': 'http://x',
            'loaded_dates': set(), 'api_bbox': BBOX,
        }]
        records = list(pipeline._extract_regional_json_api(manifest, cfg, BBOX, {}))
        assert len(records) == 2  # 2 dates
        for date, ens, src_var, data, geo_info in records:
            assert ens is None
            assert src_var == 'T2M_MAX'
            assert data.ndim == 2
            assert 'lats' in geo_info

    @patch('data_agent.etl.etl_pipeline.requests.get')
    def test_skips_loaded_dates(self, mock_get, pipeline):
        dates = [START_DATE]
        resp_data = make_nasa_power_response('T2M_MAX', [-88.0], [34.0], dates)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = resp_data
        mock_get.return_value = mock_resp

        cfg = nasa_power_fetch_config()
        manifest = [{
            'source_var': 'T2M_MAX', 'url': 'http://x',
            'loaded_dates': {START_DATE}, 'api_bbox': BBOX,
        }]
        records = list(pipeline._extract_regional_json_api(manifest, cfg, BBOX, {}))
        assert len(records) == 0

    @patch('data_agent.etl.etl_pipeline.requests.get')
    def test_filters_nodata(self, mock_get, pipeline):
        dates = [START_DATE]
        resp_data = make_nasa_power_response('T2M_MAX', [-88.0], [34.0], dates, values=[-999])
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = resp_data
        mock_get.return_value = mock_resp

        cfg = nasa_power_fetch_config()
        manifest = [{
            'source_var': 'T2M_MAX', 'url': 'http://x',
            'loaded_dates': set(), 'api_bbox': BBOX,
        }]
        records = list(pipeline._extract_regional_json_api(manifest, cfg, BBOX, {}))
        # Record is yielded but grid is all NaN (value == nodata_value)
        assert len(records) == 0 or np.all(np.isnan(records[0][3]))


# ======================================================================
# _extract_point_api_grid
# ======================================================================
class TestExtractPointApiGrid:

    @patch('data_agent.etl.etl_pipeline.requests.get')
    def test_assembles_grid(self, mock_get, pipeline):
        dates = [START_DATE]
        variable_map = {'temperature_2m_max': 'tmax'}
        resp = make_open_meteo_response(variable_map, dates, n_members=1)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = resp
        mock_get.return_value = mock_resp

        cfg = open_meteo_fetch_config()
        cfg['variable_map'] = variable_map
        manifest = [
            {'lat': 34.0, 'lon': -88.0, 'url': 'http://x'},
            {'lat': 34.0, 'lon': -87.75, 'url': 'http://x'},
            {'lat': 33.75, 'lon': -88.0, 'url': 'http://x'},
            {'lat': 33.75, 'lon': -87.75, 'url': 'http://x'},
        ]
        records = list(pipeline._extract_point_api_grid(manifest, cfg, BBOX, {}))
        assert len(records) >= 1
        date, ens, src_var, data, geo_info = records[0]
        assert date == START_DATE
        assert ens == 1
        assert data.shape == (2, 2)

    @patch('data_agent.etl.etl_pipeline.requests.get')
    def test_handles_ensemble_members(self, mock_get, pipeline):
        dates = [START_DATE]
        variable_map = {'temperature_2m_max': 'tmax'}
        resp = make_open_meteo_response(variable_map, dates, n_members=3)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = resp
        mock_get.return_value = mock_resp

        cfg = open_meteo_fetch_config()
        cfg['variable_map'] = variable_map
        manifest = [{'lat': 34.0, 'lon': -88.0, 'url': 'http://x'}]
        records = list(pipeline._extract_point_api_grid(manifest, cfg, BBOX, {}))
        # 1 date x 3 members x 1 variable = 3 records
        assert len(records) == 3
        ens_nums = {r[1] for r in records}
        assert ens_nums == {1, 2, 3}


# ======================================================================
# _extract_xarray_slices  (opendap branch)
# ======================================================================
class TestExtractXarraySlicesOpendap:

    def _make_test_dataset(self, var_name='tmax', n_time=3, n_ens=0):
        times = pd.date_range('2024-01-01', periods=n_time, freq='D')
        lats = np.array([34.0, 33.0, 32.0])
        lons = np.array([-88.0, -87.0, -86.0])
        data = np.random.rand(n_time, 3, 3).astype(np.float32)
        coords = {'time': times, 'lat': lats, 'lon': lons}
        dims = ['time', 'lat', 'lon']
        if n_ens > 0:
            data = np.random.rand(n_ens, n_time, 3, 3).astype(np.float32)
            coords['ensemble'] = np.arange(n_ens)
            dims = ['ensemble', 'time', 'lat', 'lon']
        return xr.Dataset({var_name: (dims, data)}, coords=coords)

    @patch('data_agent.etl.etl_pipeline.xr.open_dataset')
    def test_yields_records(self, mock_open, pipeline):
        ds = self._make_test_dataset('tmax', n_time=3)
        mock_open.return_value = ds

        manifest = [{'url': 'http://x', 'model': 'M', 'source_var': 'tmax', 'transport': 'opendap'}]
        cfg = nmme_fetch_config()
        records = list(pipeline._extract_xarray_slices(manifest, cfg, BBOX, {}))
        assert len(records) == 3  # 3 timesteps
        for dt, ens, src_var, data_2d, geo_info in records:
            assert src_var == 'tmax'
            assert data_2d.shape == (3, 3)

    @patch('data_agent.etl.etl_pipeline.xr.open_dataset')
    def test_handles_ensemble_dim(self, mock_open, pipeline):
        ds = self._make_test_dataset('tmax', n_time=2, n_ens=2)
        mock_open.return_value = ds

        manifest = [{'url': 'http://x', 'model': 'M', 'source_var': 'tmax', 'transport': 'opendap'}]
        cfg = nmme_fetch_config()
        records = list(pipeline._extract_xarray_slices(manifest, cfg, BBOX, {}))
        # 2 ensemble x 2 timesteps = 4
        assert len(records) == 4

    @patch('data_agent.etl.etl_pipeline.xr.open_dataset')
    def test_flips_ascending_lats(self, mock_open, pipeline):
        times = pd.date_range('2024-01-01', periods=1, freq='D')
        lats = np.array([32.0, 33.0, 34.0])  # ascending
        lons = np.array([-88.0, -87.0, -86.0])
        data = np.arange(9, dtype=np.float32).reshape(1, 3, 3)
        ds = xr.Dataset({'tmax': (['time', 'lat', 'lon'], data)},
                        coords={'time': times, 'lat': lats, 'lon': lons})
        mock_open.return_value = ds

        manifest = [{'url': 'http://x', 'model': 'M', 'source_var': 'tmax', 'transport': 'opendap'}]
        cfg = nmme_fetch_config()
        records = list(pipeline._extract_xarray_slices(manifest, cfg, BBOX, {}))
        assert len(records) == 1
        _, _, _, data_2d, geo_info = records[0]
        # Lats should be descending (N->S) after flip
        assert geo_info['lats'][0] > geo_info['lats'][-1]

    @patch('data_agent.etl.etl_pipeline.xr.open_dataset')
    def test_skips_failed_dataset(self, mock_open, pipeline):
        mock_open.side_effect = Exception('Connection refused')
        manifest = [{'url': 'http://x', 'model': 'M', 'source_var': 'tmax', 'transport': 'opendap'}]
        cfg = nmme_fetch_config()
        records = list(pipeline._extract_xarray_slices(manifest, cfg, BBOX, {}))
        assert len(records) == 0


# ======================================================================
# _extract_grib2_bands
# ======================================================================
class TestExtractGrib2Bands:

    def _make_multiband_tiff(self, tmp_dir, n_bands=3):
        """Create a multi-band GeoTIFF as stand-in for GRIB2."""
        path = os.path.join(tmp_dir, 'test.tif')
        data = np.random.rand(n_bands, 10, 10).astype(np.float32)
        transform = from_bounds(-90, 30, -80, 40, 10, 10)
        with rasterio.open(
            path, 'w', driver='GTiff',
            height=10, width=10, count=n_bands,
            dtype='float32', crs='EPSG:4326', transform=transform,
        ) as dst:
            dst.write(data)
        return path, data

    @patch('data_agent.etl.etl_pipeline.requests.get')
    def test_yields_per_band_records(self, mock_get, pipeline):
        with tempfile.TemporaryDirectory() as tmp:
            tiff_path, expected_data = self._make_multiband_tiff(tmp, n_bands=3)
            with open(tiff_path, 'rb') as f:
                tiff_bytes = f.read()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_content = MagicMock(return_value=[tiff_bytes])
        mock_get.return_value = mock_resp

        init_date = datetime.date(2024, 1, 1)
        manifest = [{
            'url': 'http://x', 'member': 1, 'grib_var': 'prate',
            'init_date': init_date, 'init_hour': '00',
            'base_url': 'http://base',
        }]
        cfg = cfsv2_fetch_config()
        records = list(pipeline._extract_grib2_bands(manifest, cfg, BBOX, {}))
        # All 3 bands fall within start_date..end_date (init_date+0, +1, +2)
        assert len(records) == 3
        for dt, member, grib_var, data_2d, geo_info in records:
            assert member == 1
            assert grib_var == 'prate'

    @patch('data_agent.etl.etl_pipeline.requests.get')
    def test_falls_back_to_older_init_date(self, mock_get, pipeline):
        tiff_bytes = create_test_tiff_bytes(width=10, height=10)

        # First request 404, second succeeds
        resp_404 = MagicMock()
        resp_404.status_code = 404
        resp_ok = MagicMock()
        resp_ok.status_code = 200
        resp_ok.iter_content = MagicMock(return_value=[tiff_bytes])
        mock_get.side_effect = [resp_404, resp_ok]

        init_date = datetime.date(2024, 1, 1)
        manifest = [{
            'url': 'http://x', 'member': 1, 'grib_var': 'tmax',
            'init_date': init_date, 'init_hour': '00',
            'base_url': 'http://base',
        }]
        cfg = cfsv2_fetch_config()
        records = list(pipeline._extract_grib2_bands(manifest, cfg, BBOX, {}))
        assert len(records) >= 1


# ======================================================================
# _load_record
# ======================================================================
class TestLoadRecord:

    def test_observational_load_calls_ensure_table(self, pipeline, mock_con, sample_grid_3x3):
        con, cur = mock_con
        geo_info = {'lats': [34.0, 33.0, 32.0], 'lons': [-88.0, -87.0, -86.0], 'resolution': 1.0}
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(pipeline, '_load_tiff_to_postgis') as mock_load:
                with patch.object(pipeline, '_ensure_table_exists') as mock_ensure:
                    pipeline._load_record(
                        START_DATE, None, 'rain', sample_grid_3x3, geo_info,
                        con, 'test', 'pfx', tmp,
                    )
                    mock_ensure.assert_called_once()
                    mock_load.assert_called_once()
                    # Verify ens=None
                    assert mock_load.call_args[1].get('ens') is None

    def test_forecast_load_calls_ensure_forecast_table(self, pipeline, mock_con, sample_grid_3x3):
        con, cur = mock_con
        geo_info = {'lats': [34.0, 33.0, 32.0], 'lons': [-88.0, -87.0, -86.0], 'resolution': 1.0}
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(pipeline, '_load_tiff_to_postgis') as mock_load:
                with patch.object(pipeline, '_ensure_forecast_table_exists') as mock_ensure:
                    pipeline._load_record(
                        START_DATE, 3, 'rain', sample_grid_3x3, geo_info,
                        con, 'test', 'pfx', tmp,
                    )
                    mock_ensure.assert_called_once()
                    assert mock_load.call_args[1].get('ens') == 3

    def test_dedup_skips_loaded(self, pipeline, mock_con, sample_grid_3x3):
        con, cur = mock_con
        geo_info = {'lats': [34.0, 33.0, 32.0], 'lons': [-88.0, -87.0, -86.0], 'resolution': 1.0}
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(pipeline, '_is_loaded', return_value=True):
                with patch.object(pipeline, '_load_tiff_to_postgis') as mock_load:
                    pipeline._load_record(
                        START_DATE, None, 'rain', sample_grid_3x3, geo_info,
                        con, 'test', 'pfx', tmp,
                    )
                    mock_load.assert_not_called()

    def test_transform_geo_info(self, pipeline, mock_con, sample_grid_3x3):
        con, cur = mock_con
        transform = from_bounds(-88, 32, -86, 34, 3, 3)
        geo_info = {'transform': transform, 'width': 3, 'height': 3}
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(pipeline, '_load_tiff_to_postgis') as mock_load:
                pipeline._load_record(
                    START_DATE, None, 'rain', sample_grid_3x3, geo_info,
                    con, 'test', 'pfx', tmp,
                )
                tiff_path = mock_load.call_args[0][0]
                assert os.path.exists(tiff_path)


# ======================================================================
# _run_pipeline
# ======================================================================
class TestRunPipeline:

    def test_dispatches_to_correct_methods(self, pipeline, mock_con):
        con, cur = mock_con
        fetch_config = {'format': 'file_download', 'variables': [], 'file_format': 'tif'}
        with patch.object(pipeline, '_discover_file_download', return_value=[]) as mock_disc:
            pipeline._run_pipeline(fetch_config, con, 'test', 'pfx', BBOX, {})
            mock_disc.assert_called_once()

    def test_empty_manifest_skips_extract(self, pipeline, mock_con):
        con, cur = mock_con
        fetch_config = {'format': 'file_download', 'variables': [], 'file_format': 'tif'}
        with patch.object(pipeline, '_discover_file_download', return_value=[]):
            with patch.object(pipeline, '_extract_file_download') as mock_ext:
                pipeline._run_pipeline(fetch_config, con, 'test', 'pfx', BBOX, {})
                mock_ext.assert_not_called()

    def test_chains_extract_transform_load(self, pipeline, mock_con, sample_grid_3x3):
        con, cur = mock_con
        geo_info = {'lats': [34.0, 33.0, 32.0], 'lons': [-88.0, -87.0, -86.0], 'resolution': 1.0}
        fake_record = (START_DATE, None, 'src_var', sample_grid_3x3, geo_info)

        fetch_config = {
            'format': 'file_download',
            'variables': [],
            'file_format': 'tif',
            'variable_map': {'src_var': 'tgt_var'},
        }
        subroutines = {}

        with patch.object(pipeline, '_discover_file_download', return_value=['item']):
            with patch.object(pipeline, '_extract_file_download', return_value=[fake_record]):
                with patch.object(pipeline, '_load_record') as mock_load:
                    pipeline._run_pipeline(fetch_config, con, 'test', 'pfx', BBOX, subroutines)
                    mock_load.assert_called_once()
                    # Verify transform applied: src_var -> tgt_var via variable_map
                    call_args = mock_load.call_args
                    assert call_args[0][2] == 'tgt_var'  # target_var

    def test_transform_filters_nan(self, pipeline, mock_con, nan_grid_3x3):
        con, cur = mock_con
        geo_info = {'lats': [34.0, 33.0, 32.0], 'lons': [-88.0, -87.0, -86.0], 'resolution': 1.0}
        fake_record = (START_DATE, None, 'var', nan_grid_3x3, geo_info)

        fetch_config = {'format': 'file_download', 'variables': [], 'file_format': 'tif'}

        with patch.object(pipeline, '_discover_file_download', return_value=['item']):
            with patch.object(pipeline, '_extract_file_download', return_value=[fake_record]):
                with patch.object(pipeline, '_load_record') as mock_load:
                    pipeline._run_pipeline(fetch_config, con, 'test', 'pfx', BBOX, {})
                    mock_load.assert_not_called()

    def test_unit_conversions_applied(self, pipeline, mock_con, sample_grid_3x3):
        con, cur = mock_con
        geo_info = {'lats': [34.0, 33.0, 32.0], 'lons': [-88.0, -87.0, -86.0], 'resolution': 1.0}
        fake_record = (START_DATE, 1, 'prate', sample_grid_3x3, geo_info)

        fetch_config = {'format': 'grib2_bands', 'base_url': 'http://x', 'variable_map': {}}
        subroutines = {
            'unit_conversions': {
                'prate': {'operation': 'multiply', 'value': 86400, 'target_name': 'rain'},
            },
        }

        with patch.object(pipeline, '_discover_grib2_bands', return_value=['item']):
            with patch.object(pipeline, '_extract_grib2_bands', return_value=[fake_record]):
                with patch.object(pipeline, '_load_record') as mock_load:
                    pipeline._run_pipeline(fetch_config, con, 'test', 'pfx', BBOX, subroutines)
                    call_args = mock_load.call_args[0]
                    assert call_args[2] == 'rain'  # target_var
                    np.testing.assert_allclose(call_args[3], sample_grid_3x3 * 86400)


# ======================================================================
# _direct_fetch_and_load  (entry point)
# ======================================================================
class TestDirectFetchAndLoad:

    def test_calls_run_pipeline(self, pipeline):
        with patch.object(pipeline, '_get_postgis_connection') as mock_conn:
            mock_con = MagicMock()
            mock_conn.return_value = mock_con
            with patch.object(pipeline, '_run_pipeline') as mock_run:
                with patch.object(pipeline, 'set_dates'):
                    pipeline._direct_fetch_and_load()
                    mock_run.assert_called_once()

    def test_handles_tuple_bbox(self, mock_dataset, mock_stdout, mock_style):
        p = ETL_Pipeline(
            mock_dataset, mock_stdout, mock_style,
            start_date=START_DATE, end_date=END_DATE,
            bbox=(-88, 32, -86, 34),
        )
        with patch.object(p, '_get_postgis_connection') as mock_conn:
            mock_conn.return_value = MagicMock()
            with patch.object(p, '_run_pipeline') as mock_run:
                with patch.object(p, 'set_dates'):
                    p._direct_fetch_and_load()
                    call_args = mock_run.call_args
                    bbox_arg = call_args[0][4]
                    assert isinstance(bbox_arg, dict)
                    assert bbox_arg['west'] == -88

    def test_closes_connection(self, pipeline):
        mock_con = MagicMock()
        with patch.object(pipeline, '_get_postgis_connection', return_value=mock_con):
            with patch.object(pipeline, '_run_pipeline'):
                with patch.object(pipeline, 'set_dates'):
                    pipeline._direct_fetch_and_load()
                    mock_con.close.assert_called_once()


# ======================================================================
# Class-level dispatch dicts
# ======================================================================
class TestDispatchDicts:

    def test_discovery_methods_all_exist(self, pipeline):
        for fmt, method_name in ETL_Pipeline.DISCOVERY_METHODS.items():
            assert hasattr(pipeline, method_name), f'Missing discover method: {method_name}'

    def test_extract_iterators_all_exist(self, pipeline):
        for fmt, method_name in ETL_Pipeline.EXTRACT_ITERATORS.items():
            assert hasattr(pipeline, method_name), f'Missing extract method: {method_name}'

    def test_formats_match(self):
        assert set(ETL_Pipeline.DISCOVERY_METHODS.keys()) == set(ETL_Pipeline.EXTRACT_ITERATORS.keys())

    def test_expected_formats_present(self):
        expected = {'file_download', 'regional_json_api', 'point_api_grid', 'xarray_slice', 'grib2_bands'}
        assert set(ETL_Pipeline.DISCOVERY_METHODS.keys()) == expected
