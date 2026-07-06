"""
Management command to seed RasterDataset records (including multi-source
combination datasets) and create PostGIS raster tables for all data sources.

Usage:
    python manage.py setup_data_sources
    python manage.py setup_data_sources --schema dataagent
"""

import copy
import os
import logging
from collections import defaultdict
from django.conf import settings
from django.core.management.base import BaseCommand
from data_agent.models import RasterDataset

logger = logging.getLogger(__name__)

# Working directories
WORKING_DIR = getattr(settings, 'DATAAGENT_WORKING_DIR', '/tmp/dataagent')
SCHEMA = getattr(settings, 'DATAAGENT_SCHEMA', 'dataagent')

# Optional per-dataset target database override.
# When set, the ETL pipeline connects to this database instead of the
# DataAgent's own Django database.  Useful when the MCP Server (consumer)
# runs on a separate host or database.
# Leave empty / unset to use the DataAgent's default DB connection.
TARGET_DB_HOST = os.environ.get('TARGET_DB_HOST', '')
TARGET_DB_PORT = os.environ.get('TARGET_DB_PORT', '')
TARGET_DB_NAME = os.environ.get('TARGET_DB_NAME', '')
TARGET_DB_USER = os.environ.get('TARGET_DB_USER', '')
TARGET_DB_PASS = os.environ.get('TARGET_DB_PASS', '')


def _build_target_db():
    """Build a target_db dict from env vars, or None if not configured."""
    if not TARGET_DB_HOST:
        return None
    cfg = {'host': TARGET_DB_HOST}
    if TARGET_DB_PORT:
        cfg['port'] = TARGET_DB_PORT
    if TARGET_DB_NAME:
        cfg['dbname'] = TARGET_DB_NAME
    if TARGET_DB_USER:
        cfg['user'] = TARGET_DB_USER
    if TARGET_DB_PASS:
        cfg['password'] = TARGET_DB_PASS
    return cfg


# =============================================================================
# Weather variable display / color-map metadata (matches tile_handler COLOR_RAMPS)
# =============================================================================

WEATHER_VARIABLES = {
    'tmax': {
        'display_name': 'Max Temperature (\u00b0C)',
        'variable_type': 'numeric',
        'color_map': {
            'ramp': [
                [-10, 50, 100, 200],
                [0, 100, 200, 255],
                [15, 255, 255, 100],
                [30, 255, 100, 0],
                [45, 180, 0, 0],
            ],
            'chart_color': 'rgba(239,68,68,0.9)',
            'label': 'Max Temp (\u00b0C)',
        },
    },
    'tmin': {
        'display_name': 'Min Temperature (\u00b0C)',
        'variable_type': 'numeric',
        'color_map': {
            'ramp': [
                [-20, 50, 100, 200],
                [-5, 100, 200, 255],
                [10, 255, 255, 100],
                [20, 255, 100, 0],
                [35, 180, 0, 0],
            ],
            'chart_color': 'rgba(59,130,246,0.9)',
            'label': 'Min Temp (\u00b0C)',
        },
    },
    'rain': {
        'display_name': 'Rainfall (mm)',
        'variable_type': 'numeric',
        'color_map': {
            'ramp': [
                [0, 240, 250, 255],
                [0.1, 180, 220, 255],
                [5, 100, 180, 255],
                [15, 50, 100, 220],
                [30, 0, 0, 180],
                [50, 0, 0, 120],
            ],
            'chart_color': 'rgba(34,197,94,0.9)',
            'label': 'Rainfall (mm)',
        },
    },
    'srad': {
        'display_name': 'Solar Radiation (MJ/m\u00b2)',
        'variable_type': 'numeric',
        'color_map': {
            'ramp': [
                [0, 255, 255, 220],
                [5, 255, 255, 150],
                [15, 255, 255, 0],
                [25, 255, 150, 0],
                [35, 200, 0, 0],
            ],
            'chart_color': 'rgba(245,158,11,0.9)',
            'label': 'Solar Rad (MJ/m\u00b2)',
        },
    },
}

# =============================================================================
# RasterDataset configurations for each weather data source
# =============================================================================

