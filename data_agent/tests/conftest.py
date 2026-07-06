"""
Shared fixtures for ETL pipeline tests.
"""
import datetime
import os
import tempfile

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_bounds
from unittest.mock import MagicMock, PropertyMock

from data_agent.etl.etl_pipeline import ETL_Pipeline


# ---------------------------------------------------------------------------
# Standard bounding box used across tests
# ---------------------------------------------------------------------------
BBOX = {'west': -88.0, 'south': 32.0, 'east': -86.0, 'north': 34.0}
START_DATE = datetime.date(2024, 1, 1)
END_DATE = datetime.date(2024, 1, 3)


# ---------------------------------------------------------------------------
# Django management command mocks
# ---------------------------------------------------------------------------
@pytest.fixture
def mock_stdout():
    stdout = MagicMock()
    stdout.write = MagicMock()
    return stdout


@pytest.fixture
def mock_style():
    style = MagicMock()
    style.SUCCESS = lambda x: x
    return style


# ---------------------------------------------------------------------------
# Minimal mock RasterDataset
# ---------------------------------------------------------------------------
@pytest.fixture
def mock_dataset():
    ds = MagicMock()
    ds.dataset_name = 'Test Dataset'
    ds.tds_product_name = 'TEST'
    ds.tds_region = 'Global'
    ds.tds_spatial_resolution = '0.5deg'
    ds.tds_temporal_resolution = 'daily'
    ds.dataset_information = {
        'metadata': {},
        'subroutines': {
            'load_to_postgis': {'schema': 'test_etl', 'table_prefix': 'test'},
        },
        'granule_info': {
            'fetch_config': {
                'format': 'file_download',
                'file_format': 'tif',
                'variables': [
                    {
                        'target_variable': 'rain',
                        'url_template': 'https://example.com/{YYYY}/{MM}/{DD}.tif',
                    },
                ],
            },
        },
    }
    ds.merge_only = False
    return ds


# ---------------------------------------------------------------------------
# ETL_Pipeline instance
# ---------------------------------------------------------------------------
@pytest.fixture
def pipeline(mock_dataset, mock_stdout, mock_style):
    return ETL_Pipeline(
        dataset=mock_dataset,
        stdout=mock_stdout,
        style=mock_style,
        start_date=START_DATE,
        end_date=END_DATE,
        bbox=BBOX,
    )


# ---------------------------------------------------------------------------
# Mock psycopg2 connection + cursor
# ---------------------------------------------------------------------------
@pytest.fixture
def mock_con():
    """Return (connection, cursor) where cursor.fetchone returns None by default."""
    con = MagicMock()
    cur = MagicMock()
    con.cursor.return_value = cur
    cur.fetchone.return_value = None
    cur.fetchall.return_value = []
    return con, cur


# ---------------------------------------------------------------------------
# Sample numpy grids
# ---------------------------------------------------------------------------
@pytest.fixture
def sample_grid_3x3():
    return np.array(
        [[1.0, 2.0, 3.0],
         [4.0, 5.0, 6.0],
         [7.0, 8.0, 9.0]],
        dtype=np.float32,
    )


@pytest.fixture
def nan_grid_3x3():
    return np.full((3, 3), np.nan, dtype=np.float32)


# ---------------------------------------------------------------------------
# Helpers for creating test GeoTIFF files
# ---------------------------------------------------------------------------
def create_test_tiff(path, data=None, width=3, height=3, bbox=None):
    """Write a minimal GeoTIFF to *path* and return the path."""
    if data is None:
        data = np.arange(width * height, dtype=np.float32).reshape(height, width)
    if bbox is None:
        bbox = BBOX
    transform = from_bounds(
        bbox['west'], bbox['south'], bbox['east'], bbox['north'],
        data.shape[1], data.shape[0],
    )
    with rasterio.open(
        path, 'w', driver='GTiff',
        height=data.shape[0], width=data.shape[1],
        count=1, dtype='float32', crs='EPSG:4326',
        transform=transform,
    ) as dst:
        dst.write(data, 1)
    return path