DATASET_CONFIGS = {
    'nasa_power': {
        'dataset_name': 'NASA POWER Daily',
        'dataset_subtype': 'nasa_power_daily',
        'tds_product_name': 'NASA-POWER',
        'tds_region': 'Global',
        'tds_spatial_resolution': '0.5deg',
        'tds_temporal_resolution': 'daily',
        'number': 1,
        'data_category': 'observational_and_forecast',
        'data_type': 'point',
        'dataset_information': {
            'temporal_resolution': 'daily',
            'variables': dict(WEATHER_VARIABLES),
            'metadata': {
                'description': 'NASA POWER Daily Weather',
                'source': 'NASA/POWER MERRA-2 + CERES',
                'contact': 'power@larc.nasa.gov',
                'version': '2.0',
                'reference': 'https://power.larc.nasa.gov/',
                'temporal_resolution': 'daily',
                'spatial_resolution': '0.5 degrees',
            },
            'subroutines': {
                'load_to_postgis': {
                    'schema': SCHEMA,
                    'table_prefix': 'power',
                },
                'unit_conversions': {
                    'T2M_MAX': {'operation': 'rename', 'target_name': 'tmax'},
                    'T2M_MIN': {'operation': 'rename', 'target_name': 'tmin'},
                    'PRECTOTCORR': {'operation': 'rename', 'target_name': 'rain'},
                    'ALLSKY_SFC_SW_DWN': {'operation': 'rename', 'target_name': 'srad'},
                },
                'expand_bbox': {'min_range': 2.0},
            },
            'granule_info': {
                'temporal_resolution': 'daily',
                'primary_key': 'power_daily',
                'fetch_config': {
                    'format': 'regional_json_api',
                    'grid_resolution': 0.5,
                    'nodata_value': -999,
                    'query_limits': {
                        'max_days_per_request': 366,
                        'min_bbox_degrees': 2.0,
                        'max_bbox_degrees': 10.0,
                    },
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
                },
                'file_info_by_key': {
                    'power_daily': {
                        'preformat_source': (
                            'https://power.larc.nasa.gov/api/temporal/daily/regional'
                            '?parameters=T2M_MAX,T2M_MIN,PRECTOTCORR,ALLSKY_SFC_SW_DWN'
                            '&community=AG&longitude-min={bbox_west}&latitude-min={bbox_south}'
                            '&longitude-max={bbox_east}&latitude-max={bbox_north}'
                            '&start={start_date}&end={end_date}&format=JSON'
                        ),
                        'source_type': 'api',
                        'variables': {
                            'T2M_MAX': {'original_variable': 'T2M_MAX'},
                            'T2M_MIN': {'original_variable': 'T2M_MIN'},
                            'PRECTOTCORR': {'original_variable': 'PRECTOTCORR'},
                            'ALLSKY_SFC_SW_DWN': {'original_variable': 'ALLSKY_SFC_SW_DWN'},
                        },
                    },
                },
            },
        },
    },
    'chirps': {
        'dataset_name': 'CHIRPS v2.0 Daily',
        'dataset_subtype': 'chirps_daily',
        'tds_product_name': 'CHIRPS',
        'tds_region': 'Global',
        'tds_spatial_resolution': '0.05deg',
        'tds_temporal_resolution': 'daily',
        'number': 2,
        'data_category': 'observational',
        'data_type': 'raster',
        'dataset_information': {
            'temporal_resolution': 'daily',
            'variables': {k: WEATHER_VARIABLES[k] for k in ['rain']},
            'metadata': {
                'description': 'CHIRPS Daily Rainfall',
                'source': 'CHC/UCSB',
                'contact': 'pete@geog.ucsb.edu',
                'version': '2.0',
                'reference': 'https://www.chc.ucsb.edu/data/chirps',
                'temporal_resolution': 'daily',
                'spatial_resolution': '0.05 degrees',
            },
            'subroutines': {
                'load_to_postgis': {
                    'schema': SCHEMA,
                    'table_prefix': 'chirps',
                },
            },
            'granule_info': {
                'temporal_resolution': 'daily',
                'primary_key': 'rainfall',
                'fetch_config': {
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
                },
                'file_info_by_key': {
                    'rainfall': {
                        'preformat_source': (
                            'https://data.chc.ucsb.edu/products/CHIRPS-2.0/'
                            'global_daily/tifs/p05/{YYYY}/'
                            'chirps-v2.0.{YYYY}.{MM}.{DD}.tif.gz'
                        ),
                        'source_type': 'https_anchor',
                        'variables': {
                            '0': {'original_variable': 'rain'},
                        },
                    },
                },
            },
        },
    },
    'chirts': {
        'dataset_name': 'CHIRTS Daily Temperature',
        'dataset_subtype': 'chirts_daily',
        'tds_product_name': 'CHIRTS',
        'tds_region': 'Global',
        'tds_spatial_resolution': '0.05deg',
        'tds_temporal_resolution': 'daily',
        'number': 3,
        'data_category': 'observational',
        'data_type': 'raster',
        'dataset_information': {
            'temporal_resolution': 'daily',
            'variables': {k: WEATHER_VARIABLES[k] for k in ['tmax', 'tmin']},
            'metadata': {
                'description': 'CHIRTS Daily Temperature',
                'source': 'CHC/UCSB',
                'contact': 'pete@geog.ucsb.edu',
                'version': '1.0',
                'reference': 'https://www.chc.ucsb.edu/data/chirts',
                'temporal_resolution': 'daily',
                'spatial_resolution': '0.05 degrees',
            },
            'subroutines': {
                'load_to_postgis': {
                    'schema': SCHEMA,
                    'table_prefix': 'chirts',
                },
            },
            'granule_info': {
                'temporal_resolution': 'daily',
                'primary_key': 'temperature',
                'fetch_config': {
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
                },
                'file_info_by_key': {
                    'tmax': {
                        'preformat_source': (
                            'https://data.chc.ucsb.edu/products/CHIRTSdaily/'
                            'v1.0/global_tifs_p05/Tmax/{YYYY}/'
                            'Tmax.{YYYY}.{MM}.{DD}.tif'
                        ),
                        'source_type': 'https_anchor',
                        'variables': {
                            '0': {'original_variable': 'tmax'},
                        },
                    },
                    'tmin': {
                        'preformat_source': (
                            'https://data.chc.ucsb.edu/products/CHIRTSdaily/'
                            'v1.0/global_tifs_p05/Tmin/{YYYY}/'
                            'Tmin.{YYYY}.{MM}.{DD}.tif'
                        ),
                        'source_type': 'https_anchor',
                        'variables': {
                            '0': {'original_variable': 'tmin'},
                        },
                    },
                },
            },
        },
    },
    'era5': {
        'dataset_name': 'ERA5 Reanalysis Daily',
        'dataset_subtype': 'era5_daily',
        'tds_product_name': 'ERA5',
        'tds_region': 'Global',
        'tds_spatial_resolution': '0.25deg',
        'tds_temporal_resolution': 'daily',
        'number': 4,
        'data_category': 'observational',
        'data_type': 'raster',
        'dataset_information': {
            'temporal_resolution': 'daily',
            'variables': dict(WEATHER_VARIABLES),
            'metadata': {
                'description': 'ERA5 Reanalysis Daily Weather',
                'source': 'ECMWF/Copernicus CDS',
                'contact': 'copernicus-support@ecmwf.int',
                'version': '5.0',
                'reference': 'https://cds.climate.copernicus.eu/',
                'temporal_resolution': 'daily',
                'spatial_resolution': '0.25 degrees',
            },
            'subroutines': {
                'load_to_postgis': {
                    'schema': SCHEMA,
                    'table_prefix': 'era5',
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
            },
            'granule_info': {
                'temporal_resolution': 'hourly',
                'primary_key': 'era5_daily',
                'fetch_config': {
                    'format': 'xarray_slice',
                    'transport': 'cds_api',
                    'cds_product': 'reanalysis-era5-single-levels',
                    'include_day_range': True,
                    'cds_request_extras': {
                        'product_type': ['reanalysis'],
                        'time': [f'{h:02d}:00' for h in range(24)],
                    },
                    # Split into separate CDS requests per variable — the
                    # new CDS API may silently drop variables from
                    # multi-variable requests.
                    'cds_variable_groups': [
                        {'variables': ['2m_temperature']},
                        {'variables': ['total_precipitation']},
                        {'variables': ['surface_solar_radiation_downwards']},
                    ],
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
                },
                'file_info_by_key': {
                    'era5_daily': {
                        'preformat_source': 'cds://reanalysis-era5-single-levels',
                        'source_type': 'api',
                        'scrape_options': {
                            'cds_product': 'reanalysis-era5-single-levels',
                        },
                        'variables': {
                            't2m_max': {
                                'original_variable': '2m_temperature',
                                'aggregation': 'max',
                            },
                            't2m_min': {
                                'original_variable': '2m_temperature',
                                'aggregation': 'min',
                            },
                            'total_precipitation': {
                                'original_variable': 'total_precipitation',
                                'aggregation': 'sum',
                            },
                            'surface_solar_radiation_downwards': {
                                'original_variable': 'surface_solar_radiation_downwards',
                                'aggregation': 'sum',
                            },
                        },
                    },
                },
            },
        },
    },
    'agera5': {
        'dataset_name': 'AgERA5 Agrometeorological Daily',
        'dataset_subtype': 'agera5_daily',
        'tds_product_name': 'AgERA5',
        'tds_region': 'Global',
        'tds_spatial_resolution': '0.1deg',
        'tds_temporal_resolution': 'daily',
        'number': 5,
        'data_category': 'observational',
        'data_type': 'raster',
        'dataset_information': {
            'temporal_resolution': 'daily',
            'variables': dict(WEATHER_VARIABLES),
            'metadata': {
                'description': 'AgERA5 Agrometeorological Daily Weather',
                'source': 'Copernicus CDS',
                'contact': 'copernicus-support@ecmwf.int',
                'version': '1.1',
                'reference': 'https://cds.climate.copernicus.eu/',
                'temporal_resolution': 'daily',
                'spatial_resolution': '0.1 degrees',
            },
            'subroutines': {
                'load_to_postgis': {
                    'schema': SCHEMA,
                    'table_prefix': 'agera5',
                },
                'unit_conversions': {
                    'Temperature_Air_2m_Max_24h': {
                        'operation': 'rename', 'target_name': 'tmax',
                    },
                    'Temperature_Air_2m_Min_24h': {
                        'operation': 'rename', 'target_name': 'tmin',
                    },
                    'Precipitation_Flux': {
                        'operation': 'multiply', 'value': 86400,
                        'target_name': 'rain',
                    },
                    'Solar_Radiation_Flux': {
                        'operation': 'rename', 'target_name': 'srad',
                    },
                },
            },
            'granule_info': {
                'temporal_resolution': 'daily',
                'primary_key': 'agera5_daily',
                'fetch_config': {
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
                },
                'file_info_by_key': {
                    'agera5_daily': {
                        'preformat_source': 'cds://sis-agrometeorological-indicators',
                        'source_type': 'api',
                        'scrape_options': {
                            'cds_product': 'sis-agrometeorological-indicators',
                        },
                        'variables': {
                            'Temperature_Air_2m_Max_24h': {
                                'original_variable': '2m_temperature_max',
                            },
                            'Temperature_Air_2m_Min_24h': {
                                'original_variable': '2m_temperature_min',
                            },
                            'Precipitation_Flux': {
                                'original_variable': 'precipitation_flux',
                            },
                            'Solar_Radiation_Flux': {
                                'original_variable': 'solar_radiation_flux',
                            },
                        },
                    },
                },
            },
        },
    },
    'prism': {
        'dataset_name': 'PRISM Daily',
        'dataset_subtype': 'prism_daily',
        'tds_product_name': 'PRISM',
        'tds_region': 'CONUS',
        'tds_spatial_resolution': '0.04deg',
        'tds_temporal_resolution': 'daily',
        'number': 6,
        'data_category': 'observational',
        'data_type': 'raster',
        'dataset_information': {
            'temporal_resolution': 'daily',
            'variables': {k: WEATHER_VARIABLES[k] for k in ['tmax', 'tmin', 'rain']},
            'metadata': {
                'description': 'PRISM Daily Weather (~4km)',
                'source': 'Oregon State University / PRISM Climate Group',
                'contact': 'prism_climate@nacse.org',
                'version': 'AN/LT',
                'reference': 'https://prism.oregonstate.edu/',
                'temporal_resolution': 'daily',
                'spatial_resolution': '~4km (~0.04 degrees)',
            },
            'subroutines': {
                'load_to_postgis': {
                    'schema': SCHEMA,
                    'table_prefix': 'prism',
                },
            },
            'granule_info': {
                'temporal_resolution': 'daily',
                'primary_key': 'prism_daily',
                'fetch_config': {
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
                },
                'file_info_by_key': {
                    'ppt': {
                        'source_type': 'https_anchor',
                        'variables': {'0': {'original_variable': 'rain'}},
                    },
                    'tmax': {
                        'source_type': 'https_anchor',
                        'variables': {'0': {'original_variable': 'tmax'}},
                    },
                    'tmin': {
                        'source_type': 'https_anchor',
                        'variables': {'0': {'original_variable': 'tmin'}},
                    },
                },
            },
        },
    },
    # =========================================================================
    # Forecast data sources
    # =========================================================================
    'open_meteo': {
        'dataset_name': 'Open-Meteo Ensemble Forecast',
        'dataset_subtype': 'open_meteo_ensemble',
        'tds_product_name': 'Open-Meteo',
        'tds_region': 'Global',
        'tds_spatial_resolution': '0.25deg',
        'tds_temporal_resolution': 'daily',
        'number': 7,
        'data_category': 'forecast',
        'data_type': 'point',
        'dataset_information': {
            'temporal_resolution': 'daily',
            'variables': dict(WEATHER_VARIABLES),
            'metadata': {
                'description': 'Open-Meteo ECMWF IFS Ensemble Forecast',
                'source': 'Open-Meteo / ECMWF',
                'version': '1.0',
                'reference': 'https://open-meteo.com/en/docs/ensemble-api',
                'temporal_resolution': 'daily',
                'spatial_resolution': '0.25 degrees',
            },
            'subroutines': {
                'load_to_postgis': {
                    'schema': SCHEMA,
                    'table_prefix': 'open_meteo',
                },
                'unit_conversions': {
                    'temperature_2m_max': {'operation': 'rename', 'target_name': 'tmax'},
                    'temperature_2m_min': {'operation': 'rename', 'target_name': 'tmin'},
                    'precipitation_sum': {'operation': 'rename', 'target_name': 'rain'},
                    'shortwave_radiation_sum': {'operation': 'rename', 'target_name': 'srad'},
                },
            },
            'granule_info': {
                'temporal_resolution': 'daily',
                'fetch_config': {
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
                },
            },
        },
    },
    'nmme': {
        'dataset_name': 'NMME Climate Forecast',
        'dataset_subtype': 'nmme_monthly',
        'tds_product_name': 'NMME',
        'tds_region': 'Global',
        'tds_spatial_resolution': '1.0deg',
        'tds_temporal_resolution': 'daily',
        'number': 8,
        'data_category': 'forecast',
        'data_type': 'raster',
        'dataset_information': {
            'temporal_resolution': 'daily',
            'variables': {k: WEATHER_VARIABLES[k] for k in ['tmax', 'tmin', 'rain']},
            'metadata': {
                'description': 'NMME Multi-Model Ensemble Climate Forecast',
                'source': 'NOAA/PSL',
                'version': '1.0',
                'reference': 'https://www.cpc.ncep.noaa.gov/products/NMME/',
                'temporal_resolution': 'monthly',
                'spatial_resolution': '1.0 degrees',
            },
            'subroutines': {
                'load_to_postgis': {
                    'schema': SCHEMA,
                    'table_prefix': 'nmme',
                },
                'unit_conversions': {
                    'tmax': {'operation': 'rename', 'target_name': 'tmax'},
                    'tmin': {'operation': 'rename', 'target_name': 'tmin'},
                    'prate': {'operation': 'rename', 'target_name': 'rain'},
                },
            },
            'granule_info': {
                'temporal_resolution': 'daily',
                'fetch_config': {
                    'format': 'xarray_slice',
                    'transport': 'opendap',
                    'ensemble_dims': ['ensemble', 'ens', 'member', 'M'],
                    'base_url': 'https://psl.noaa.gov/thredds/dodsC/Datasets/NMME',
                    'models': ['CCSM4', 'GEM_NEMO', 'GFDL-SPEAR', 'CanCM4i'],
                    'variable_map': {
                        'tmax': 'tmax',
                        'tmin': 'tmin',
                        'prate': 'rain',
                    },
                },
            },
        },
    },
    'seas5': {
        'dataset_name': 'SEAS5 Seasonal Forecast',
        'dataset_subtype': 'seas5_seasonal',
        'tds_product_name': 'SEAS5',
        'tds_region': 'Global',
        'tds_spatial_resolution': '1.0deg',
        'tds_temporal_resolution': 'daily',
        'number': 9,
        'data_category': 'forecast',
        'data_type': 'raster',
        'dataset_information': {
            'temporal_resolution': 'daily',
            'variables': dict(WEATHER_VARIABLES),
            'metadata': {
                'description': 'ECMWF SEAS5 Seasonal Forecast',
                'source': 'ECMWF / Copernicus CDS',
                'version': '5.0',
                'reference': 'https://cds.climate.copernicus.eu/',
                'temporal_resolution': 'daily',
                'spatial_resolution': '1.0 degrees',
            },
            'subroutines': {
                'load_to_postgis': {
                    'schema': SCHEMA,
                    'table_prefix': 'seas5',
                },
                'unit_conversions': {
                    'maximum_2m_temperature_in_the_last_24_hours': {'operation': 'rename', 'target_name': 'tmax'},
                    'minimum_2m_temperature_in_the_last_24_hours': {'operation': 'rename', 'target_name': 'tmin'},
                    'total_precipitation': {'operation': 'multiply', 'value': 1000, 'target_name': 'rain'},
                    'surface_solar_radiation_downwards': {'operation': 'rename', 'target_name': 'srad'},
                },
            },
            'granule_info': {
                'temporal_resolution': 'daily',
                'fetch_config': {
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
                },
            },
        },
    },
    'cfsv2': {
        'dataset_name': 'CFSv2 Seasonal Forecast',
        'dataset_subtype': 'cfsv2_seasonal',
        'tds_product_name': 'CFSv2',
        'tds_region': 'Global',
        'tds_spatial_resolution': '0.5deg',
        'tds_temporal_resolution': 'daily',
        'number': 10,
        'data_category': 'forecast',
        'data_type': 'raster',
        'dataset_information': {
            'temporal_resolution': 'daily',
            'variables': dict(WEATHER_VARIABLES),
            'metadata': {
                'description': 'NCEP CFSv2 Seasonal Forecast',
                'source': 'NOAA/NCEP',
                'version': '2.0',
                'reference': 'https://www.ncei.noaa.gov/products/weather-climate-models/climate-forecast-system',
                'temporal_resolution': 'daily',
                'spatial_resolution': '0.5 degrees',
            },
            'subroutines': {
                'load_to_postgis': {
                    'schema': SCHEMA,
                    'table_prefix': 'cfsv2',
                },
                'unit_conversions': {
                    'tmax': {'operation': 'rename', 'target_name': 'tmax'},
                    'tmin': {'operation': 'rename', 'target_name': 'tmin'},
                    'prate': {'operation': 'multiply', 'value': 86400, 'target_name': 'rain'},
                    'dswsfc': {'operation': 'multiply', 'value': 86400, 'target_name': 'srad'},
                },
            },
            'granule_info': {
                'temporal_resolution': 'daily',
                'fetch_config': {
                    'format': 'grib2_bands',
                    'base_url': 'https://noaa-cfs-pds.s3.amazonaws.com',
                    'variable_map': {
                        'tmax': 'tmax',
                        'tmin': 'tmin',
                        'prate': 'rain',
                        'dswsfc': 'srad',
                    },
                    'members': [1, 2, 3, 4],
                    'init_hours': ['00'],
                },
            },
        },
    },
}

# Variables provided by each source
SOURCE_VARIABLES = {
    'nasa_power': ['tmax', 'tmin', 'rain', 'srad'],
    'era5': ['tmax', 'tmin', 'rain', 'srad'],
    'agera5': ['tmax', 'tmin', 'rain', 'srad'],
    'chirps': ['rain'],
    'chirts': ['tmax', 'tmin'],
    'prism': ['tmax', 'tmin', 'rain'],
    'open_meteo': ['tmax', 'tmin', 'rain', 'srad'],
    'nmme': ['tmax', 'tmin', 'rain'],
    'seas5': ['tmax', 'tmin', 'rain', 'srad'],
    'cfsv2': ['tmax', 'tmin', 'rain', 'srad'],
}

# Combined dataset configurations
COMBINATION_CONFIGS = [
    {
        'name': 'chirps_chirts_era5',
        'rain': 'chirps', 'tmax': 'chirts', 'tmin': 'chirts', 'srad': 'era5',
        'target_res': 0.05, 'prefix': 'chirps_chirts_era5',
    },
    {
        'name': 'chirps_chirts_power',
        'rain': 'chirps', 'tmax': 'chirts', 'tmin': 'chirts', 'srad': 'nasa_power',
        'target_res': 0.05, 'prefix': 'chirps_chirts_power',
    },
    {
        'name': 'chirps_chirts_agera5',
        'rain': 'chirps', 'tmax': 'chirts', 'tmin': 'chirts', 'srad': 'agera5',
        'target_res': 0.05, 'prefix': 'chirps_chirts_agera5',
    },
    {
        'name': 'chirps_era5',
        'rain': 'chirps', 'tmax': 'era5', 'tmin': 'era5', 'srad': 'era5',
        'target_res': 0.05, 'prefix': 'chirps_era5',
    },
    {
        'name': 'chirps_agera5',
        'rain': 'chirps', 'tmax': 'agera5', 'tmin': 'agera5', 'srad': 'agera5',
        'target_res': 0.05, 'prefix': 'chirps_agera5',
    },
    {
        'name': 'chirps_power',
        'rain': 'chirps', 'tmax': 'nasa_power', 'tmin': 'nasa_power', 'srad': 'nasa_power',
        'target_res': 0.05, 'prefix': 'chirps_power',
    },
    # PRISM combinations (rain/tmax/tmin from PRISM, srad from other sources)
    {
        'name': 'prism_era5',
        'rain': 'prism', 'tmax': 'prism', 'tmin': 'prism', 'srad': 'era5',
        'target_res': 0.04, 'prefix': 'prism_era5',
    },
    {
        'name': 'prism_power',
        'rain': 'prism', 'tmax': 'prism', 'tmin': 'prism', 'srad': 'nasa_power',
        'target_res': 0.04, 'prefix': 'prism_power',
    },
    {
        'name': 'prism_agera5',
        'rain': 'prism', 'tmax': 'prism', 'tmin': 'prism', 'srad': 'agera5',
        'target_res': 0.04, 'prefix': 'prism_agera5',
    },
]


def _build_source_group(source_key, target_vars):
    """
    Extract a filtered source_group config from DATASET_CONFIGS[source_key]
    for only the specified target variables.

    Returns: {
        'id': '<source_key>_<var1>_<var2>',
        'temporal_resolution': 'daily',
        'fetch_config': { ...filtered... },
        'unit_conversions': { ...filtered... },  # only if needed
    }
    """
    target_vars = set(target_vars)
    cfg = DATASET_CONFIGS[source_key]
    ds_info = cfg['dataset_information']
    granule = ds_info['granule_info']
    fetch_raw = granule['fetch_config']
    fetch = copy.deepcopy(fetch_raw)
    fmt = fetch['format']

    # --- filter fetch_config by format ---

    if fmt == 'file_download':
        # Keep only variable entries whose target_variable is wanted
        fetch['variables'] = [
            v for v in fetch['variables']
            if v['target_variable'] in target_vars
        ]

    elif fmt == 'regional_json_api':
        # variable_map: {api_param: target_var} — keep wanted targets
        fetch['variable_map'] = {
            k: v for k, v in fetch['variable_map'].items()
            if v in target_vars
        }

    elif fmt == 'xarray_slice':
        # variable_map values may be a string or list of strings
        filtered_vmap = {}
        for cds_var, targets in fetch['variable_map'].items():
            if isinstance(targets, list):
                kept = [t for t in targets if t in target_vars]
                if kept:
                    filtered_vmap[cds_var] = kept if len(kept) > 1 else kept[0]
            else:
                if targets in target_vars:
                    filtered_vmap[cds_var] = targets
        fetch['variable_map'] = filtered_vmap

        # Collect the set of CDS variable names we still need
        needed_cds_vars = set(filtered_vmap.keys())

        # Filter cds_variable_groups if present
        if 'cds_variable_groups' in fetch:
            fetch['cds_variable_groups'] = [
                g for g in fetch['cds_variable_groups']
                if any(v in needed_cds_vars for v in g['variables'])
            ]

        # Filter daily_aggregation if present: keep entries whose
        # intermediate name maps (via unit_conversions) to a wanted target
        if 'daily_aggregation' in fetch:
            unit_conv = ds_info.get('subroutines', {}).get('unit_conversions', {})
            filtered_agg = {}
            for agg_key, agg_val in fetch['daily_aggregation'].items():
                # The aggregation key is the intermediate variable name.
                # Check if its unit_conversion target_name is wanted.
                conv = unit_conv.get(agg_key, {})
                agg_target = conv.get('target_name', agg_key)
                if agg_target in target_vars:
                    filtered_agg[agg_key] = agg_val
            fetch['daily_aggregation'] = filtered_agg

    elif fmt == 'point_api_grid':
        # variable_map: {api_param: target_var}
        fetch['variable_map'] = {
            k: v for k, v in fetch['variable_map'].items()
            if v in target_vars
        }

    # --- build the source group dict ---

    group_id = f"{source_key}_{'_'.join(sorted(target_vars))}"
    group = {
        'id': group_id,
        'temporal_resolution': granule.get('temporal_resolution', 'daily'),
        'fetch_config': fetch,
    }

    # Filter unit_conversions to only entries whose target_name is wanted
    unit_conv = ds_info.get('subroutines', {}).get('unit_conversions', {})
    if unit_conv:
        filtered_conv = {
            k: v for k, v in unit_conv.items()
            if v.get('target_name') in target_vars
        }
        if filtered_conv:
            group['unit_conversions'] = copy.deepcopy(filtered_conv)

    return group


def _create_raster_table(cur, schema, table):
    """Create a PostGIS raster table with standard indexes."""
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {schema}.{table} (
            rast raster NOT NULL,
            fdate date NOT NULL,
            rid serial NOT NULL
        );
    """)
    # Create indexes (use IF NOT EXISTS pattern via exception handling)
    for idx_sql in [
        f"CREATE INDEX IF NOT EXISTS {table}_time ON {schema}.{table} (fdate);",
        f"CREATE INDEX IF NOT EXISTS {table}_rid ON {schema}.{table} (rid);",
        f"CREATE INDEX IF NOT EXISTS {table}_spatial ON {schema}.{table} USING GIST (ST_Envelope(rast));",
    ]:
        try:
            cur.execute(idx_sql)
        except Exception:
            pass


def _create_forecast_raster_table(cur, schema, table):
    """Create a PostGIS raster table with ens column for forecast data."""
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {schema}.{table} (
            rast raster NOT NULL,
            fdate date NOT NULL,
            rid serial NOT NULL,
            ens integer NOT NULL
        );
    """)
    for idx_sql in [
        f"CREATE INDEX IF NOT EXISTS {table}_time ON {schema}.{table} (fdate);",
        f"CREATE INDEX IF NOT EXISTS {table}_rid ON {schema}.{table} (rid);",
        f"CREATE INDEX IF NOT EXISTS {table}_ens ON {schema}.{table} (ens);",
        f"CREATE INDEX IF NOT EXISTS {table}_spatial ON {schema}.{table} USING GIST (ST_Envelope(rast));",
    ]:
        try:
            cur.execute(idx_sql)
        except Exception:
            pass