def create_test_tiff_bytes(data=None, width=3, height=3, bbox=None):
    """Return raw bytes of a minimal GeoTIFF."""
    with tempfile.NamedTemporaryFile(suffix='.tif', delete=False) as tmp:
        create_test_tiff(tmp.name, data=data, width=width, height=height, bbox=bbox)
        tmp.seek(0)
        raw = open(tmp.name, 'rb').read()
    os.unlink(tmp.name)
    return raw


# ---------------------------------------------------------------------------
# Realistic fake API responses
# ---------------------------------------------------------------------------
def make_nasa_power_response(source_var, lons, lats, dates, values=None):
    """Build a dict mimicking the NASA POWER regional API GeoJSON response."""
    features = []
    for lon in lons:
        for lat in lats:
            date_data = {}
            for i, dt in enumerate(dates):
                key = dt.strftime('%Y%m%d')
                date_data[key] = values[i] if values else round(20.0 + lon + lat, 2)
            features.append({
                'geometry': {'type': 'Point', 'coordinates': [lon, lat]},
                'properties': {'parameter': {source_var: date_data}},
            })
    return {'type': 'FeatureCollection', 'features': features}


def make_open_meteo_response(variable_map, dates, n_members=1):
    """Build a dict mimicking the Open-Meteo ensemble API response."""
    daily = {'time': [d.isoformat() for d in dates]}
    for api_var in variable_map:
        if n_members > 1:
            daily[api_var] = [
                [round(20.0 + m * 0.1 + i * 0.5, 2) for i in range(len(dates))]
                for m in range(n_members)
            ]
        else:
            daily[api_var] = [round(20.0 + i * 0.5, 2) for i in range(len(dates))]
    return {'daily': daily}