class Command(BaseCommand):
    help = 'Seed RasterDataset records (single + combined sources), create PostGIS tables'

    def add_arguments(self, parser):
        parser.add_argument(
            '--schema', type=str, default=SCHEMA,
            help=f'PostGIS schema name (default: {SCHEMA})',
        )
        parser.add_argument(
            '--skip-tables', action='store_true',
            help='Skip PostGIS table creation (only create Django model records)',
        )
        parser.add_argument(
            '--target-db-host', type=str, default=TARGET_DB_HOST,
            help='Override target database host for PostGIS writes',
        )
        parser.add_argument(
            '--target-db-name', type=str, default=TARGET_DB_NAME,
            help='Override target database name for PostGIS writes',
        )

    def handle(self, *args, **options):
        schema = options['schema']
        skip_tables = options['skip_tables']

        # Build target_db config from env vars or CLI overrides
        target_db = _build_target_db()
        if options.get('target_db_host'):
            target_db = target_db or {}
            target_db['host'] = options['target_db_host']
        if options.get('target_db_name'):
            target_db = target_db or {}
            target_db['dbname'] = options['target_db_name']

        self.stdout.write(self.style.SUCCESS('Setting up DataAgent data sources...'))
        if target_db:
            self.stdout.write(self.style.SUCCESS(
                f'  Target DB override: host={target_db.get("host")}, '
                f'dbname={target_db.get("dbname", "(default)")}'
            ))

        # 1. Create RasterDataset records
        created_datasets = {}
        for source_key, config in DATASET_CONFIGS.items():
            # Update schema in the config
            ds_info = config['dataset_information']
            if 'subroutines' in ds_info and 'load_to_postgis' in ds_info['subroutines']:
                ds_info['subroutines']['load_to_postgis']['schema'] = schema
                # Attach target_db if configured
                if target_db:
                    ds_info['subroutines']['load_to_postgis']['target_db'] = target_db
                else:
                    ds_info['subroutines']['load_to_postgis'].pop('target_db', None)

            defaults = {
                'dataset_type': 'time_series',
                'dataset_subtype': config['dataset_subtype'],
                'is_pipeline_enabled': True,
                'tds_product_name': config.get('tds_product_name', 'UNKNOWN_PRODUCT_NAME'),
                'tds_region': config.get('tds_region', 'Global'),
                'tds_spatial_resolution': config['tds_spatial_resolution'],
                'tds_temporal_resolution': config.get('tds_temporal_resolution', 'daily'),
                'number': config['number'],
                'temp_working_dir': os.path.join(WORKING_DIR, source_key, 'working'),
                'final_load_dir': os.path.join(WORKING_DIR, source_key, 'final'),
                'dataset_information': ds_info,
                'data_category': config.get('data_category', 'observational'),
                'data_type': config.get('data_type', 'raster'),
            }

            ds, created = RasterDataset.objects.update_or_create(
                dataset_name=config['dataset_name'],
                defaults=defaults,
            )
            created_datasets[source_key] = ds
            action = 'Created' if created else 'Updated'
            self.stdout.write(self.style.SUCCESS(
                f'  {action} RasterDataset: {ds.dataset_name} (uuid={ds.uuid})'
            ))

        # 2. Create RasterDataset records for multi-source combinations
        combo_number_start = max(c['number'] for c in DATASET_CONFIGS.values()) + 1
        for idx, combo in enumerate(COMBINATION_CONFIGS):
            combo_name = combo['name']
            prefix = combo['prefix']

            # Group variable→source mapping by source
            # e.g. {'chirps': ['rain'], 'chirts': ['tmax', 'tmin'], 'era5': ['srad']}
            source_to_vars = defaultdict(list)
            for var in ['rain', 'tmax', 'tmin', 'srad']:
                source_to_vars[combo[var]].append(var)

            # Build source_groups list
            source_groups = []
            for src_key, vars_list in source_to_vars.items():
                group = _build_source_group(src_key, vars_list)
                source_groups.append(group)

            # Determine a human-friendly dataset name
            # e.g. "CHIRPS + CHIRTS + ERA5 (Combined Daily)"
            source_labels = [
                DATASET_CONFIGS[sk].get('tds_product_name', sk)
                for sk in source_to_vars
            ]
            ds_display = ' + '.join(source_labels) + ' (Combined Daily)'

            ds_info = {
                'temporal_resolution': 'daily',
                'variables': dict(WEATHER_VARIABLES),
                'subroutines': {
                    'load_to_postgis': {
                        'schema': schema,
                        'table_prefix': prefix,
                    },
                },
                'granule_info': {
                    'source_groups': source_groups,
                    'target_resolution': combo['target_res'],
                },
            }

            # Attach target_db if configured
            if target_db:
                ds_info['subroutines']['load_to_postgis']['target_db'] = target_db

            defaults = {
                'dataset_type': 'time_series',
                'dataset_subtype': f'{combo_name}_combined',
                'is_pipeline_enabled': True,
                'tds_product_name': combo_name.upper().replace('_', '-'),
                'tds_region': 'Global',
                'tds_spatial_resolution': f'{combo["target_res"]}deg',
                'tds_temporal_resolution': 'daily',
                'number': combo_number_start + idx,
                'temp_working_dir': os.path.join(WORKING_DIR, combo_name, 'working'),
                'final_load_dir': os.path.join(WORKING_DIR, combo_name, 'final'),
                'dataset_information': ds_info,
                'data_category': 'combined',
                'data_type': 'raster',
            }

            ds, created = RasterDataset.objects.update_or_create(
                dataset_name=ds_display,
                defaults=defaults,
            )
            created_datasets[combo_name] = ds
            action = 'Created' if created else 'Updated'
            self.stdout.write(self.style.SUCCESS(
                f'  {action} Combined RasterDataset: {ds.dataset_name} '
                f'(prefix={prefix}, uuid={ds.uuid})'
            ))

        # 3. Create PostGIS tables
        if not skip_tables:
            self._create_postgis_tables(schema, created_datasets)

        self.stdout.write(self.style.SUCCESS(
            '\nData source setup complete!'
        ))

    def _create_postgis_tables(self, schema, created_datasets):
        """Create PostGIS schema and raster tables."""
        try:
            import psycopg2 as pg
        except ImportError:
            self.stdout.write(self.style.ERROR(
                'psycopg2 not installed, skipping PostGIS table creation'
            ))
            return

        db = settings.DATABASES['default']
        con = pg.connect(
            dbname=db['NAME'],
            user=db['USER'],
            password=db['PASSWORD'],
            host=db['HOST'],
            port=db['PORT'],
        )
        con.autocommit = True
        cur = con.cursor()

        # Create schema
        cur.execute(f'CREATE SCHEMA IF NOT EXISTS {schema};')
        self.stdout.write(self.style.SUCCESS(f'\n  Created schema: {schema}'))

        # Single-source tables
        tables_created = 0
        forecast_sources = {
            k for k, v in DATASET_CONFIGS.items()
            if v.get('data_category') == 'forecast'
        }
        for source_key, variables in SOURCE_VARIABLES.items():
            ds_info = DATASET_CONFIGS[source_key]['dataset_information']
            prefix = ds_info['subroutines']['load_to_postgis']['table_prefix']
            create_fn = (
                _create_forecast_raster_table
                if source_key in forecast_sources
                else _create_raster_table
            )
            for var in variables:
                table = f'{prefix}_{var}'
                create_fn(cur, schema, table)
                tables_created += 1

        # Combined tables
        for combo in COMBINATION_CONFIGS:
            prefix = combo['prefix']
            for var in ['tmax', 'tmin', 'rain', 'srad']:
                table = f'{prefix}_{var}'
                _create_raster_table(cur, schema, table)
                tables_created += 1

        con.commit()
        cur.close()
        con.close()

        self.stdout.write(self.style.SUCCESS(
            f'  Created {tables_created} PostGIS raster tables in {schema}'
        ))