# ---------------------------------------------------------------------------
# fetch_config builders (matching setup_data_sources.py)
# ---------------------------------------------------------------------------
def chirps_fetch_config():
    return {
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


def chirts_fetch_config():
    return {
        'format': 'file_download',
        'file_format': 'tif',
        'variables': [
            {
                'target_variable': 'tmax',
                'url_template': (
                    'https://data.chc.ucsb.edu/products/CHIRTSdaily/'
                    'v1.0/global_tifs_p05/Tmax/{YYYY}/Tmax.{YYYY}.{MM}.{DD}.tif'
                ),
            },
            {
                'target_variable': 'tmin',
                'url_template': (
                    'https://data.chc.ucsb.edu/products/CHIRTSdaily/'
                    'v1.0/global_tifs_p05/Tmin/{YYYY}/Tmin.{YYYY}.{MM}.{DD}.tif'
                ),
            },
        ],
    }


def prism_fetch_config():
    return {
        'format': 'file_download',
        'file_format': 'zip_bil',
        'variables': [
            {
                'target_variable': 'rain',
                'url_template': 'https://services.nacse.org/prism/data/get/us/4km/ppt/{YYYYMMDD}',
            },
            {
                'target_variable': 'tmax',
                'url_template': 'https://services.nacse.org/prism/data/get/us/4km/tmax/{YYYYMMDD}',
            },
            {
                'target_variable': 'tmin',
                'url_template': 'https://services.nacse.org/prism/data/get/us/4km/tmin/{YYYYMMDD}',
            },
        ],
    }


def nasa_power_fetch_config():
    return {
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


def nasa_power_subroutines():
    return {
        'load_to_postgis': {'schema': 'test_etl', 'table_prefix': 'power'},
        'unit_conversions': {
            'T2M_MAX': {'operation': 'rename', 'target_name': 'tmax'},
            'T2M_MIN': {'operation': 'rename', 'target_name': 'tmin'},
            'PRECTOTCORR': {'operation': 'rename', 'target_name': 'rain'},
            'ALLSKY_SFC_SW_DWN': {'operation': 'rename', 'target_name': 'srad'},
        },
        'expand_bbox': {'min_range': 2.0},
    }


def open_meteo_fetch_config():
    return {
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


def open_meteo_subroutines():
    return {
        'load_to_postgis': {'schema': 'test_etl', 'table_prefix': 'open_meteo'},
        'unit_conversions': {
            'temperature_2m_max': {'operation': 'rename', 'target_name': 'tmax'},
            'temperature_2m_min': {'operation': 'rename', 'target_name': 'tmin'},
            'precipitation_sum': {'operation': 'rename', 'target_name': 'rain'},
            'shortwave_radiation_sum': {'operation': 'rename', 'target_name': 'srad'},
        },
    }


def nmme_fetch_config():
    return {
        'format': 'xarray_slice',
        'transport': 'opendap',
        'ensemble_dims': ['ensemble', 'ens', 'member', 'M'],
        'base_url': 'https://psl.noaa.gov/thredds/dodsC/Datasets/NMME',
        'models': ['CCSM4'],
        'variable_map': {'tmax': 'tmax', 'tmin': 'tmin', 'prate': 'rain'},
    }


def nmme_subroutines():
    return {
        'load_to_postgis': {'schema': 'test_etl', 'table_prefix': 'nmme'},
        'unit_conversions': {
            'tmax': {'operation': 'rename', 'target_name': 'tmax'},
            'tmin': {'operation': 'rename', 'target_name': 'tmin'},
            'prate': {'operation': 'rename', 'target_name': 'rain'},
        },
    }


def seas5_fetch_config():
    return {
        'format': 'xarray_slice',
        'transport': 'cds_api',
        'ensemble_dims': ['number', 'ensemble', 'member', 'realization'],
        'cds_product': 'seasonal-original-single-levels',
        'cds_request_extras': {
            'originating_centre': 'ecmwf',
            'system': '51',
            'day': '01',
            'leadtime_hour': [str(h) for h in range(24, 5161, 24)],
            'data_format': 'grib',
        },
        'variable_map': {
            'maximum_2m_temperature_in_the_last_24_hours': 'tmax',
            'minimum_2m_temperature_in_the_last_24_hours': 'tmin',
            'total_precipitation': 'rain',
            'surface_solar_radiation_downwards': 'srad',
        },
    }


def seas5_subroutines():
    return {
        'load_to_postgis': {'schema': 'test_etl', 'table_prefix': 'seas5'},
        'unit_conversions': {
            'maximum_2m_temperature_in_the_last_24_hours': {'operation': 'rename', 'target_name': 'tmax'},
            'minimum_2m_temperature_in_the_last_24_hours': {'operation': 'rename', 'target_name': 'tmin'},
            'total_precipitation': {'operation': 'multiply', 'value': 1000, 'target_name': 'rain'},
            'surface_solar_radiation_downwards': {'operation': 'rename', 'target_name': 'srad'},
        },
    }


def era5_fetch_config():
    return {
        'format': 'xarray_slice',
        'transport': 'cds_api',
        'cds_product': 'reanalysis-era5-single-levels',
        'include_day_range': True,
        'cds_request_extras': {
            'product_type': ['reanalysis'],
            'time': [f'{h:02d}:00' for h in range(24)],
        },
        'daily_aggregation': {
            't2m_max': {'cds_var': '2m_temperature', 'method': 'max'},
            't2m_min': {'cds_var': '2m_temperature', 'method': 'min'},
            'total_precipitation': {'cds_var': 'total_precipitation', 'method': 'sum'},
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


def era5_subroutines():
    return {
        'load_to_postgis': {'schema': 'test_etl', 'table_prefix': 'era5'},
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


def agera5_fetch_config():
    return {
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


def agera5_subroutines():
    return {
        'load_to_postgis': {'schema': 'test_etl', 'table_prefix': 'agera5'},
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


def cfsv2_fetch_config():
    return {
        'format': 'grib2_bands',
        'base_url': 'https://noaa-cfs-pds.s3.amazonaws.com',
        'variable_map': {
            'tmax': 'tmax',
            'tmin': 'tmin',
            'prate': 'rain',
            'dswsfc': 'srad',
        },
        'members': [1, 2],
        'init_hours': ['00'],
    }


def cfsv2_subroutines():
    return {
        'load_to_postgis': {'schema': 'test_etl', 'table_prefix': 'cfsv2'},
        'unit_conversions': {
            'tmax': {'operation': 'rename', 'target_name': 'tmax'},
            'tmin': {'operation': 'rename', 'target_name': 'tmin'},
            'prate': {'operation': 'multiply', 'value': 86400, 'target_name': 'rain'},
            'dswsfc': {'operation': 'multiply', 'value': 86400, 'target_name': 'srad'},
        },
    }
