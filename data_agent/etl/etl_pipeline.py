import time
import calendar
import sys
import datetime
import glob
import gzip
import os
import re
import shutil
import urllib
import zipfile
import json
import copy
import logging
import random
import string
import numpy as np
import pandas as pd
import requests
import xarray as xr
import math
import subprocess
import tempfile
import rasterio
from rasterio.transform import from_bounds
from datetime import timedelta
from pathlib import Path
from collections import OrderedDict
from inspect import getframeinfo, stack

try:
    import regionmask as rm
except ImportError:
    rm = None

try:
    import geopandas as gpd
except ImportError:
    gpd = None

try:
    import dask
except ImportError:
    dask = None

try:
    from dateutil import rrule
except ImportError:
    rrule = None

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

try:
    from osgeo import gdal
except ImportError:
    gdal = None

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterable, **kwargs):
        return iterable

try:
    import psycopg2 as pg
except ImportError:
    pg = None

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Load GDAL config from environment (optional, not required for all operations)
_config_path = str(BASE_DIR) + '/config.json'
if os.path.exists(_config_path):
    with open(_config_path) as _f:
        _config = json.load(_f)
    for _key in ('GDAL_DRIVER_PATH', 'GDAL_DATA', 'GDAL_LIB', 'PROJ_LIB'):
        if _key in _config:
            os.environ[_key] = _config[_key]

DEBUG_LEVEL = os.environ.get('ETL_DEBUG_LEVEL', 'DEBUG')


class ETL_Pipeline:
    # NOTE: per-run mutable state (cleanup_paths, loaded_files,
    # dataset_variables, first_run) is initialised in __init__ so each
    # pipeline instance owns its own. They were previously class-level
    # attributes, which meant every instance shared one list — causing
    # cross-dataset state bleed and a race when pipelines run concurrently
    # (or sequentially reuse a worker process).

    # ── Config-driven Discover → Extract → Transform → Load dispatch ──
    DISCOVERY_METHODS = {
        'file_download': '_discover_file_download',
        'regional_json_api': '_discover_regional_json_api',
        'point_api_grid': '_discover_point_api_grid',
        'xarray_slice': '_discover_xarray_slice',
        'grib2_bands': '_discover_grib2_bands',
    }
    EXTRACT_ITERATORS = {
        'file_download': '_extract_file_download',
        'regional_json_api': '_extract_regional_json_api',
        'point_api_grid': '_extract_point_api_grid',
        'xarray_slice': '_extract_xarray_slices',
        'grib2_bands': '_extract_grib2_bands',
    }

    # Default Constructor
    def __init__(self, dataset, stdout, style, start_date, end_date, bbox=None,
                 target_schema=None):
        self.dataset = dataset
        self.stdout = stdout
        self.style = style
        self.start_date = start_date
        self.end_date = end_date
        self.bbox = bbox
        self.target_schema = target_schema
        # Per-instance mutable state (see class docstring note above).
        self.cleanup_paths = []
        self.loaded_files = []
        self.dataset_variables = []
        self.first_run = True

########################################################################################################################
# LOGGING FUNCTIONS ####################################################################################################
########################################################################################################################
    def log_message(self, message, message_type):
        stack_frame = getframeinfo(stack()[2][0])
        function_name = stack_frame.function
        line_no = stack_frame.lineno
        message_timestamp = time.ctime()
        self.stdout.write(self.style.SUCCESS("\n[{}][{}][{}:{}]: {}\n".format(message_timestamp, message_type,
                                                                                  function_name, line_no, message)))

    def log_success(self, message):
        self.log_message(message, 'SUCCESS')

    def log_error(self, message):
        self.log_message(message, 'ERROR')

    def log_warning(self, message):
        self.log_message(message, 'WARNING')

    def log_debug(self, message):
        if DEBUG_LEVEL == 'DEBUG':
            self.log_message(message, 'DEBUG')

    def log_once(self, message):
        if self.first_run:
            self.log_message(message, 'PROCESS')


########################################################################################################################
# FORMATTING FUNCTIONS #################################################################################################
########################################################################################################################

    def get_replacement_string(self, key, format_params):
        if('current_date' in format_params.keys()):
            current_date = format_params['current_date']
            if key == 'product':
                return self.dataset.tds_product_name
            if key == 'region':
                return self.dataset.tds_region
            if key == 'spatial_resolution':
                return self.dataset.tds_spatial_resolution
            if key == 'temporal_resolution':
                return self.dataset.tds_temporal_resolution
            if 'YYYYMMDD' in key:
                if key == 'YYYYMMDD':
                    return "{:0>4d}{:02d}{:02d}".format(current_date.year, current_date.month, current_date.day)
                match = re.search(r'(?<=-)\d+', key)
                if match:
                    days_before = int(match.group(0))
                    delta_date = current_date - datetime.timedelta(days=days_before)
                    return "{:0>4d}{:02d}{:02d}".format(delta_date.year, delta_date.month, delta_date.day)
                match = re.search(r'(?<=\+)\d+', key)
                if match:
                    days_after = int(match.group(0))
                    delta_date = current_date + datetime.timedelta(days=days_after)
                    return "{:0>4d}{:02d}{:02d}".format(delta_date.year, delta_date.month, delta_date.day)
            if key == 'YYYY':
                return "{:0>4d}".format(current_date.year)
            if key == 'MM':
                return "{:02d}".format(current_date.month)
            if key == 'DD':
                return "{:02d}".format(current_date.day)
            if key == 'YY':
                return "{:02d}".format(current_date.year % 100)
            if key == 'DK':
                dekad = (current_date.month - 1) * 3
                dekad += min(current_date.day - 1, 29) // 10 + 1
                return f"{dekad:02}"
            if key == 'time':
                return format_params['time_string'] if 'time_string' in format_params else '000000'
        if key == 'temporal_end_date':
            return self.end_date.strftime('%Y-%m-%d')
        if key == 'temporal_start_date':
            return self.start_date.strftime('%Y-%m-%d')
        if key == 'start_date':
            return self.start_date.strftime('%Y%m%d')
        if key == 'end_date':
            return self.end_date.strftime('%Y%m%d')
        if key in ('bbox_west', 'bbox_south', 'bbox_east', 'bbox_north') and self.bbox:
            return str(self.bbox[key.replace('bbox_', '')])

    def format_string(self, pre_format_str, format_params):
        open_bracket_i = pre_format_str.find('{')
        close_bracket_i = pre_format_str.find('}')
        formatted_string = pre_format_str
        while open_bracket_i >= 0 and close_bracket_i >= 0:
            key = formatted_string[open_bracket_i + 1:close_bracket_i]
            formatted_string = formatted_string[0:open_bracket_i] + self.get_replacement_string(key, format_params) + \
                               formatted_string[close_bracket_i + 1:]
            open_bracket_i = formatted_string.find('{')
            close_bracket_i = formatted_string.find('}')
        return formatted_string

    def format_attributes(self, attributes):
        attribute_obj = OrderedDict()

        insertion_order = attributes['insertion_order']

        for attr in insertion_order:
            if attr not in attributes:
                self.log_warning("Attribute {} is not included in the attribute dictionary. "
                                 "Please check your ETL object. Continuing")
                continue
            attribute_obj[attr] = attributes[attr]

        return attribute_obj

    def format_wildcard(self, wildcard):
        end = wildcard.find('=')
        key = wildcard[1:end]
        value = wildcard[end + 1:len(wildcard) - 1]
        if key == 'npdtype':
            return np.dtype(value)
        elif key == 'npfloat':
            return np.float32(value)
        elif key == 'npuint16':
            return np.uint16(value)
        elif key == 'npint8':
            return np.int8(value)
        elif key == 'npint32':
            return np.int32(value)
        elif key == 'npfloat32array':
            arr = json.loads(value)
            new_arr = []
            for val in arr:
                new_arr.append(np.float32(val))
            return new_arr
        elif key == 'tuple':
            num_elems = value.count(",") + 1
            tuple_vals = value.split(", ")
            if not num_elems == len(tuple_vals):
                self.log_error("Tuple improperly formatted. Expected length {}, found {}. Tuple "
                               "string: {}".format(num_elems, len(tuple_vals), value))
                return None
            if len(tuple_vals) == 3:
                return int(tuple_vals[0]), int(tuple_vals[1]), int(tuple_vals[2])
        elif key == 'float':
            return float(value)
        elif key == 'bool':
            return bool(value)
        elif key == 'int':
            return int(value)
        else:
            self.log_error("Specified key-value pair not supported as wildcard.\nKey: {}\nValue: {}".format(key, value))

    def format_encoding(self, encoding):
        encoding_obj = encoding.copy()

        for key in encoding_obj.keys():
            encoding_obj[key] = self.format_wildcard(encoding_obj[key])

        return encoding_obj

    def has_wildcard(self, search_str):
        open_i = search_str.find('{')
        close_i = search_str.find('}')
        return open_i >= 0 and close_i >= 0

    def count_wildcard(self, search_str):
        open_c = search_str.count('{')
        close_c = search_str.count('}')
        if not open_c == close_c:
            self.log_error("Wildcards improperly formatted, found unpaired braces.")
            return 0
        return open_c

    def contains_wildcards(self, search_str, wildcards):
        for wildcard in wildcards:
            if wildcard not in search_str:
                return False
        return True

########################################################################################################################
# UTILITY FUNCTIONS ####################################################################################################
########################################################################################################################

    def set_start_date_from_last_processed(self):
        final_load_dir = self.dataset.final_load_dir
        list_of_files = sorted(filter(os.path.isfile, glob.glob(final_load_dir + '/**/*', recursive=True)))
        if len(list_of_files) != 0:
            last_processed_file = list_of_files[-1]
            date = os.path.basename(last_processed_file).split('.')
            if len(date) > 0:
                year = int(date[1][:4])
                month = int(date[1][4:6])
                day = int(date[1][6:8])
                # Add one to last processed date to avoid override
                self.start_date = datetime.date(year=year, month=month, day=day) + datetime.timedelta(days=1)

                warn_message = 'Pipeline start date was not initialized. Starting from the last ' \
                               'processed date: {}.'.format(self.start_date)
                self.log_warning(warn_message)

                return self.start_date
            else:
                error_message = 'Improperly formatted nc4 file in final load directory: {}'.format(last_processed_file)
                self.log_error(error_message)

                return None
        else:
            return None

    def set_dates(self):
        if self.start_date is None:
            if not self.set_start_date_from_last_processed():
                error_message = 'No start date was provided for this ETL Pipeline Run, and the final load directory ' \
                                'is empty. Please provide an adequate start date to initialize this dataset (run ' \
                                '<python manage.py start_etl_pipeline {ISO_START_DATE}.'
                self.log_error(error_message)

        if self.end_date is None:
            self.end_date = datetime.date.today()

            warn_message = 'Pipeline end date was not initialized. The last day to be processed will be today: ' \
                           '{}.'.format(self.end_date)
            self.log_warning(warn_message)

    def set_start_date_latest_merge(self):
        search_dir = self.dataset.fast_directory_path
        list_of_files = sorted(filter(os.path.isfile, glob.glob(search_dir + '/**/*', recursive=True)))
        if len(list_of_files) != 0:
            last_processed_file = list_of_files[-1]
            self.log_debug("Last merged file found: {}".format(last_processed_file))
            date = os.path.basename(last_processed_file).split('.')
            if len(date) > 0:
                latest_file = xr.open_mfdataset(last_processed_file)
                latest_dates = sorted(latest_file['time'].values)
                self.log_debug("Dates in last merged file: {}".format(latest_dates))
                self.start_date = datetime.datetime.strptime(str(latest_dates[-1])[0:10], "%Y-%m-%d").date()

                warn_message = 'Pipeline start date was not initialized. Starting from the last ' \
                               'merged file timestep: {}'.format(self.start_date)
                self.log_warning(warn_message)

                return self.start_date
            else:
                error_message = 'Improperly formatted nc4 file in final load directory: {}'.format(last_processed_file)
                self.log_error(error_message)

                return None
        else:
            return None

    def set_start_date_first_file(self):
        search_dir = self.dataset.final_load_dir
        list_of_files = sorted(filter(os.path.isfile, glob.glob(search_dir + '/**/*', recursive=True)))
        if len(list_of_files) != 0:
            first_loaded_file = list_of_files[0]
            date = os.path.basename(first_loaded_file).split('.')
            if len(date) > 0:
                year = int(date[1][:4])
                month = int(date[1][4:6])
                day = int(date[1][6:8])
                self.start_date = datetime.date(year=year, month=month, day=day)

                warn_message = 'Pipeline start date was not initialized. Starting from the first ' \
                               'loaded file date: {}.'.format(self.start_date)
                self.log_warning(warn_message)

                return self.start_date
            else:
                error_message = 'Improperly formatted nc4 file in final load directory: {}'.format(
                    last_processed_file)
                self.log_error(error_message)
                return None
        else:
            return None

    def set_dates_for_merge(self):
        if self.start_date is None:
            if not self.set_start_date_latest_merge():
                if not self.set_start_date_first_file():
                    error_message = 'No start date was provided for this ETL Pipeline Run, and the merge and load ' \
                                    'directories are empty. As a merge only dataset, please load new files to properly'\
                                    'run pipeline'
                    self.log_error(error_message)

        if self.end_date is None:
            self.end_date = datetime.date.today()

            warn_message = 'Pipeline end date was not initialized. The last day to be processed will be today: ' \
                           '{}.'.format(self.end_date)
            self.log_warning(warn_message)

    def do_dates_overlap(self, merged_file, new_loads):
        ds = xr.open_mfdataset(merged_file)

        dates_in_files = []
        for new_path in new_loads:
            new_file = xr.open_mfdataset(new_path)
            dates_in_files.append(new_file.time.values[0])

        invalid_dates = [date for date in ds.time.values if np.isin(date, dates_in_files)]
        if invalid_dates:
            self.log_debug("Invalid dates: {}".format(invalid_dates))
            return True
        return False

    # Obsolete
    def remove_invalid_dates(self, merged_file, valid_dates):
        self.log_warning("Deleting invalid dates from merged file {}".format(merged_file))
        with xr.open_mfdataset(merged_file) as ds:
            self.log_debug("Dates to be maintained: {}".format(valid_dates))
            erasure_dates = [date for date in ds.time.values if date not in valid_dates]
            self.log_debug("Dates to be erased: {}".format(erasure_dates))
            with dask.config.set(**{'array.slicing.split_large_chunks': True}):
                valid_ds = ds.sel(time=valid_dates)
            self.log_debug("Final dates in selection: {}".format(valid_ds.time.values))
        valid_ds.to_netcdf(merged_file, unlimited_dims='time')

    def compatible_start_from_files(self, file_a, file_b):
        self.log_debug("Finding compatible start date for files {}, {}".format(file_a, file_b))
        file_a_obj = xr.open_mfdataset(file_a)
        file_a_dates = sorted(file_a_obj['time'].values)
        file_a_start = file_a_dates[0]

        file_b_obj = xr.open_mfdataset(file_b)
        file_b_dates = sorted(file_b_obj['time'].values)
        file_b_start = file_b_dates[0]

        return max(file_a_start, file_b_start)

    def get_scrape_options(self):
        """
        Retrieves scrape options for the granule's primary key.
        """
        granule_info = self.dataset.dataset_information.get('granule_info', {})
        primary_key = granule_info.get('primary_key')
        file_info_by_key = granule_info.get('file_info_by_key', {})

        if not primary_key or primary_key not in file_info_by_key:
            error = f"Missing or invalid file key '{primary_key}' in granule information."
            self.log_error(error)
            raise KeyError(error)

        scrape_options = file_info_by_key[primary_key].get('scrape_options')
        if not scrape_options:
            raise ValueError(f"Missing 'scrape_options' for key '{primary_key}'.")

        return scrape_options


    def get_earthdata_total_pages(self, preformat_source: str, format_params: dict, page_size: int) -> int:
        """
        Fetches the total number of pages from Earthdata API based on the total hits and page size.

        Args:
            preformat_source (str): URL template string.
            format_params (dict): Dictionary containing query parameters for the URL.
            page_size (int): The number of items per page.

        Returns:
            int: Total number of pages.

        Raises:
            ValueError: If the 'CMR-Hits' header is missing or invalid.
            Exception: For other unexpected errors.
        """
        try:
            # Set query params to fetch a single result
            format_params.update({'page_num': 1, 'page_size': 1})

            url = preformat_source.format(**format_params)
            self.log_success(f"Fetching total hits from: {url}")  

            response = requests.get(url)
            response.raise_for_status()

            total_hits = response.headers.get("CMR-Hits")
            if not total_hits or not total_hits.isdigit():
                raise ValueError("Invalid or missing 'CMR-Hits' header in response.")
            total_hits_int = int(total_hits)
            total_pages = math.ceil(total_hits_int / page_size)
            self.log_success(f"Total hits: {total_hits_int}, Total pages: {total_pages}")
            return total_pages
        except requests.RequestException as req_err:
            self.log_error(f"HTTP request error while fetching total pages: {req_err}")
            raise
        except ValueError as val_err:
            self.log_error(f"Value error: {val_err}")
            raise
        except Exception as e:
            self.log_error(f"Unexpected error while getting total page number: {e}")
            raise




    def compatible_end_from_files(self, file_a, file_b):
        self.log_debug("Finding compatible end date for files {}, {}".format(file_a, file_b))
        file_a_obj = xr.open_mfdataset(file_a)
        file_a_dates = sorted(file_a_obj['time'].values)
        file_a_end = file_a_dates[-1]

        file_b_obj = xr.open_mfdataset(file_b)
        file_b_dates = sorted(file_b_obj['time'].values)
        file_b_end = file_b_dates[-1]

        return min(file_a_end, file_b_end)

    def extract_href(self, html_tag):
        return html_tag.get('href')

    def get_begin_date_from_dekad(self, dekad, year):
        dates = [1, 11, 21]
        day = dates[dekad % 3 - 1]
        month = math.ceil(dekad / 3.0)
        return datetime.date(year, month, day)

    def generate_sources(self, preformat_source):
        list_of_sources = []
        if self.has_wildcard(preformat_source):
            num_wildcards = self.count_wildcard(preformat_source)
            if self.contains_wildcards(preformat_source, ['{YYYY}', '{MM}', '{DD}']) and num_wildcards == 3:
                for date in rrule.rrule(rrule.DAILY, dtstart=self.start_date.replace(day=1), until=self.end_date):
                    format_params = {'current_date': date}
                    list_of_sources.append(self.format_string(preformat_source, format_params))
            elif self.contains_wildcards(preformat_source, ['{YYYY}', '{MM}']) and num_wildcards == 2:
                for date in rrule.rrule(rrule.MONTHLY, dtstart=self.start_date.replace(day=1), until=self.end_date):
                    format_params = {'current_date': date}
                    list_of_sources.append(self.format_string(preformat_source, format_params))
            elif self.contains_wildcards(preformat_source, ['{YYYY}']) and num_wildcards == 1:
                for date in rrule.rrule(rrule.YEARLY, dtstart=self.start_date.replace(day=1), until=self.end_date):
                    format_params = {'current_date': date}
                    list_of_sources.append(self.format_string(preformat_source, format_params))
            elif self.contains_wildcards(preformat_source, ['{temporal_start_date}','{temporal_end_date}','{page_num}','{page_size}']) and num_wildcards == 4:
                scrape_options = self.get_scrape_options()
                format_params = {'temporal_start_date':self.start_date,'temporal_end_date':self.end_date}
                total_page_num = self.get_earthdata_total_pages(preformat_source,format_params,scrape_options['page_size'])
                for page in range(1,total_page_num):
                    temp_preformat_source = preformat_source.format(**{'page_num':page,'page_size':scrape_options['page_size'], 'temporal_start_date':'{temporal_start_date}', 'temporal_end_date':'{temporal_end_date}'})
                    list_of_sources.append(self.format_string(temp_preformat_source, format_params))
            elif self.contains_wildcards(preformat_source, ['{bbox_west}']) and self.bbox:
                # API-style URL with bbox placeholders — substitute and return single URL
                format_params = {}
                formatted_url = self.format_string(preformat_source, format_params)
                list_of_sources.append(formatted_url)
            else:
                self.log_error("Error parsing wildcards from preformat source, {}. Currently "
                               "supported wildcards include {{YYYY}}{{MM}} and {{YYYY}}".format(preformat_source))
            return list_of_sources
        return [preformat_source]


    def add_time_indices(self, available_dates, available_granules, time_indices_modifications):
        for date in available_dates:
            file_start_date = date
            file_end_date = date
            if "dynamic_end_date" in time_indices_modifications:
                if time_indices_modifications["dynamic_end_date"] == "dekad":
                    self.log_once("[SUBROUTINES] Modifying end date using dekad date ranges")
                    month = file_start_date.month
                    year = file_start_date.year
                    last_day_of_month = calendar.monthrange(year, month)[1]
                    file_end_date = min((file_start_date + datetime.timedelta(days=10)).date(),
                                        datetime.date(year=year, month=month, day=last_day_of_month))
            start_delta = datetime.timedelta(**time_indices_modifications["timedelta_start"])
            start_time = file_start_date + start_delta
            end_delta = datetime.timedelta(**time_indices_modifications["timedelta_end"])
            end_time = file_end_date + end_delta
            index_delta = datetime.timedelta(**time_indices_modifications["timedelta_ds_time_index"])
            ds_time_index = file_start_date + index_delta
            self.log_once("[VERIFY] Expected time modifications: \n\nstart_date+={}\nend_date+={}\n"
                          "index_date+={}".format(start_delta, end_delta, index_delta))

            available_granules[date]['start_time'] = start_time
            available_granules[date]['end_time'] = end_time
            available_granules[date]['ds_time_index'] = ds_time_index
        return available_granules


    def extract_match_data(self, match, current_source, pathtype, search_string, path_options, key, tiled):
        try:
            time_string = None

            if '<time>' in search_string:
                time_string = match.group('time')
            if '<YY>' in search_string and '<dekad>' in search_string:
                dekad = int(match.group('dekad'))
                year = 2000 + int(match.group('YY'))
                date = self.get_begin_date_from_dekad(dekad, year)
                format_params = {'current_date': date}
                date_string = self.format_string('{YYYYMMDD}', format_params)
            elif '<YYYY>' in search_string and '<MM>' in search_string and '<DD>' in search_string:
                year = int(match.group('YYYY'))
                month = int(match.group('MM'))
                day = int(match.group('DD'))
                date = datetime.date(year, month, day)
                format_params = {'current_date': date}
                date_string = self.format_string('{YYYYMMDD}', format_params)
            elif '<delta>' in search_string and '<YYYY>' in search_string and '<JJJ>' in search_string:
                delta = 28 if match.group('delta') == '4WK' else 84
                year = int(match.group('YYYY'))
                julian_day = int(match.group('JJJ'))
                date = datetime.datetime.strptime('{} {}'.format(julian_day, year), '%j %Y')
                if delta:
                    date = date - datetime.timedelta(days=delta)
                format_params = {'current_date': date}
                date_string = self.format_string('{YYYYMMDD}', format_params)
            elif '<YYYY>' in search_string and '<JJJ>' in search_string:
                year = int(match.group('YYYY'))
                julian_day = int(match.group('JJJ'))
                date = datetime.datetime.strptime('{} {}'.format(julian_day, year), '%j %Y')
                format_params = {'current_date': date}
                date_string = self.format_string('{YYYYMMDD}', format_params)
            elif '<YYYY>' in search_string:
                year = int(match.group('YYYY'))
                julian_day = int(1)
                date = datetime.datetime.strptime('{} {}'.format(julian_day, year), '%j %Y')
                format_params = {'current_date': date}
                date_string = self.format_string('{YYYYMMDD}', format_params)
            else:
                date_string = match.group('date')
            if time_string:
                date = datetime.datetime.strptime(date_string + time_string, '%Y%m%d%H%M%S')
            else:
                date = datetime.datetime.strptime(date_string, '%Y%m%d')
            granule_data = self.initialize_granule(date, time_string)

            if '<version>' in search_string:
                granule_data['version'] = match.group('version')

            download_information = {}

            if tiled:
                if '<modis_h_offset>' and '<modis_v_offset>' in search_string:
                    h_offset = int(match.group('modis_h_offset'))
                    v_offset = int(match.group('modis_v_offset'))
                    tile_info = {
                        'h_offset': h_offset,
                        'v_offset': v_offset
                    }
                    download_information['tile_info'] = tile_info
                else:
                    self.log_error("Dataset is set to tiled, but no tile information was "
                                   "found for this granule")


            filename = match.group('filename')

            if pathtype == 'url':
                source = urllib.parse.urljoin(current_source, filename)
            elif pathtype == 'filesystem':
                source = os.path.join(current_source, filename)
            # Add pathtype for api access, and extract url directly from the match, not from the provided {current_source}
            elif pathtype == 'api':
                source = match.group('https_source')
                pathtype = 'url'
            else:
                self.log_error('Specified pathtype ({}) is not supported'.format(pathtype))
                source = ''

            if '/' in filename:
                download_information['filename'] = filename.split('/')[-1]
            else:
                download_information['filename'] = filename

            download_information['download_type'] = pathtype
            download_information['source'] = source

            if 'session_user' in path_options and 'session_pass' in path_options:
                download_information['session_user'] = path_options['session_user']
                download_information['session_pass'] = path_options['session_pass']

            if 'stream' in path_options:
                download_information['stream'] = True

            if 'earthdata_login' in path_options:
                download_information['earthdata_login'] = True

            granule_data['download_information_by_key'][key] = download_information

            return date, granule_data
        except Exception as error:
            exc_type, exc_obj, exc_tb = sys.exc_info()

            self.log_error('Uncaught exception at line {}: {}'.format(exc_tb.tb_lineno, error))


    def scrape_html_anchor_matches(self, source, scrape_options):
        match_list = []
        pathtype = 'url'
        path_options = {}

        search_string = scrape_options['search_string']

        if 'session_user' in scrape_options and 'session_pass' in scrape_options:
            requester = requests.Session()
            path_options = {
                'session_user': scrape_options['session_user'],
                'session_pass': scrape_options['session_pass']
            }
            requester.auth = (scrape_options['session_user'], scrape_options['session_pass'])
        else:
            requester = requests
        text_body = requester.get(source).text
        file_candidates = BeautifulSoup(text_body, 'html.parser').findAll('a')
        for file_candidate in map(self.extract_href, file_candidates):
            match = re.search(search_string, file_candidate)
            if match:
                match_list.append(match)

        if 'stream' in scrape_options:
            path_options.update({'stream': True})

        if 'earthdata_login' in scrape_options:
            path_options.update({'earthdata_login': True})

        return match_list, pathtype, search_string, path_options

    def scrape_html_text_matches(self, source, scrape_options):
        match_list = []
        pathtype = 'url'
        path_options = {}

        search_string = scrape_options['search_string']

        if 'session_user' in scrape_options and 'session_pass' in scrape_options:
            requester = requests.Session()
            path_options = {
                'session_user': scrape_options['session_user'],
                'session_pass': scrape_options['session_pass']
            }
            requester.auth = (scrape_options['session_user'], scrape_options['session_pass'])
        else:
            requester = requests
        file_candidates = requester.get(source).text.split()
        for file_candidate in file_candidates:
            match = re.search(search_string, file_candidate)
            if match:
                match_list.append(match)

        
        if 'stream' in scrape_options:
            path_options.update({'stream': True})

        if 'earthdata_login' in scrape_options:
            path_options.update({'earthdata_login': True})

        return match_list, pathtype, search_string, path_options

    def scrape_filesystem_matches(self, source, scrape_options):
        match_list = []
        pathtype = 'filesystem'
        path_options = {}

        search_string = scrape_options['search_string']

        if not os.path.isdir(source):
            self.log_error("Could not find expected source directory {}. Please check that the folder exists. "
                           "If the directory was meant to be populated by another script, please check"
                           "that the data pipeline is still functioning as expected.".format(source))
            return [], pathtype, search_string, path_options

        file_candidates = [f for f in os.listdir(source) if re.search(search_string, f)]
        if not len(file_candidates):
            self.log_warning("No file candidates found at location {} with specified search "
                             "string {}".format(source, search_string))

        for file_candidate in file_candidates:
            match = re.search(search_string, file_candidate)
            if match:
                match_list.append(match)

        return match_list, pathtype, search_string, path_options
    
    def extract_value_from_json(self,data, path):
        keys = path.split('.')
        for key in keys:
            try:
                if isinstance(data, list):
                    for obj in data:
                        if key in obj:
                            data = obj[key]
                elif isinstance(data,dict):
                    data=data[key]
            except (KeyError, IndexError):
                logger.warning(f"Key or index '{key}' not found.")
                return None
        return data
    

    def scrape_api(self, source, scrape_options):
        match_list = []
        pathtype = 'api'
        path_options = {}

        search_string = scrape_options.get('search_string')
        api_depth = scrape_options.get('api_depth', '') 
        link_value = scrape_options.get('link_value', '') 

        if 'session_user' in scrape_options and 'session_pass' in scrape_options:
            requester = requests.Session()
            path_options = {
                'session_user': scrape_options['session_user'],
                'session_pass': scrape_options['session_pass']
            }
            requester.auth = (scrape_options['session_user'], scrape_options['session_pass'])
        else:
            requester = requests

        try:
            response = requests.get(source)
            response.raise_for_status()
            data = response.json()
           
            # using api_depth
            links = self.extract_value_from_json(data,api_depth)
           
            for link in links:
                href = link.get(link_value)
                match = re.search(search_string, href)
                if match:
                    match_list.append(match)
        
        except requests.exceptions.RequestException as e:
            logger.error(f"HTTP Request failed: {e}")
            return []
        except ValueError as e:
            logger.error(f"Error parsing JSON: {e}")
            return []
        if 'earthdata_login' in scrape_options:
            path_options.update({'earthdata_login': True})
        return match_list, pathtype, search_string, path_options

    def stitch_tiles(self, download_results, tiling_specification, file_key, date):
        try:
            working_path = self.dataset.temp_working_dir
            tiling_dir = os.path.join(working_path, "{}{}".format(file_key, date.strftime('%Y%m%d')))

            stitched_filepath = os.path.join(working_path, "{}{}_stitched.tif".format(file_key, date.strftime('%Y%m%d')))

            vrt_input_files = []
            vrt_path = os.path.join(tiling_dir, "{}{}.vrt".format(file_key, date.strftime('%Y%m%d')))

            if os.path.exists(tiling_dir):
                for file in os.listdir(tiling_dir):
                    os.remove(os.path.join(tiling_dir, file))
            else:
                os.makedirs(tiling_dir, exist_ok=True)

            all_downloaded_tiles = download_results['additional_download_results']
            all_downloaded_tiles.append({key: download_results[key] for key in download_results if not key == 'additional_download_results'})

            for tile_download_result in all_downloaded_tiles:
                tile_info = tile_download_result['tile_info']

                if 'extract_h5_subset' in tiling_specification:
                    input_path = tile_download_result['path']
                    
                    subdataset_name = tiling_specification['extract_h5_subset']
                    subset_filepath = os.path.join(tiling_dir, os.path.basename(input_path).replace(".h5", "_subset.tif"))\
                    
                    gdal.UseExceptions()
                    hdf_dataset = gdal.Open(input_path)
                    if not hdf_dataset:
                        raise FileNotFoundError(f"Could not open HDF5 file: {input_path}")

                    subdataset = None
                    for name, desc in hdf_dataset.GetSubDatasets():
                        if subdataset_name in name:
                            subdataset = name
                            break

                    if subdataset is None:
                        raise ValueError(f"Subdataset '{subdataset_name}' not found in {input_path}")

                    gdal.Translate(subset_filepath, subdataset)
                    tile_download_result['path'] = subset_filepath
                    self.cleanup_paths.append(subset_filepath)
                    hdf_dataset = None
                
                if 'apply_MODIS_sinusoidal_georeferencing' in tiling_specification:
                    # MODIS Sinusoidal projection (SR-ORG:6974 or EPSG:6842)
                    modis_sinu_proj = 'PROJCS["Sinusoidal",GEOGCS["GCS_unnamed ellipse",DATUM["D_unknown",SPHEROID["Unknown",6371007.181,0]],PRIMEM["Greenwich",0],UNIT["Degree",0.0174532925199433]],PROJECTION["Sinusoidal"],PARAMETER["longitude_of_center",0],PARAMETER["false_easting",0],PARAMETER["false_northing",0],UNIT["Meter",1]]'

                    input_path = tile_download_result['path']

                    h = tile_info['h_offset']
                    v = tile_info['v_offset']

                    # Calculate the upper left coordinates for the tile
                    x_min = (h - 18) * 1111950  # Horizontal offset (in meters)
                    y_max = (9 - v) * 1111950  # Vertical offset (in meters)

                    # The MODIS tile size is 1111950 meters, and we set a negative pixel size for y-axis
                    pixel_size = 463.31271653  # MODIS 500m Sinusoidal grid pixel size
                    geo_transform = (x_min, pixel_size, 0, y_max, 0, -pixel_size)  # Negative y resolution

                    # Open the input TIFF file
                    ds = gdal.Open(input_path)
                    driver = gdal.GetDriverByName('GTiff')

                    # Create a new file with the updated georeferencing info
                    # NOTE: Requires .TIF file, can be updated to include others if needed?
                    georef_filepath = input_path.replace('.tif', '_georef.tif')
                    out_ds = driver.CreateCopy(georef_filepath, ds, 0)

                    # Apply the geotransformation and projection manually
                    out_ds.SetGeoTransform(geo_transform)
                    out_ds.SetProjection(modis_sinu_proj)
                    out_ds = None  # Close the dataset

                    tile_download_result['path'] = georef_filepath
                    self.cleanup_paths.append(georef_filepath)

                final_tilepath = tile_download_result['path']

                vrt_input_files.append(final_tilepath)

            # Create a GDAL VRT (virtual dataset) to handle the mosaic
            vrt_options = gdal.BuildVRTOptions(resampleAlg='nearest', addAlpha=True)
            gdal.BuildVRT(vrt_path, vrt_input_files, options=vrt_options)

            file_options = ["TILED=YES", "BLOCKXSIZE=256", "BLOCKYSIZE=256"]

            # Reproject and output the final stitched file to the desired output path
            warp_options = gdal.WarpOptions(
                dstSRS='EPSG:4326',  # Reproject to WGS84
                format='GTiff',
                resampleAlg='cubic',
                creationOptions=file_options,
                xRes=0.0045000045,
                yRes=0.0045000045
            )

            gdal.Warp(stitched_filepath, vrt_path, options=warp_options)

            # Cleanup temporary VRT file
            self.cleanup_paths.append(vrt_path)
            # NOTE: stitched_filepath is intentionally NOT registered for
            # cleanup — it is the output path returned to the caller below.

            return {"path": stitched_filepath}
        except Exception as error:
            exc_type, exc_obj, exc_tb = sys.exc_info()

            self.log_error('Uncaught exception at line {}: {}'.format(exc_tb.tb_lineno, error))
            raise Exception


########################################################################################################################
# GRANULE FUNCTIONS ####################################################################################################
########################################################################################################################

    def merge_granule_data(self, primary_granule, secondary_granule):
        # TODO check overlapping files or different main components to granule (version, time, etc)
        download_information_by_key = primary_granule['download_information_by_key']
        download_information_by_key.update(secondary_granule['download_information_by_key'])
        primary_granule['download_information_by_key'] = download_information_by_key
        return primary_granule
    

    def add_tile_to_granule(self, existing_granule, additional_granule, file_key):
        if "additional_tile_granules" in existing_granule['download_information_by_key'][file_key]:
            additional_tiles = existing_granule['download_information_by_key'][file_key]["additional_tile_granules"]
        else:
            additional_tiles = []
        
        additional_tiles.append(additional_granule['download_information_by_key'][file_key])
        existing_granule['download_information_by_key'][file_key]["additional_tile_granules"] = additional_tiles

        return existing_granule


    def initialize_granule(self, date, time_string):
        preformat_nc4_filename = "{product}.{YYYYMMDD}T{time}Z.{region}.{spatial_resolution}.{temporal_resolution}.nc4"
        granule_data = {
            'download_information_by_key': {}
        }

        # TODO different format_params by date string
        format_params = {'current_date': date}
        if time_string:
            format_params['time_string'] = time_string
            granule_data['time_string'] = time_string

        nc4_filename = self.format_string(preformat_nc4_filename, format_params)
        granule_data['nc4_filename'] = nc4_filename

        return granule_data

    def granules_from_file_key(self, file_key):
        try:
            scrape_function_by_type = dict(https_anchor=self.scrape_html_anchor_matches,
                                           https_text=self.scrape_html_text_matches,
                                           filesystem=self.scrape_filesystem_matches,
                                           api=self.scrape_api
                                           )

            granule_info = self.dataset.dataset_information['granule_info']
            file_granules_by_key = granule_info['file_info_by_key']
            if file_key not in file_granules_by_key:
                self.log_error("Expected file key {} but did not find granule skeleton for said key.".format(file_key))
            file_granule = file_granules_by_key[file_key]

            available_dates = []
            available_granules = {}

            preformat_source = file_granule['preformat_source']
            source_type = file_granule['source_type']
            scrape_options = file_granule['scrape_options']

            is_tiled = False

            if "tiled" in file_granule.keys():
                is_tiled = True

            source_list = self.generate_sources(preformat_source)

            alternate_base_source = None
            if 'alternate_base_source' in file_granule:
                alternate_base_source = file_granule['alternate_base_source']

            if 'additional_sources' in file_granule:
                for additional_source in file_granule['additional_sources']:
                    source_list.extend(self.generate_sources(additional_source))

            for source in source_list:
                match_list, pathtype, search_string, path_options = scrape_function_by_type[source_type](source,
                                                                                                         scrape_options)
                for match in match_list:
                    base_path = alternate_base_source if alternate_base_source else source
                    date, granule_data = self.extract_match_data(match, base_path, pathtype, search_string,
                                                                 path_options, file_key, is_tiled)                                                                    
                    if date in available_dates:
                        if is_tiled:
                            existing_granule = available_granules[date]
                            new_granule = self.add_tile_to_granule(existing_granule, granule_data, file_key)
                            available_granules[date] = new_granule
                            total_tiles = len(new_granule['download_information_by_key'][file_key]['additional_tile_granules'])
                            self.log_success("Date already has a valid granule. Merged new tile data for "
                                             "date {}. Found {} tiles\n".format(date, total_tiles))
                        else:
                            self.log_warning("Date already has a valid granule. Ignoring granule for "
                                             "date {}. Granule data:\n{}\n".format(date, granule_data))
                            continue
                    elif self.start_date <= date.date() <= self.end_date:
                        self.log_success("Found granule for date {}. Granule data:\n{}\n".format(date, granule_data))
                        available_granules[date] = granule_data
                        available_dates.append(date)

            return available_dates, available_granules
        except Exception as error:
            exc_type, exc_obj, exc_tb = sys.exc_info()

            self.log_error('Uncaught exception at line {}: {}'.format(exc_tb.tb_lineno, error))

    def construct_granules(self):
        available_dates = []
        available_granules = []

        try:
            granule_info = self.dataset.dataset_information['granule_info']
            file_granules_by_key = granule_info['file_info_by_key']

            if "primary_key" not in granule_info:
                self.log_error("Please specify a primary file key for the dataset in the granule skeleton.")
            if "time_indices_modifications" not in granule_info:
                self.log_error("Please specify the time index modifications (start, index, end) "
                               "in the granule skeleton")

            primary_file_key = granule_info['primary_key']
            time_indices_modifications = granule_info['time_indices_modifications']

            available_dates, available_granules = self.granules_from_file_key(primary_file_key)
            available_granules = self.add_time_indices(available_dates, available_granules, time_indices_modifications)

            if 'additional_file_keys' in granule_info:
                for file_key in granule_info['additional_file_keys']:
                    file_granule = file_granules_by_key[file_key]
                    dates_from_new_file, granules_from_new_file = self.granules_from_file_key(file_key)
                    if not available_dates == dates_from_new_file and "missing_ok" not in file_granule:
                        ad_set = set(available_dates)
                        nad_set = set(dates_from_new_file)
                        available_dates = sorted(list(ad_set.intersection(nad_set)))

                        warn_message = 'Not all files available along entire date range. Clipping to compatible dates.'
                        self.log_warning(warn_message)
                    for date in available_dates:
                        if date not in granules_from_new_file:
                            if "missing_ok" in file_granule:
                                # Copy granule data for date
                                new_granule = copy.deepcopy(available_granules[date])

                                # Copy download information, using primary file as template, mark for overwrite
                                # NOTE: Overwrite only compatible with raster file as primary_key
                                download_info_overwrite = \
                                    new_granule['download_information_by_key'][primary_file_key]
                                # TODO Add different overwrite values by dataset
                                download_info_overwrite['overwrite'] = 'nan'
                                self.log_warning("File key {} has been set to overwrite, based on granule "
                                                 "from file key {}".format(file_key, primary_file_key))

                                # Overwrite prior granule information
                                new_granule['download_information_by_key'] = {}
                                new_granule['download_information_by_key'][file_key] = download_info_overwrite

                                granules_from_new_file[date] = new_granule
                            else:
                                self.log_warning("Granule component not found for date {}. Missing "
                                                 "file key: {}".format(date, file_key))
                                continue
                        available_granules[date] = self.merge_granule_data(available_granules[date],
                                                                           granules_from_new_file[date])
        except Exception as error:
            exc_type, exc_obj, exc_tb = sys.exc_info()

            self.log_error('Uncaught exception at line {}: {}'.format(exc_tb.tb_lineno, error))

        # Return output
        return_obj = {
            'available_dates': available_dates,
            'available_granules': available_granules
        }
        return return_obj

    def determine_loaded_files(self):
        final_load_dir = self.dataset.final_load_dir
        list_of_files = sorted(filter(os.path.isfile, glob.glob(final_load_dir + '/**/*', recursive=True)))
        for file in list_of_files:
            date_str = os.path.basename(file).split('.')
            if len(date_str) > 0:
                year = int(date_str[1][:4])
                month = int(date_str[1][4:6])
                day = int(date_str[1][6:8])

                date = datetime.date(year=year, month=month, day=day)
                if self.start_date < date < self.end_date:
                    self.log_success("Found new valid file for date {}".format(date))
                    self.loaded_files.append(file)
        self.log_success("Finished determining new files loaded to final directory")

########################################################################################################################
# VALIDATION FUNCTIONS #################################################################################################
########################################################################################################################

    def query_nc4(self, start_date, end_date, variable, geom, file_list):
        def get_bounds_from_dataset(ds, lat1, lat2, lon1, lon2):
            lat_bounds = ds.sel(latitude=[lat1, lat2], method='nearest').latitude.values
            lon_bounds = ds.sel(longitude=[lon1, lon2], method='nearest').longitude.values
            lat_slice = slice(lat_bounds[0], lat_bounds[1])
            lon_slice = slice(lon_bounds[0], lon_bounds[1])
            return lat_slice, lon_slice

        jsonn = {}
        with open(geom) as infile:
            jsonn = json.load(infile)


        json_aoi = json.dumps(jsonn)
        geo_data_frame = gpd.read_file(json_aoi)
        self.log_debug("Loaded query geometry")

        lon1, lat1, lon2, lat2 = geo_data_frame.total_bounds

        with xr.open_mfdataset(file_list,
                               parallel=True,
                               chunks={'time': 32, 'longitude': 500, 'latitude': 500},
                               autoclose=True) as nc_file:
            self.log_debug("Loaded files {}".format(file_list))
            lat_slice, lon_slice = get_bounds_from_dataset(nc_file, lat1, lat2, lon1, lon2)

            unmasked_data = nc_file[variable].sel(longitude=lon_slice, latitude=lat_slice).sel(
                time=slice(start_date, end_date))
            self.log_debug("Loaded file data for specified dates and roi")
            nc_file.close()

        if jsonn['features'][0]['geometry']['type'] == "Point":
            data = unmasked_data
        else:
            aoi_combined = geo_data_frame.assign(combine=1).dissolve(by='combine', aggfunc='sum')
            bool_mask = rm.mask_3D_geopandas(aoi_combined, unmasked_data, lon_name='longitude',
                                             lat_name='latitude').squeeze(dim='region', drop=True)

            if bool_mask is None:
                data = unmasked_data

            else:
                data = unmasked_data.where(bool_mask)

        dates = data.time.dt.strftime("%Y-%m-%d").values.tolist()

        # TODO Make dynamic
        operation = 'avg'

        if operation == "min":
            ds_vals = data.min(dim=['latitude', 'longitude']).values
            ds_vals[np.isnan(ds_vals)] = -9999
            return dates, ds_vals
        elif operation == "avg":
            ds_vals = data.mean(dim=['latitude', 'longitude']).values
            ds_vals[np.isnan(ds_vals)] = -9999
            return dates, ds_vals
        elif operation == "max":
            ds_vals = data.max(dim=['latitude', 'longitude']).values
            ds_vals[np.isnan(ds_vals)] = -9999
            return dates, ds_vals


########################################################################################################################
# ETL FUNCTIONS ########################################################################################################
########################################################################################################################

    def download_handler(self, download_information, is_recursive_call):
        working_path = self.dataset.temp_working_dir
        has_error = False

        ext = download_information['source'].split('.')[-1]
        requires_extraction = ext in ['zip', 'gz']

        filename = download_information['filename']
        filepath = os.path.join(working_path, filename)
        filedir = os.path.dirname(filepath)


        if not os.path.exists(filedir):
            self.log_warning("Working download directory did not exist. Making new directory: {}".format(filedir))
            os.makedirs(filedir)

        download_type = download_information['download_type']

        requires_overwrite = None
        if "overwrite" in download_information:
            # NOTE: Overwrite only compatible with raster file as primary_key
            self.log_warning("Encountered file key granule requiring overwrite, {}, "
                             "skipping download".format(download_information))
            requires_overwrite = download_information['overwrite']
            download_type = None
            requires_extraction = False

        if download_type:
            self.log_debug("Initializing new download: {}, of download type {}, found at {}. Has extension {} and "
                           "will be loaded here: {}".format(filename, download_type, download_information['source'],
                                                            ext, filepath))

        if download_type == 'url':
            url = download_information['source']

            if 'session_user' in download_information and 'session_pass' in download_information:
                requester = requests.Session()
                requester.auth = (download_information['session_user'], download_information['session_pass'])
            else:
                requester = requests

            if 'earthdata_login' in download_information:
                # NOTE: Assumes requester is of Session class
                r1 = requester.request('get', url)
                r = requester.get(r1.url, auth=requester.auth)
                if r.ok:
                    if not os.path.exists(os.path.dirname(working_path)):
                        self.log_warning("Working directory did not exist. Making new directory.")
                        os.makedirs(os.path.dirname(working_path))
                    with open(filepath, 'wb') as outfile:
                        self.log_success("Downloaded source file at {}".format(filepath))
                        outfile.write(r.content)
                        self.cleanup_paths.append(filepath)
            elif 'stream' in download_information:
                response = requester.get(url, stream=True)
                total_size = int(response.headers.get('content-length', 0))
                block_size = 1024  # 1 KB
                with open(filepath, 'wb') as file:
                    for data in tqdm(response.iter_content(block_size), total=total_size // block_size, unit='KB',
                                     desc=os.path.basename(filepath)):
                        file.write(data)
                self.log_success("Downloaded source file at {}".format(filepath))
            else:
                r = requester.get(url)
                if r.ok:
                    if not os.path.exists(os.path.dirname(working_path)):
                        self.log_warning("Working directory did not exist. Making new directory.")
                        os.makedirs(os.path.dirname(working_path))
                    with open(filepath, 'wb') as outfile:
                        self.log_success("Downloaded source file at {}".format(filepath))
                        outfile.write(r.content)
                        self.cleanup_paths.append(filepath)
                else:
                    self.log_error('Problem fetching remote URL resource: {}'.format(url))
                    self.log_error(r)
                    has_error = True
        elif download_type == 'filesystem':
            filepath = download_information['source']
            if not os.path.isfile(filepath):
                self.log_error("Filepath does not point to a valid file. Please check again: {}".format(filepath))
            self.log_success("Source file location: {}".format(filepath))
        elif download_type:
            error_message = 'Dataset improperly configured, download_type: {} not supported'.format(download_type)
            self.log_error(error_message)

        additional_download_results = []
        if "additional_tile_granules" in download_information:
            self.log_warning("Downloading additional tiles")
            for tile_granule in download_information["additional_tile_granules"]:
                tile_download_result = self.download_handler(tile_granule, True)
                additional_download_results.append(tile_download_result)
        
        if is_recursive_call:
            self.log_warning("May be downloading additional tiles")
        else:
            self.log_warning("No additional tiles found")

        if "tile_info" in download_information:
            tile_info = download_information["tile_info"]
        else:
            tile_info = {}

        return_obj = {
            "has_error": has_error,
            "requires_extraction": requires_extraction,
            "requires_overwrite": requires_overwrite,
            "path": filepath,
            "filename": filename,
            "additional_download_results": additional_download_results,
            "tile_info": tile_info
        }

        self.log_debug("Exiting download handler. Results: {}".format(str(return_obj)))
        return return_obj

    def extraction_handler(self, path, zipped_file_granule, format_params, zipped_file_key):
        self.log_debug("Extracting files from {}".format(path))
        working_path = self.dataset.temp_working_dir
        has_error = False

        ext = path.split('.')[-1]

        return_obj = {
            "has_error": has_error,
            "files": {}
        }

        if ext == 'zip':
            with zipfile.ZipFile(path, "r") as f_in:
                contained_files = f_in.namelist()
                self.log_debug("Contained files {}".format(contained_files))
                zip_files = zipped_file_granule["contained_files"]
                for file_key in zip_files.keys():
                    contained_file_granule = zip_files[file_key]
                    init = True
                    backup_idx = 0
                    filename = self.format_string(contained_file_granule['preformat_filename'], format_params)
                    while init or ("backup_filename" in contained_file_granule and
                                   backup_idx <= len(contained_file_granule['backup_filename'])):
                        if init:
                            init = False
                        else:
                            backup_idx += 1
                        self.log_debug("Attempting to extract {}".format(filename))
                        if filename in contained_files:
                            # Extract a single file from zip
                            f_in.extract(filename, working_path)
                            unzipped_filepath = os.path.join(working_path, filename)
                            contained_file_granule["path"] = unzipped_filepath
                            self.cleanup_paths.append(unzipped_filepath)
                            self.log_success("Extracted {}".format(unzipped_filepath))

                            return_obj["files"][file_key] = contained_file_granule
                            break
                        # TODO Keep track of partial download netcdf4 files created like this
                        filename = self.format_string(zipped_file_granule['backup_filename'][backup_idx], format_params)
        elif ext == 'gz':
            with gzip.open(path, 'rb') as f_in:
                unzipped_file_path = os.path.splitext(path)[0]
                with open(unzipped_file_path, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)
                    self.cleanup_paths.append(unzipped_file_path)
                    zipped_file_granule["path"] = unzipped_file_path
                    return_obj["files"][zipped_file_key] = zipped_file_granule
                    self.log_success("Extracted {}".format(unzipped_file_path))
        else:
            error_message = 'Unsupported extraction extension: {}. Supported extensions: zip, gz'.format(ext)
            self.log_error(error_message)

        return return_obj


    def load_handler(self, working_nc4_filepath, final_nc4_filepath):
        if not os.path.exists(os.path.dirname(final_nc4_filepath)):
            self.log_warning("Final load directory did not exist. Making new directory.")
            os.makedirs(os.path.dirname(final_nc4_filepath))

        # Copy file over to final directory
        shutil.copyfile(working_nc4_filepath, final_nc4_filepath)
        self.cleanup_paths.append(working_nc4_filepath)

        self.log_success('Loaded NC4 File: {}'.format(final_nc4_filepath))
        self.loaded_files.append(final_nc4_filepath)

        # Store duplicate copy for visualization
        duplicate_nc4_filepath = re.sub('/mnt/climateserv/', '/mnt/nvmeclimateserv/', final_nc4_filepath, 1)
        os.makedirs(os.path.dirname(duplicate_nc4_filepath), exist_ok=True)
        shutil.copyfile(working_nc4_filepath, duplicate_nc4_filepath)

        self.log_success('Loaded NVME NC4 File: {}'.format(final_nc4_filepath))


    def merge_handler(self):
        # Merge datasets
        merge_type = self.dataset.merge_type
        final_load_dir = self.dataset.final_load_dir

        try:
            if merge_type == 'Yearly':
                match_str = "{product}.{YYYY}.*T{time}Z.{region}.{spatial_resolution}.{temporal_resolution}.nc4"
                preformat_merged_filename = "{product}.{region}.{spatial_resolution}.{temporal_resolution}.{YYYY}.nc4"
                frequency = 'Y'
            elif merge_type == 'Monthly':
                match_str = "{product}.{YYYY}{MM}.*T{time}Z.{region}.{spatial_resolution}.{temporal_resolution}.nc4"
                preformat_merged_filename = "{product}.{region}.{spatial_resolution}.{temporal_resolution}.{YYYY}{MM}.nc4"
                frequency = 'M'
            else:
                # No merge
                return

            if not os.path.exists(self.dataset.fast_directory_path):
                self.log_warning("Merged directory did not exist. Making new directory.")
                os.makedirs(self.dataset.fast_directory_path)

            pr = pd.period_range(
                start='{}-{}'.format(self.start_date.year, self.start_date.month),
                end='{}-{}'.format(self.end_date.year, self.end_date.month),
                freq=frequency
            )
            pr_tuples = [(period.month, period.year) for period in pr]
            for pr_m, pr_y in pr_tuples:
                merged_date = datetime.date(year=pr_y, month=pr_m, day=1)
                format_params = {'current_date': merged_date}
                pattern = self.format_string(match_str, format_params)
                files = sorted([nc4_file for nc4_file in self.loaded_files if re.search(pattern, nc4_file)])
                if len(files) > 0:
                    merged_filename = self.format_string(preformat_merged_filename, format_params)
                    merged_path = os.path.join(self.dataset.fast_directory_path, merged_filename)
                    generate_file = True
                    if os.path.exists(merged_path):
                        generate_file = False

                        # Delete date overlaps if any
                        if self.do_dates_overlap(merged_path, files):
                            self.log_warning("Date overlap found between {} and at least one of {}. Deleting merged "
                                             "file and merging from scratch".format(merged_path, files))

                            os.remove(merged_path)
                            generate_file = True

                            # Generate list with all files in final load directory that fit this time range
                            all_loaded_files = sorted(filter(os.path.isfile,
                                                             glob.glob(final_load_dir + '/**/*', recursive=True)))
                            files = sorted([nc4_file for nc4_file in all_loaded_files if re.search(pattern, nc4_file)])
                    # Merged file doesn't exist or has been deleted
                    if generate_file:
                        self.log_warning("No prior merged file {} found. Using first available "
                                         "date to generate.".format(merged_path))
                        earliest_nc4_file = files.pop(0)
                        earliest_nc4_path = os.path.join(self.dataset.final_load_dir, earliest_nc4_file)
                        shutil.copyfile(earliest_nc4_path, merged_path)

                        if len(files) == 0:
                            self.log_warning("Generated merged file from single granule; No other relevant granules "
                                             "loaded for {}".format(merged_path))
                            continue

                    # Merge files
                    append_str = " --rec_apn " + " --rec_apn ".join(files)
                    command_str = f'sudo ncrcat -4 -h {append_str} {merged_path}'
                    self.log_debug("Merge command: {}".format(command_str))
                    process = subprocess.Popen(command_str, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    stdout, stderr = process.communicate()

                    self.log_success('Merged files into {}'.format(merged_path))
                else:
                    warn_message = 'No files to merge for pattern {} within indexing a {} ' \
                                   'file on date {}-{}'.format(pattern, frequency, pr_m, pr_y)
                    self.log_warning(warn_message)
        except Exception as error:
            exc_type, exc_obj, exc_tb = sys.exc_info()

            self.log_error('Uncaught exception at line {}: {}'.format(exc_tb.tb_lineno, error))


    def cleanup_handler(self):
        for cleanup_path in self.cleanup_paths:
            self.log_debug('Found temporary file: {}'.format(cleanup_path))

            os.remove(cleanup_path)
            self.log_success('Removed temporary file: {}'.format(cleanup_path))

        self.cleanup_paths = []

########################################################################################################################
# MAIN SCRIPT ##########################################################################################################
########################################################################################################################

    def execute_etl_pipeline(self):
        try:
            if self.dataset.merge_only:
                self.log_success("Dataset is Merge Only Enabled")
                self.set_dates_for_merge()
                self.determine_loaded_files()
                self.merge_handler()
                return

            dataset_information = self.dataset.dataset_information

            # Direct-fetch pipeline path: datasets with fetch_config bypass
            # the legacy granule/download flow entirely.
            if 'fetch_config' in dataset_information.get('granule_info', {}):
                self._direct_fetch_and_load()
                return

            # Legacy flow requires 'data' key in dataset_information.
            # ERA5/AgERA5 configs don't have it — they need CDS API support.
            if 'data' not in dataset_information:
                source_type = ''
                for fi in dataset_information.get('granule_info', {}).get('file_info_by_key', {}).values():
                    source_type = fi.get('source_type', '')
                    break
                raise ValueError(
                    f"Dataset '{self.dataset.dataset_name}' (source_type={source_type}) "
                    f"does not have a 'fetch_config' or legacy 'data' key. "
                    f"This dataset type is not yet supported for on-demand fetching."
                )

            subroutines = dataset_information['subroutines']
            metadata_dict = dataset_information['metadata']
            file_granule_constructors = dataset_information['data']

            self.set_dates()

            self.log_success('Dates set. Range: {}-{}'.format(self.start_date, self.end_date))

            if "adjust_start_date" in subroutines:
                if 'nc_directory_size' in subroutines['adjust_start_date']:
                    forecast_dir = subroutines['adjust_start_date']['nc_directory_size']
                    message = '[SUBROUTINE]: Modifying start date based on forecast file ' \
                              'directory {}.'.format(forecast_dir)
                    self.log_debug(message)

                    list_of_files = sorted(filter(os.path.isfile, glob.glob(forecast_dir + '/*.nc', recursive=False)))
                    last_file = list_of_files[-1]
                    end_date_str = re.search(r'\d{8}', last_file).group(0)
                    year = int(end_date_str[:4])
                    month = int(end_date_str[4:6])
                    day = int(end_date_str[6:8])

                    self.end_date = datetime.date(year=year, month=month, day=day)
                    # Add one to forecast directory size to offset start_date = last_processed + 1
                    # Want to override last forecast file
                    self.start_date = self.start_date - datetime.timedelta(days=len(list_of_files)+1)

                    self.log_once('[SUBROUTINE]: Modified start date: {}'.format(self.start_date))
                    self.log_once('[SUBROUTINE]: Modified end date: {}'.format(self.end_date))

            granules_return_object = self.construct_granules()

            available_dates = granules_return_object['available_dates']
            available_granules = granules_return_object['available_granules']
            available_dates = sorted(available_dates)

            message = 'Found {} granules. Starting ETL for individual granules'.format(len(available_dates))
            self.log_success(message)

            # Loop through list of possible dates
            for nc4_date in available_dates:
                self.log_debug('Processing granule for date: {}'.format(nc4_date))

                self.cleanup_paths = []
                granule = available_granules[nc4_date]

                self.log_debug('Granule information: {}'.format(str(granule)))

                format_params = {'current_date': nc4_date}
                if "time_string" in granule:
                    format_params['time_string'] = granule['time_string']

                downloaded_files = {}
                for file_key in file_granule_constructors.keys():
                    file_granule = copy.deepcopy(file_granule_constructors[str(file_key)])
                    self.log_once("New file granule found: {}".format(file_key))
                    download_result = self.download_handler(granule['download_information_by_key'][file_key], False)
                    if len(download_result["additional_download_results"]) > 0:
                        tiling_specification = subroutines["tiling_specification"]
                        stitch_result = self.stitch_tiles(download_result, tiling_specification, file_key, nc4_date)
                        if "has_error" not in stitch_result:
                            file_granule["path"] = stitch_result["path"]
                            downloaded_files[file_key] = file_granule
                        else:
                            error_message = 'An error occured at the stitching step for ' \
                                            'date, filekey: {}, {}'.format(nc4_date, file_key)
                            self.log_error(error_message)
                    elif not download_result["has_error"]:
                        if download_result["requires_extraction"]:
                            extraction_result = self.extraction_handler(download_result["path"], file_granule,
                                                                        format_params, file_key)
                            downloaded_files.update(extraction_result["files"])
                        else:
                            file_granule["path"] = download_result["path"]
                            if download_result['requires_overwrite']:
                                # NOTE: Passes overwrite value from granule
                                file_granule["overwrite"] = download_result['requires_overwrite']
                            downloaded_files[file_key] = file_granule
                    else:
                        error_message = 'An error occured at the download/extraction step for ' \
                                        'date, filekey: {}, {}'.format(nc4_date, file_key)
                        self.log_error(error_message)
                        continue

                dataset_by_variable = []
                variable_list = []
                encodings_by_variable = {}
                attributes_by_variable = {}
                final_ds = xr.Dataset()

                self.log_debug("Starting agglomeration of downloaded files into final xarray Dataset")
                for file_key in downloaded_files.keys():
                    file_granule = downloaded_files[file_key]
                    variables = file_granule["variables"]
                    path = file_granule["path"]
                    if Path(path).suffix == '.nc':
                        self.log_debug("Opening local xarray Dataset from source NC file: {}".format(path))
                        ds = xr.open_dataset(path)
                        for variable in variables.keys():
                            file_variable_data = variables[variable]
                            profile_number = file_variable_data['profile'] if 'profile' in file_variable_data.keys() \
                                else None
                            original_variable = file_variable_data['original_variable']

                            if profile_number is not None:
                                ds = ds.assign(variables={variable: ds[original_variable][profile_number]})
                            else:
                                ds = ds.assign(variables={variable: ds[original_variable]})
                            self.log_debug("Refactored variable. Original: {}, Modified: {}".format(original_variable,
                                                                                                    variable))
                            variable_list.append(variable)

                            # TODO catch ERRORS if database not properly configured
                            encodings_by_variable[variable] = file_granule["encodings"][variable]
                            attributes_by_variable[variable] = file_granule["attributes"][variable]
                        self.log_debug("Dropping extra variables: {}".format(file_granule['vars_to_drop']))
                        ds = ds.drop_vars(file_granule['vars_to_drop'])
                    else:
                        self.log_debug("Opening local xarray Dataset from source raster file: {}".format(path))
                        file = xr.open_rasterio(path)
                        if "overwrite" in file_granule:
                            if file_granule['overwrite'] == 'nan':
                                self.log_warning("Overwriting file {} data with NaN for "
                                                 "variable {}".format(path, file_key))
                                file.values[:] = np.nan
                            else:
                                self.log_warning("File overwrite is set for variable {} but overwrite "
                                                 "value is not defined".format(file_key))
                        if "rescale_accumulated_precip" in subroutines:
                            self.log_once("[SUBROUTINE]: Rescaling all file arrays")
                            file = file / 10.0
                        file_ds = file.rename(file_key).to_dataset()
                        for band in variables.keys():
                            variable = variables[band]
                            variable_list.append(variable)

                            variable_ds = file_ds.isel(band=int(band)) \
                                                 .reset_coords('band', drop=True) \
                                                 .rename({file_key: variable})
                            self.log_debug("Extracted raster band {}. Refactoring name. Original: {}, "
                                           "Modified: {}".format(band, file_key, variable))

                            dataset_by_variable.append(variable_ds)
                            # TODO catch ERRORS if database not properly configured
                            encodings_by_variable[variable] = file_granule["encodings"][variable]
                            attributes_by_variable[variable] = file_granule["attributes"][variable]
                        self.log_debug("Merging {} file variables into singular "
                                       "dataset".format(len(dataset_by_variable)))
                        if len(dataset_by_variable) == 1:
                            ds = dataset_by_variable[0]
                            self.log_debug("Skipped dataset merge with only one variable present")
                        else:
                            ds = xr.merge(dataset_by_variable)
                    if "clip_values" in subroutines:
                        min_val = subroutines["clip_values"]["min"]
                        max_val = subroutines["clip_values"]["max"]
                        ds[file_key] = ds[file_key].clip(min_val, max_val)
                    if "rescale_ndvi" in subroutines:
                        ds[file_key] = ds[file_key] / 10000.0
                    if len(downloaded_files.keys()) == 1:
                        self.log_debug("Skipped dataset merge with only one file key present")
                        final_ds = ds
                    else:
                        final_ds = xr.merge([final_ds, ds])

                ds = final_ds
                self.dataset_variables = variable_list
                self.log_success("Final dataset generated containing {} variables: {}".format(len(self.dataset_variables), self.dataset_variables))

                # Set default timestamps
                start_time = granule['start_time']
                end_time = granule['end_time']
                ds_time_index = granule['ds_time_index']
                self.log_once("[VERIFY] Sample time indices for date {}:\nstart_time: {}\nend_time: {}\n"
                              "index_time: {}".format(nc4_date, start_time, end_time, ds_time_index))

                # Add the time dimension as a new coordinate.
                ds = ds.assign_coords(time=ds_time_index).expand_dims(dim='time', axis=0)
                ds['time_bnds'] = xr.DataArray(np.array([start_time, end_time]).reshape((1, 2)), dims=['time', 'nbnds'])
                self.log_debug("start_time: {}\nend_time: {}\nindex_time: "
                               "{}".format(start_time, end_time, ds_time_index))

                # 3) Rename and add attributes to this dataset.
                if "lis_manual_lat_lon" in subroutines:
                    self.log_once("[SUBROUTINES]: Manually setting Lat/Lon to LIS bounds")
                    lat_vals = np.round(np.round(np.nanmin(ds.lat.values), 3) + 0.03 * np.arange(0, 2231), 3)
                    lon_vals = np.round(np.round(np.nanmin(ds.lon.values), 3) + 0.03 * np.arange(0, 2351), 3)

                    ds = ds.rename_dims({'north_south': 'latitude', 'east_west': 'longitude'})

                    ds = ds.assign_coords(latitude=lat_vals)
                    ds = ds.assign_coords(longitude=lon_vals)
                    ds = ds.drop_vars(['lat', 'lon'])
                else:
                    ds = ds.rename({'y': 'latitude', 'x': 'longitude'})

                if "round_coords_lat_lon" in subroutines:
                    self.log_once("[SUBROUTINES]: Rounding Lat/Lon coordinates")
                    ds = ds.assign_coords(latitude=np.around(ds.latitude.values, decimals=6),
                                          longitude=np.around(ds.longitude.values, decimals=6))

                # 4) Reorder latitude dimension into ascending order
                if ds.latitude.values[1] - ds.latitude.values[0] < 0:
                    ds = ds.reindex(latitude=ds.latitude[::-1])
                if "reverse_lat_order" in subroutines:
                    self.log_once("[SUBROUTINES]: Reversing latitude order")
                    ds = ds.reindex(latitude=ds.latitude[::-1])

                if "slice_roi" in subroutines:
                    region_range = subroutines["slice_roi"]
                    ds = ds.sel(latitude=slice(region_range[0][0], region_range[0][1]),
                                longitude=slice(region_range[1][0], region_range[1][1]))
                    self.log_once("[SUBROUTINES]: Subsetting by ROI {}".format(region_range))

                # TODO add modifications based on dataset
                lat_attr = OrderedDict([('long_name', 'latitude'), ('units', 'degrees_north'), ('axis', 'Y')])
                lon_attr = OrderedDict([('long_name', 'longitude'), ('units', 'degrees_east'), ('axis', 'X')])

                time_attr = OrderedDict([('long_name', 'time'), ('axis', 'T'), ('bounds', 'time_bnds')])
                time_bounds_attr = OrderedDict([('long_name', 'time_bounds')])

                metadata_dict['south'] = np.min(ds.latitude.values)
                metadata_dict['north'] = np.max(ds.latitude.values)
                metadata_dict['east'] = np.max(ds.longitude.values)
                metadata_dict['west'] = np.min(ds.longitude.values)

                if 'version' in granule:
                    metadata_dict['version'] = granule['version']

                std_file_attr = OrderedDict([('Description', metadata_dict['description']),
                                             ('DateCreated', pd.Timestamp.now().strftime('%Y-%m-%dT%H:%M:%SZ')),
                                             ('Contact', metadata_dict['contact']),
                                             ('Source', metadata_dict['source']),
                                             ('Version', metadata_dict['version']),
                                             ('Reference', metadata_dict['reference']),
                                             ('RangeStartTime', start_time.strftime('%Y-%m-%dT%H:%M:%SZ')),
                                             ('RangeEndTime', end_time.strftime('%Y-%m-%dT%H:%M:%SZ')),
                                             ('SouthernmostLatitude', metadata_dict['south']),
                                             ('NorthernmostLatitude', metadata_dict['north']),
                                             ('WesternmostLongitude', metadata_dict['west']),
                                             ('EasternmostLongitude', metadata_dict['east']),
                                             ('TemporalResolution', metadata_dict['temporal_resolution']),
                                             ('SpatialResolution', metadata_dict['spatial_resolution'])])

                file_attr = OrderedDict()

                for key, value in std_file_attr.items():
                    file_attr[key] = value
                    if "modify_file_attr" in subroutines:
                        if key in subroutines["modify_file_attr"].keys():
                            self.log_once("Adding extra file attributes after key {}".format(key))
                            modification_info = subroutines["modify_file_attr"][key]
                            attr_names = modification_info["insertion_order"]
                            preformat_attr_values = modification_info["attrs_to_insert"]
                            attr_values = self.format_encoding(preformat_attr_values)
                            for added_key in attr_names:
                                self.log_once("Added extra attribute {}:{}".format(added_key, attr_values[added_key]))
                                file_attr[added_key] = attr_values[added_key]

                time_encoding = {'units': 'seconds since 1970-01-01T00:00:00Z', 'dtype': np.dtype('int32')}
                time_bounds_encoding = {'units': 'seconds since 1970-01-01T00:00:00Z', 'dtype': np.dtype('int32')}

                # Set the Attributes
                ds.latitude.attrs = lat_attr
                ds.longitude.attrs = lon_attr

                ds.time.attrs = time_attr
                ds.time_bnds.attrs = time_bounds_attr

                for variable in variable_list:
                    var_attributes = attributes_by_variable[variable]
                    self.log_once("[VERIFY] Expected variable attributes {} : {}".format(variable, var_attributes))
                    ds[variable].attrs = self.format_attributes(var_attributes)

                ds.attrs = file_attr

                # Set the Encodings
                for variable in variable_list:
                    var_encodings = encodings_by_variable[variable]
                    self.log_once("[VERIFY] Expected variable encodings {} : {}".format(variable, var_encodings))
                    ds[variable].encoding = self.format_encoding(var_encodings)

                ds.time.encoding = time_encoding
                ds.time_bnds.encoding = time_bounds_encoding

                
                final_nc4_filename = granule['nc4_filename']

                self.log_debug("Dataset representation:" + str(ds)) 
                self.log_debug("Latitude | Logitude attributes: \n" + str(ds.latitude.attrs) + "|" + str(ds.longitude.attrs))

                nc4_working_path = os.path.join(self.dataset.temp_working_dir, final_nc4_filename)
                ds.to_netcdf(nc4_working_path, unlimited_dims='time')

                # Add to nc4 directory
                nc4_final_path = os.path.join(self.dataset.final_load_dir, final_nc4_filename)
                self.load_handler(nc4_working_path, nc4_final_path)

                # NEW: Optionally load to PostGIS
                if 'load_to_postgis' in subroutines:
                    postgis_config = subroutines['load_to_postgis']
                    con = self._get_postgis_connection(postgis_config)
                    schema = self.target_schema or postgis_config.get('schema', 'dataagent')
                    table_prefix = postgis_config.get('table_prefix', 'weather')
                    for variable in variable_list:
                        table = f'{table_prefix}_{variable}'
                        try:
                            self.load_to_postgis(ds, variable, nc4_date, con, schema, table)
                            self.log_success(f'Loaded {variable} for {nc4_date} to PostGIS {schema}.{table}')
                        except Exception as e:
                            self.log_error(f'Failed to load {variable} for {nc4_date} to PostGIS: {e}')
                    con.close()

                self.first_run = False
                self.cleanup_handler()

            self.merge_handler()
        except Exception as error:
            import traceback
            tb_str = traceback.format_exc()
            exc_type, exc_obj, exc_tb = sys.exc_info()
            logger.error(
                'execute_etl_pipeline FAILED: %s: %s\nFull traceback:\n%s',
                type(error).__name__, error, tb_str,
            )
            self.log_error('Uncaught exception at line {}: {}'.format(exc_tb.tb_lineno, error))

########################################################################################################################
# DATAAGENT EXTENSIONS — PostGIS loading, combining, and output formats
########################################################################################################################

    def _get_postgis_connection(self, postgis_config=None):
        """
        Get a psycopg2 connection to the PostGIS database.

        Resolution order:
        1. ``postgis_config['target_db']`` — per-dataset override stored in the
           dataset_information JSON.  Allows each dataset to target a different
           database host / name (e.g. the MCP Server's database).
        2. Django ``settings.DATABASES['default']``.
        3. Environment variables (DBNAME, DBUSER, PASSWORD, DBHOST, DBPORT).
        """
        if pg is None:
            raise ImportError('psycopg2 is required for PostGIS operations')

        # 1. Per-dataset target_db override
        if postgis_config and 'target_db' in postgis_config:
            tdb = postgis_config['target_db']
            return pg.connect(
                dbname=tdb.get('dbname', os.environ.get('DBNAME')),
                user=tdb.get('user', os.environ.get('DBUSER', 'postgres')),
                password=tdb.get('password', os.environ.get('PASSWORD', '')),
                host=tdb.get('host', os.environ.get('DBHOST', 'localhost')),
                port=tdb.get('port', os.environ.get('DBPORT', '5432')),
            )

        # 2. Django settings
        try:
            from django.conf import settings
            db = settings.DATABASES['default']
            return pg.connect(
                dbname=db['NAME'],
                user=db['USER'],
                password=db['PASSWORD'],
                host=db['HOST'],
                port=db['PORT'],
            )
        except Exception:
            pass

        # 3. Environment variables
        return pg.connect(
            dbname=os.environ.get('DBNAME'),
            user=os.environ.get('DBUSER', 'postgres'),
            password=os.environ.get('PASSWORD', ''),
            host=os.environ.get('DBHOST', 'localhost'),
            port=os.environ.get('DBPORT', '5432'),
        )

    def _ensure_table_exists(self, con, schema, table):
        """
        Ensure the target schema and raster table exist.
        Creates them if they don't, with standard indexes (fdate, rid, spatial).
        """
        cur = con.cursor()
        try:
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS {schema}')
            con.commit()

            # Check if table exists
            cur.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = %s AND table_name = %s",
                (schema, table),
            )
            if cur.fetchone() is None:
                cur.execute(f"""
                    CREATE TABLE {schema}.{table} (
                        rast raster NOT NULL,
                        fdate date NOT NULL,
                        rid serial NOT NULL
                    )
                """)
                cur.execute(f'CREATE INDEX {table}_time ON {schema}.{table} (fdate)')
                cur.execute(f'CREATE INDEX {table}_rid ON {schema}.{table} (rid)')
                cur.execute(
                    f'CREATE INDEX {table}_spatial ON {schema}.{table} '
                    f'USING GIST (ST_Envelope(rast))'
                )
                con.commit()
                logger.info(f'Created table {schema}.{table} with indexes')
        finally:
            cur.close()

    def _ensure_forecast_table_exists(self, con, schema, table):
        """
        Ensure the target schema and forecast raster table exist.
        Like _ensure_table_exists but includes an ens (ensemble member) column.
        """
        cur = con.cursor()
        try:
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS {schema}')
            con.commit()

            cur.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = %s AND table_name = %s",
                (schema, table),
            )
            if cur.fetchone() is None:
                cur.execute(f"""
                    CREATE TABLE {schema}.{table} (
                        rast raster NOT NULL,
                        fdate date NOT NULL,
                        rid serial NOT NULL,
                        ens integer NOT NULL
                    )
                """)
                cur.execute(f'CREATE INDEX {table}_time ON {schema}.{table} (fdate)')
                cur.execute(f'CREATE INDEX {table}_rid ON {schema}.{table} (rid)')
                cur.execute(f'CREATE INDEX {table}_ens ON {schema}.{table} (ens)')
                cur.execute(
                    f'CREATE INDEX {table}_spatial ON {schema}.{table} '
                    f'USING GIST (ST_Envelope(rast))'
                )
                con.commit()
                logger.info(f'Created forecast table {schema}.{table} with ens column')
        finally:
            cur.close()

    def load_to_postgis(self, ds, variable, date, con, schema, table):
        """
        Load a single-date, single-variable xarray DataArray to PostGIS raster.
        Follows the standard tiff_to_db raster-ingestion pattern.

        Parameters
        ----------
        ds : xarray.Dataset
            Dataset containing the variable.
        variable : str
            Variable name to extract from the dataset.
        date : datetime.date
            Date for this time slice.
        con : psycopg2 connection
            Database connection.
        schema : str
            PostGIS schema name.
        table : str
            Target table name.
        """
        import tempfile

        # Ensure schema and table exist before loading
        self._ensure_table_exists(con, schema, table)

        # Extract the variable data array
        if 'time' in ds[variable].dims:
            da = ds[variable].isel(time=0)
        else:
            da = ds[variable]

        # Determine coordinate names
        lat_name = 'latitude' if 'latitude' in da.dims else 'lat'
        lon_name = 'longitude' if 'longitude' in da.dims else 'lon'

        lats = da[lat_name].values
        lons = da[lon_name].values

        # Write to temporary GeoTIFF
        with tempfile.NamedTemporaryFile(suffix='.tif', delete=False) as tmp:
            tiff_path = tmp.name

        try:
            transform = from_bounds(
                lons.min(), lats.min(), lons.max(), lats.max(),
                len(lons), len(lats),
            )

            data = da.values
            # Ensure latitude is in descending order for GeoTIFF convention
            if lats[0] < lats[-1]:
                data = data[::-1, :]

            with rasterio.open(
                tiff_path, 'w', driver='GTiff',
                height=data.shape[0], width=data.shape[1],
                count=1, dtype=data.dtype, crs='EPSG:4326',
                transform=transform,
            ) as dst:
                dst.write(data, 1)

            # Generate random temp table name
            temptable = ''.join(
                random.SystemRandom().choice(string.ascii_lowercase) for _ in range(8)
            )

            cur = con.cursor()

            # Build psql args from Django settings / env vars
            try:
                from django.conf import settings as dj_settings
                db = dj_settings.DATABASES['default']
                _host = db['HOST']
                _port = db['PORT']
                _user = db['USER']
                _pass = db['PASSWORD']
                _dbname = db['NAME']
            except Exception:
                _host = os.environ.get('DBHOST', 'localhost')
                _port = os.environ.get('DBPORT', '5432')
                _user = os.environ.get('DBUSER', 'postgres')
                _pass = os.environ.get('PASSWORD', '')
                _dbname = os.environ.get('DBNAME')

            # raster2pgsql to load into temp table
            cmd = (
                f'raster2pgsql -d -s 4326 -t 10x10 {tiff_path} {temptable}'
                f' | psql -h {_host} -p {_port} -U {_user} -d {_dbname}'
            )
            env = os.environ.copy()
            env['PGPASSWORD'] = _pass
            proc = subprocess.Popen(
                cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                env=env,
            )
            proc.communicate()

            try:
                columns = ['rid', 'rast']

                # Spatial index
                cur.execute(f'CREATE INDEX {temptable}_rid ON {temptable} (rid);')
                cur.execute(
                    f'CREATE INDEX {temptable}_spatial ON {temptable} '
                    f'USING GIST (ST_Envelope(rast));'
                )

                # Add date column
                columns.append('fdate')
                cur.execute(f'ALTER TABLE {temptable} ADD COLUMN fdate DATE')
                date_str = date.strftime('%Y-%m-%d') if hasattr(date, 'strftime') else str(date)[:10]
                cur.execute(f"UPDATE {temptable} SET fdate = date '{date_str}'")
                cur.execute(f'CREATE INDEX {temptable}_time ON {temptable} (fdate);')

                # Insert into permanent table
                col_str = ','.join(columns)
                cur.execute(
                    f'INSERT INTO {schema}.{table}({col_str}) '
                    f'(SELECT {col_str} FROM {temptable});'
                )
                con.commit()
            finally:
                cur.execute(f'DROP TABLE IF EXISTS {temptable};')
                con.commit()
                cur.close()
        finally:
            if os.path.exists(tiff_path):
                os.remove(tiff_path)

    def combine_datasets_subroutine(self, combination_config, con, schema, date_range):
        """
        Combine variables from multiple source PostGIS tables into unified tables.
        Handles regridding when sources have different resolutions.

        Parameters
        ----------
        combination_config : dict
            Configuration with keys: rain_table, tmax_table, tmin_table, srad_table,
            target_resolution, output_prefix.
        con : psycopg2 connection
            Database connection.
        schema : str
            PostGIS schema name.
        date_range : list[datetime.date]
            Dates to process.
        """
        import tempfile

        rain_table = combination_config['rain_table']
        tmax_table = combination_config['tmax_table']
        tmin_table = combination_config['tmin_table']
        srad_table = combination_config['srad_table']
        target_res = combination_config['target_resolution']
        output_prefix = combination_config['output_prefix']

        source_tables = {
            'rain': rain_table,
            'tmax': tmax_table,
            'tmin': tmin_table,
            'srad': srad_table,
        }

        cur = con.cursor()

        for date in date_range:
            date_str = date.strftime('%Y-%m-%d')

            for var_name, src_table in source_tables.items():
                output_table = f'{output_prefix}_{var_name}'

                # Check if already loaded for this date
                cur.execute(
                    f"SELECT COUNT(*) FROM {schema}.{output_table} "
                    f"WHERE fdate = %s", (date_str,)
                )
                if cur.fetchone()[0] > 0:
                    continue

                # Check if source data exists
                cur.execute(
                    f"SELECT COUNT(*) FROM {schema}.{src_table} "
                    f"WHERE fdate = %s", (date_str,)
                )
                if cur.fetchone()[0] == 0:
                    logger.warning(
                        f'No source data in {schema}.{src_table} for {date_str}, skipping'
                    )
                    continue

                # Extract source raster to temp file
                with tempfile.NamedTemporaryFile(suffix='.tif', delete=False) as tmp:
                    src_tiff = tmp.name
                with tempfile.NamedTemporaryFile(suffix='.tif', delete=False) as tmp:
                    regridded_tiff = tmp.name

                try:
                    # Export raster from PostGIS to GeoTIFF
                    cur.execute(
                        f"SELECT ST_AsGDALRaster(ST_Union(rast), 'GTiff') "
                        f"FROM {schema}.{src_table} WHERE fdate = %s",
                        (date_str,)
                    )
                    result = cur.fetchone()
                    if result and result[0]:
                        with open(src_tiff, 'wb') as f:
                            f.write(bytes(result[0]))

                        # Regrid using gdalwarp if needed
                        with rasterio.open(src_tiff) as src:
                            src_res = abs(src.res[0])

                        if abs(src_res - target_res) > 0.001:
                            cmd = (
                                f'gdalwarp -r bilinear -tr {target_res} {target_res} '
                                f'-t_srs EPSG:4326 {src_tiff} {regridded_tiff}'
                            )
                            proc = subprocess.Popen(
                                cmd, shell=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            )
                            proc.communicate()
                            load_tiff = regridded_tiff
                        else:
                            load_tiff = src_tiff

                        # Load regridded raster to combined table
                        temptable = ''.join(
                            random.SystemRandom().choice(string.ascii_lowercase)
                            for _ in range(8)
                        )

                        try:
                            from django.conf import settings as dj_settings
                            _db = dj_settings.DATABASES['default']
                            _h, _p = _db['HOST'], _db['PORT']
                            _u, _pw, _dn = _db['USER'], _db['PASSWORD'], _db['NAME']
                        except Exception:
                            _h = os.environ.get('DBHOST', 'localhost')
                            _p = os.environ.get('DBPORT', '5432')
                            _u = os.environ.get('DBUSER', 'postgres')
                            _pw = os.environ.get('PASSWORD', '')
                            _dn = os.environ.get('DBNAME')

                        cmd = (
                            f'raster2pgsql -d -s 4326 -t 10x10 {load_tiff} {temptable}'
                            f' | psql -h {_h} -p {_p} -U {_u} -d {_dn}'
                        )
                        _env = os.environ.copy()
                        _env['PGPASSWORD'] = _pw
                        proc = subprocess.Popen(
                            cmd, shell=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            env=_env,
                        )
                        proc.communicate()

                        try:
                            cur.execute(f'ALTER TABLE {temptable} ADD COLUMN fdate DATE')
                            cur.execute(
                                f"UPDATE {temptable} SET fdate = date '{date_str}'"
                            )
                            cur.execute(
                                f'INSERT INTO {schema}.{output_table}(rid, rast, fdate) '
                                f'(SELECT rid, rast, fdate FROM {temptable});'
                            )
                            con.commit()
                        finally:
                            cur.execute(f'DROP TABLE IF EXISTS {temptable};')
                            con.commit()
                finally:
                    for path in (src_tiff, regridded_tiff):
                        if os.path.exists(path):
                            os.remove(path)

        cur.close()

    def to_data_table(self, ds_or_con, variables, start_date=None, end_date=None,
                      lat=None, lon=None, bbox=None, source_prefix=None, schema=None):
        """
        Extract a data table from either an xarray Dataset or PostGIS tables.

        Parameters
        ----------
        ds_or_con : xarray.Dataset or psycopg2.connection
            Data source.
        variables : list[str]
            Column names to include (e.g., ['tmax','tmin','rain','srad']).
            Validated against available variables.
        start_date : datetime.date, optional
        end_date : datetime.date, optional
        lat : float, optional
            Single point latitude.
        lon : float, optional
            Single point longitude.
        bbox : tuple, optional
            (west, south, east, north) bounding box.
        source_prefix : str, optional
            PostGIS table prefix (e.g., 'power', 'chirps_chirts_era5').
        schema : str, optional
            PostGIS schema name.

        Returns
        -------
        pd.DataFrame
            Columns: [date, lat, lon, *variables]

        Raises
        ------
        ValueError
            If requested variables are not available in the dataset.
        """
        if isinstance(ds_or_con, xr.Dataset):
            return self._data_table_from_xarray(
                ds_or_con, variables, start_date, end_date, lat, lon, bbox,
            )
        else:
            return self._data_table_from_postgis(
                ds_or_con, variables, start_date, end_date, lat, lon, bbox,
                source_prefix, schema,
            )

    def _data_table_from_xarray(self, ds, variables, start_date, end_date,
                                lat, lon, bbox):
        """Extract data table from xarray Dataset."""
        # Validate variables
        available = list(ds.data_vars)
        missing = [v for v in variables if v not in available]
        if missing:
            raise ValueError(
                f'Variables not available in dataset: {missing}. '
                f'Available: {available}'
            )

        # Select time range
        if start_date and end_date:
            ds = ds.sel(time=slice(str(start_date), str(end_date)))

        # Determine coordinate names
        lat_name = 'latitude' if 'latitude' in ds.dims else 'lat'
        lon_name = 'longitude' if 'longitude' in ds.dims else 'lon'

        # Select spatial subset
        if lat is not None and lon is not None:
            ds = ds.sel(**{lat_name: lat, lon_name: lon}, method='nearest')
        elif bbox:
            west, south, east, north = bbox
            ds = ds.sel(**{
                lat_name: slice(south, north),
                lon_name: slice(west, east),
            })

        # Convert to DataFrame
        df = ds[variables].to_dataframe().reset_index()

        # Normalize column names
        rename_map = {}
        if 'time' in df.columns:
            rename_map['time'] = 'date'
        if lat_name in df.columns and lat_name != 'lat':
            rename_map[lat_name] = 'lat'
        if lon_name in df.columns and lon_name != 'lon':
            rename_map[lon_name] = 'lon'
        if rename_map:
            df = df.rename(columns=rename_map)

        # Select only relevant columns
        cols = [c for c in ['date', 'lat', 'lon'] + variables if c in df.columns]
        return df[cols].dropna(subset=variables, how='all')

    def _data_table_from_postgis(self, con, variables, start_date, end_date,
                                 lat, lon, bbox, source_prefix, schema):
        """Extract data table from PostGIS raster tables."""
        if not source_prefix or not schema:
            raise ValueError('source_prefix and schema are required for PostGIS queries')

        # Validate that tables exist
        cur = con.cursor()
        for var in variables:
            table = f'{source_prefix}_{var}'
            cur.execute(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = %s AND table_name = %s)",
                (schema, table),
            )
            if not cur.fetchone()[0]:
                cur.close()
                raise ValueError(
                    f"Table {schema}.{table} does not exist. "
                    f"Variable '{var}' is not available for source '{source_prefix}'."
                )

        records = []
        date_filter = ''
        params = []
        if start_date:
            date_filter += ' AND fdate >= %s'
            params.append(start_date)
        if end_date:
            date_filter += ' AND fdate <= %s'
            params.append(end_date)

        if lat is not None and lon is not None:
            # Point query
            for var in variables:
                table = f'{source_prefix}_{var}'
                query = (
                    f"SELECT fdate, "
                    f"ST_Value(rast, ST_SetSRID(ST_MakePoint(%s, %s), 4326)) as {var} "
                    f"FROM {schema}.{table} "
                    f"WHERE ST_Intersects(rast, ST_SetSRID(ST_MakePoint(%s, %s), 4326))"
                    f"{date_filter} "
                    f"ORDER BY fdate"
                )
                cur.execute(query, [lon, lat, lon, lat] + params)
                for row in cur.fetchall():
                    records.append({
                        'date': row[0],
                        'lat': lat,
                        'lon': lon,
                        var: row[1],
                    })

            cur.close()

            if not records:
                return pd.DataFrame(columns=['date', 'lat', 'lon'] + variables)

            # Merge records by date
            df = pd.DataFrame(records)
            df = df.groupby(['date', 'lat', 'lon']).first().reset_index()
            # Add NaN columns for any variables not found in the data
            for var in variables:
                if var not in df.columns:
                    df[var] = float('nan')
            return df[['date', 'lat', 'lon'] + variables]

        elif bbox:
            west, south, east, north = bbox
            # Get pixel centroids within bbox for the first variable to establish grid
            var = variables[0]
            table = f'{source_prefix}_{var}'
            query = (
                f"SELECT fdate, "
                f"(ST_PixelAsCentroids(rast)).x as lon, "
                f"(ST_PixelAsCentroids(rast)).y as lat, "
                f"(ST_PixelAsCentroids(rast)).val as {var} "
                f"FROM {schema}.{table} "
                f"WHERE ST_Intersects(rast, "
                f"ST_MakeEnvelope(%s, %s, %s, %s, 4326))"
                f"{date_filter}"
            )
            cur.execute(query, [west, south, east, north] + params)
            df = pd.DataFrame(
                cur.fetchall(), columns=['date', 'lon', 'lat', var],
            )

            # Add remaining variables
            for var in variables[1:]:
                table = f'{source_prefix}_{var}'
                query = (
                    f"SELECT fdate, "
                    f"(ST_PixelAsCentroids(rast)).x as lon, "
                    f"(ST_PixelAsCentroids(rast)).y as lat, "
                    f"(ST_PixelAsCentroids(rast)).val as {var} "
                    f"FROM {schema}.{table} "
                    f"WHERE ST_Intersects(rast, "
                    f"ST_MakeEnvelope(%s, %s, %s, %s, 4326))"
                    f"{date_filter}"
                )
                cur.execute(query, [west, south, east, north] + params)
                var_df = pd.DataFrame(
                    cur.fetchall(), columns=['date', 'lon', 'lat', var],
                )
                df = df.merge(var_df, on=['date', 'lat', 'lon'], how='outer')

            cur.close()
            return df[['date', 'lat', 'lon'] + variables]

        else:
            cur.close()
            raise ValueError('Either (lat, lon) or bbox must be provided')

    # Output format methods

    def to_json(self, df):
        """Convert DataFrame to JSON-serializable dict (records orientation)."""
        import math
        df = df.copy()
        for col in df.columns:
            if df[col].dtype == 'object' or str(df[col].dtype).startswith('date'):
                df[col] = df[col].apply(
                    lambda v: v.isoformat() if hasattr(v, 'isoformat') else v
                )
        records = df.to_dict(orient='records')
        # Sanitize NaN/Inf → None after conversion (pandas keeps NaN in numeric cols)
        for rec in records:
            for key, val in rec.items():
                if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
                    rec[key] = None
        return records

    def to_csv(self, df):
        """Convert DataFrame to CSV string."""
        return df.to_csv(index=False)

    def to_geojson(self, df):
        """
        Convert point-based DataFrame to GeoJSON FeatureCollection.
        Requires 'lat' and 'lon' columns.
        """
        features = []
        for _, row in df.iterrows():
            properties = {
                k: (v.isoformat() if hasattr(v, 'isoformat') else v)
                for k, v in row.items() if k not in ('lat', 'lon')
            }
            features.append({
                'type': 'Feature',
                'geometry': {
                    'type': 'Point',
                    'coordinates': [float(row['lon']), float(row['lat'])],
                },
                'properties': properties,
            })
        return {
            'type': 'FeatureCollection',
            'features': features,
        }

    def to_netcdf4(self, ds, output_path):
        """Write xarray Dataset to NetCDF4."""
        ds.to_netcdf(output_path, unlimited_dims='time')

    # Utility: check data availability in PostGIS

    def check_availability(self, con, schema, source_prefix, variable,
                           start_date=None, end_date=None):
        """
        Check what dates are loaded in PostGIS for a given source/variable.

        Returns
        -------
        list[datetime.date]
            Sorted list of available dates.
        """
        table = f'{source_prefix}_{variable}'
        cur = con.cursor()

        query = f'SELECT DISTINCT fdate FROM {schema}.{table}'
        conditions = []
        params = []
        if start_date:
            conditions.append('fdate >= %s')
            params.append(start_date)
        if end_date:
            conditions.append('fdate <= %s')
            params.append(end_date)

        if conditions:
            query += ' WHERE ' + ' AND '.join(conditions)
        query += ' ORDER BY fdate'

        try:
            cur.execute(query, params)
            dates = [row[0] for row in cur.fetchall()]
        except Exception:
            con.rollback()
            dates = []
        finally:
            cur.close()

        return dates

    # ==========================================================================
    # Direct-fetch pipeline (PRISM, CHIRPS, CHIRTS file-based sources)
    # ==========================================================================

    def _load_tiff_to_postgis(self, tiff_path, date, con, schema, table, ens=None):
        """
        Load a GeoTIFF file directly to a PostGIS raster table.

        Uses pure Python (psycopg2 + ST_FromGDALRaster) — no subprocess calls
        to raster2pgsql or psql.  The GeoTIFF is retiled to 10×10 tiles in
        PostGIS using ST_Tile.

        Parameters
        ----------
        ens : int, optional
            Ensemble member number. When provided, the table must have an ens
            column (created via _ensure_forecast_table_exists).
        """
        if ens is not None:
            self._ensure_forecast_table_exists(con, schema, table)
        else:
            self._ensure_table_exists(con, schema, table)

        with open(tiff_path, 'rb') as f:
            tiff_bytes = f.read()

        date_str = date.strftime('%Y-%m-%d') if hasattr(date, 'strftime') else str(date)[:10]

        cur = con.cursor()
        try:
            # Enable GDAL drivers for this session (PostGIS disables them by default)
            cur.execute("SET postgis.gdal_enabled_drivers = 'ENABLE_ALL'")
            if ens is not None:
                cur.execute(
                    f"INSERT INTO {schema}.{table} (rid, rast, fdate, ens) "
                    f"SELECT row_number() OVER (), tile, %s::date, %s "
                    f"FROM ST_Tile(ST_FromGDALRaster(%s), 10, 10) AS tile",
                    (date_str, ens, pg.Binary(tiff_bytes)),
                )
            else:
                cur.execute(
                    f"INSERT INTO {schema}.{table} (rid, rast, fdate) "
                    f"SELECT row_number() OVER (), tile, %s::date "
                    f"FROM ST_Tile(ST_FromGDALRaster(%s), 10, 10) AS tile",
                    (date_str, pg.Binary(tiff_bytes)),
                )
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            cur.close()

    def _convert_to_geotiff(self, raw_bytes, fmt, tmp_dir):
        """
        Convert downloaded raw bytes to a GeoTIFF file.

        Supported formats:
            zip_bil  – ZIP containing ESRI BIL files (PRISM)
            tif_gz   – gzip-compressed GeoTIFF (CHIRPS)
            tif      – plain GeoTIFF (CHIRTS)

        Returns the path to the resulting GeoTIFF.
        """
        if fmt == 'zip_bil':
            # PRISM switched from ZIP+BIL to COG (GeoTIFF) in Oct 2025.
            # Detect actual format: if the response is not a ZIP, treat
            # it as a plain GeoTIFF.
            if not raw_bytes[:2] == b'PK':
                logger.info(
                    'Expected zip_bil but received non-ZIP data (len=%d, first_bytes=%r); treating as GeoTIFF',
                    len(raw_bytes), raw_bytes[:40],
                )
                tiff_path = os.path.join(tmp_dir, 'data.tif')
                with open(tiff_path, 'wb') as f:
                    f.write(raw_bytes)
                return tiff_path
            zip_path = os.path.join(tmp_dir, 'archive.zip')
            with open(zip_path, 'wb') as f:
                f.write(raw_bytes)
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(tmp_dir)
            # Find raster file — new PRISM API returns .tif, legacy returns .bil
            raster_files = [
                os.path.join(tmp_dir, n) for n in os.listdir(tmp_dir)
                if n.endswith('.bil') or (n.endswith('.tif') and not n.endswith('.aux.xml'))
            ]
            if not raster_files:
                raise FileNotFoundError('No .bil or .tif file found in PRISM archive')
            raster_path = raster_files[0]
            # If it's already a GeoTIFF, return it directly
            if raster_path.endswith('.tif'):
                return raster_path
            tiff_path = os.path.join(tmp_dir, 'output.tif')
            with rasterio.open(raster_path) as src:
                profile = src.profile.copy()
                profile.update(driver='GTiff')
                with rasterio.open(tiff_path, 'w', **profile) as dst:
                    dst.write(src.read())
            return tiff_path

        elif fmt == 'tif_gz':
            gz_path = os.path.join(tmp_dir, 'data.tif.gz')
            with open(gz_path, 'wb') as f:
                f.write(raw_bytes)
            tiff_path = os.path.join(tmp_dir, 'data.tif')
            with gzip.open(gz_path, 'rb') as gz_in:
                with open(tiff_path, 'wb') as f_out:
                    shutil.copyfileobj(gz_in, f_out)
            return tiff_path

        elif fmt == 'tif':
            tiff_path = os.path.join(tmp_dir, 'data.tif')
            with open(tiff_path, 'wb') as f:
                f.write(raw_bytes)
            return tiff_path

        else:
            raise ValueError(f'Unsupported download format: {fmt}')

    def _clip_raster(self, tiff_path, bbox, tmp_dir):
        """
        Clip a GeoTIFF to the instance bounding box using gdalwarp.

        Parameters
        ----------
        tiff_path : str
            Path to the input GeoTIFF.
        bbox : dict
            Bounding box with keys: west, south, east, north.
        tmp_dir : str
            Temporary directory for output.

        Returns the path to the clipped GeoTIFF.
        """
        clipped_path = os.path.join(tmp_dir, 'clipped.tif')
        cmd = (
            f"gdalwarp -te {bbox['west']} {bbox['south']} {bbox['east']} {bbox['north']} "
            f"-overwrite {tiff_path} {clipped_path}"
        )
        proc = subprocess.Popen(
            cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        proc.communicate()
        if os.path.exists(clipped_path):
            return clipped_path
        return tiff_path  # fallback to unclipped if gdalwarp failed

    def _direct_fetch_and_load(self):
        """
        Direct-fetch pipeline for data sources with fetch_config.

        Supports three format types:
        - File-based (zip_bil, tif_gz, tif): one download per variable per day
        - nasa_power_api: single API call returns all variables/dates as JSON,
          converted to GeoTIFFs via numpy+rasterio
        """
        import tempfile

        logger.info(
            '_direct_fetch_and_load entered. bbox type=%s, bbox=%s',
            type(self.bbox).__name__, self.bbox,
        )

        dataset_information = self.dataset.dataset_information
        fetch_config = dataset_information['granule_info']['fetch_config']
        subroutines = dataset_information.get('subroutines', {})
        postgis_config = subroutines.get('load_to_postgis', {})
        schema = self.target_schema or postgis_config.get('schema', 'dataagent')
        table_prefix = postgis_config.get('table_prefix', 'unknown')
        dl_format = fetch_config['format']

        con = self._get_postgis_connection(postgis_config)

        # Build bbox dict from instance bbox if set
        bbox = None
        if self.bbox:
            if isinstance(self.bbox, dict):
                bbox = self.bbox
            else:
                bbox = {
                    'west': self.bbox[0], 'south': self.bbox[1],
                    'east': self.bbox[2], 'north': self.bbox[3],
                }

        self.set_dates()

        # Check for source_groups (combined source with per-group fetch configs)
        source_groups = dataset_information.get('granule_info', {}).get('source_groups')
        if source_groups:
            logger.info(
                'Direct-fetch pipeline (source_groups): %s, dates %s to %s, %d groups',
                table_prefix, self.start_date, self.end_date, len(source_groups),
            )
            for group in source_groups:
                group_fc = group['fetch_config']
                group_format = group_fc['format']
                # Merge group-level unit_conversions with dataset-level subroutines
                group_subs = dict(subroutines)
                if 'unit_conversions' in group:
                    group_subs['unit_conversions'] = group['unit_conversions']
                logger.info(
                    '  Processing source_group %s (format=%s, temporal_res=%s)',
                    group.get('id', '?'), group_format, group.get('temporal_resolution', '?'),
                )
                self._run_pipeline(group_fc, con, schema, table_prefix, bbox, group_subs)
        else:
            logger.info(
                'Direct-fetch pipeline: %s, dates %s to %s, format=%s',
                table_prefix, self.start_date, self.end_date, dl_format,
            )
            self._run_pipeline(fetch_config, con, schema, table_prefix, bbox, subroutines)

        con.close()
        logger.info('Direct-fetch pipeline complete for %s', table_prefix)


    # ======================================================================
    # Config-driven Discover → Extract → Transform → Load pipeline
    # ======================================================================

    def _run_pipeline(self, fetch_config, con, schema, table_prefix, bbox, subroutines):
        """Orchestrate: Discover → Extract → Transform → Load."""
        dl_format = fetch_config['format']
        unit_conversions = subroutines.get('unit_conversions', {})
        variable_map = fetch_config.get('variable_map', {})

        # ── DISCOVER ──
        discover_fn = getattr(self, self.DISCOVERY_METHODS[dl_format])
        manifest = discover_fn(fetch_config, bbox, subroutines, con, schema, table_prefix)
        logger.info('Discovery complete: %d work items for %s', len(manifest), table_prefix)

        if not manifest:
            logger.info('Nothing to process for %s — all data already loaded', table_prefix)
            return

        # ── EXTRACT → TRANSFORM → LOAD ──
        extract_fn = getattr(self, self.EXTRACT_ITERATORS[dl_format])

        record_count = 0
        with tempfile.TemporaryDirectory() as tmp_dir:
            for raw_record in extract_fn(manifest, fetch_config, bbox, subroutines):
                date, ens, source_var, raw_data, geo_info = raw_record
                record_count += 1

                # TRANSFORM
                target_var, data = self._transform_record(
                    source_var, raw_data, unit_conversions, variable_map,
                )
                if data is None:
                    logger.warning(
                        'Transform returned None for %s (source=%s, date=%s) — all-NaN data',
                        target_var, source_var, date,
                    )
                    continue

                # LOAD
                self._load_record(
                    date, ens, target_var, data, geo_info,
                    con, schema, table_prefix, tmp_dir,
                )

        if record_count == 0 and manifest:
            logger.warning(
                'Extract phase yielded 0 records for %s (manifest had %d items)',
                table_prefix, len(manifest),
            )

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _substitute_date_placeholders(self, template, date):
        """Replace {YYYY}, {MM}, {DD}, {YYYYMMDD} in a URL template."""
        return (
            template
            .replace('{YYYYMMDD}', date.strftime('%Y%m%d'))
            .replace('{YYYY}', date.strftime('%Y'))
            .replace('{MM}', date.strftime('%m'))
            .replace('{DD}', date.strftime('%d'))
        )

    def _apply_expand_bbox(self, bbox, subroutines):
        """Apply expand_bbox subroutine — ensure minimum lat/lon range."""
        if not bbox:
            return bbox
        api_bbox = dict(bbox)
        expand_config = subroutines.get('expand_bbox')
        min_range = expand_config.get('min_range', 2.0) if expand_config else 2.0

        lat_range = float(api_bbox['north']) - float(api_bbox['south'])
        lon_range = float(api_bbox['east']) - float(api_bbox['west'])
        if lat_range < min_range:
            expand = (min_range - lat_range) / 2.0 + 0.1
            api_bbox['south'] = round(float(api_bbox['south']) - expand, 4)
            api_bbox['north'] = round(float(api_bbox['north']) + expand, 4)
        if lon_range < min_range:
            expand = (min_range - lon_range) / 2.0 + 0.1
            api_bbox['west'] = round(float(api_bbox['west']) - expand, 4)
            api_bbox['east'] = round(float(api_bbox['east']) + expand, 4)
        return api_bbox

    def _is_loaded(self, con, schema, table, date, ens=None, bbox=None):
        """Check if a specific (date, ens) combo is already in PostGIS.

        When ``bbox`` is provided, only return True if an existing raster's
        envelope fully contains that bbox — so a new raster extending spatial
        coverage is not mistakenly skipped as a duplicate.
        """
        try:
            cur = con.cursor()
            if bbox is not None:
                params = [
                    date,
                    float(bbox['west']), float(bbox['south']),
                    float(bbox['east']), float(bbox['north']),
                ]
                where = (
                    "ST_Contains(ST_Envelope(rast), "
                    "ST_MakeEnvelope(%s, %s, %s, %s, 4326))"
                )
                if ens is not None:
                    cur.execute(
                        f"SELECT 1 FROM {schema}.{table} "
                        f"WHERE fdate = %s AND ens = %s AND {where} LIMIT 1",
                        [date, ens] + params[1:],
                    )
                else:
                    cur.execute(
                        f"SELECT 1 FROM {schema}.{table} "
                        f"WHERE fdate = %s AND {where} LIMIT 1",
                        params,
                    )
            elif ens is not None:
                cur.execute(
                    f"SELECT 1 FROM {schema}.{table} WHERE fdate = %s AND ens = %s LIMIT 1",
                    (date, ens),
                )
            else:
                cur.execute(
                    f"SELECT 1 FROM {schema}.{table} WHERE fdate = %s LIMIT 1",
                    (date,),
                )
            result = cur.fetchone() is not None
            cur.close()
            return result
        except Exception:
            con.rollback()
            return False

    def _get_loaded_dates(self, con, schema, table, ens=None, bbox=None):
        """Return set of dates already loaded for a table.

        When ``bbox`` is provided, only dates whose existing raster envelope
        fully contains that bbox count as "loaded" — so an incoming fetch for
        a region outside the existing coverage is not skipped as a duplicate.
        This lets the ETL extend spatial coverage over time instead of
        treating the first-ingested bbox as canonical.
        """
        try:
            cur = con.cursor()
            if bbox is not None:
                params = [
                    float(bbox['west']), float(bbox['south']),
                    float(bbox['east']), float(bbox['north']),
                ]
                where = (
                    "ST_Contains(ST_Envelope(rast), "
                    "ST_MakeEnvelope(%s, %s, %s, %s, 4326))"
                )
                if ens is not None:
                    cur.execute(
                        f"SELECT DISTINCT fdate FROM {schema}.{table} "
                        f"WHERE ens = %s AND {where}",
                        [ens] + params,
                    )
                else:
                    cur.execute(
                        f"SELECT DISTINCT fdate FROM {schema}.{table} "
                        f"WHERE {where}",
                        params,
                    )
            elif ens is not None:
                cur.execute(
                    f"SELECT DISTINCT fdate FROM {schema}.{table} WHERE ens = %s",
                    (ens,),
                )
            else:
                cur.execute(f"SELECT DISTINCT fdate FROM {schema}.{table}")
            dates = {row[0] for row in cur.fetchall()}
            cur.close()
            return dates
        except Exception:
            con.rollback()
            return set()

    # ------------------------------------------------------------------
    # PROBE — build a single URL for remote availability checks
    # ------------------------------------------------------------------

    def build_probe_url(self, dataset_information, probe_date, end_date=None,
                        bbox=None, lat=None, lon=None):
        """Build ONE probe URL from a dataset's config for remote availability checks.

        When end_date is provided, the URL spans the full date range (for date
        counting).  Otherwise probe_date is used for both start and end.

        Returns dict: {url: str|None, format: str, method: 'HEAD'|'GET', note: str}
        """
        # fetch_config lives inside granule_info (same path as _direct_fetch_and_load)
        granule_info = dataset_information.get('granule_info', {})
        fetch_config = granule_info.get('fetch_config', {})
        subroutines = dataset_information.get('subroutines', {})
        fmt = fetch_config.get('format', 'unknown')
        probe_end = end_date or probe_date

        logger.debug(
            "build_probe_url: format='%s', probe_date=%s, end_date=%s, bbox=%s, lat=%s, lon=%s",
            fmt, probe_date, end_date, bbox, lat, lon,
        )

        if not fetch_config:
            logger.warning("build_probe_url: no 'fetch_config' in granule_info. "
                           "granule_info keys: %s, dataset_information keys: %s",
                           list(granule_info.keys()), list(dataset_information.keys()))
            return {'url': None, 'format': fmt, 'method': 'HEAD', 'note': 'No fetch_config in dataset'}

        if fmt == 'file_download':
            variables = fetch_config.get('variables', [])
            if not variables:
                logger.warning("build_probe_url: file_download format but no variables configured")
                return {'url': None, 'format': fmt, 'method': 'HEAD', 'note': 'No variables configured'}
            template = variables[0]['url_template']
            url = self._substitute_date_placeholders(template, probe_date)
            logger.info("build_probe_url: file_download url_template='%s' -> url='%s'", template, url)
            return {'url': url, 'format': fmt, 'method': 'HEAD', 'note': ''}

        elif fmt == 'regional_json_api':
            variable_map = fetch_config.get('variable_map', {})
            source_var = next(iter(variable_map), None)
            if not source_var:
                logger.warning("build_probe_url: regional_json_api format but no variable_map entries")
                return {'url': None, 'format': fmt, 'method': 'GET', 'note': 'No variables configured'}
            api_bbox = bbox or {'west': 0, 'south': 0, 'east': 1, 'north': 1}
            # Apply expand_bbox (e.g. NASA POWER requires >= 2-degree range)
            api_bbox = self._apply_expand_bbox(api_bbox, subroutines)
            start_str = probe_date.strftime('%Y%m%d')
            end_str = probe_end.strftime('%Y%m%d')
            template = fetch_config['url_template']
            url = (
                template
                .replace('{parameter}', source_var)
                .replace('{bbox_west}', str(api_bbox['west']))
                .replace('{bbox_south}', str(api_bbox['south']))
                .replace('{bbox_east}', str(api_bbox['east']))
                .replace('{bbox_north}', str(api_bbox['north']))
                .replace('{start_date}', start_str)
                .replace('{end_date}', end_str)
            )
            logger.info(
                "build_probe_url: regional_json_api source_var='%s', bbox=%s, "
                "dates='%s..%s' -> url='%s'",
                source_var, api_bbox, start_str, end_str, url,
            )
            return {'url': url, 'format': fmt, 'method': 'GET', 'note': ''}

        elif fmt == 'point_api_grid':
            probe_lat = lat if lat is not None else 0
            probe_lon = lon if lon is not None else 0
            start_str = probe_date.strftime('%Y-%m-%d')
            end_str = probe_end.strftime('%Y-%m-%d')
            template = fetch_config['url_template']
            url = (
                template
                .replace('{lat}', str(probe_lat))
                .replace('{lon}', str(probe_lon))
                .replace('{start_date}', start_str)
                .replace('{end_date}', end_str)
            )
            logger.info(
                "build_probe_url: point_api_grid lat=%s, lon=%s, dates='%s..%s' -> url='%s'",
                probe_lat, probe_lon, start_str, end_str, url,
            )
            return {'url': url, 'format': fmt, 'method': 'GET', 'note': ''}

        elif fmt == 'xarray_slice':
            transport = fetch_config.get('transport', 'opendap')
            if transport == 'cds_api':
                cds_product = fetch_config.get('cds_product', '')
                cds_url = os.environ.get('CDS_API_URL', 'https://cds.climate.copernicus.eu/api')
                cds_key = os.environ.get('CDS_API_KEY', '')
                logger.info(
                    "build_probe_url: xarray_slice cds_api — product='%s', "
                    "CDS_API_KEY=%s, CDS_API_URL='%s'",
                    cds_product,
                    (cds_key[:8] + '...') if cds_key else 'NOT SET',
                    cds_url,
                )
                if not cds_key:
                    return {'url': None, 'format': f'{fmt}/cds_api', 'method': 'HEAD',
                            'note': 'CDS_API_KEY not configured'}
                # Probe the CDS API catalog endpoint
                catalog_url = f"{cds_url.rstrip('/')}/catalogue/v1/collections/{cds_product}"
                logger.info("build_probe_url: probing CDS catalog at %s", catalog_url)
                return {'url': catalog_url, 'format': f'{fmt}/cds_api', 'method': 'GET',
                        'note': f'product: {cds_product}'}
            base_url = fetch_config.get('base_url', '')
            models = fetch_config.get('models', [])
            variable_map = fetch_config.get('variable_map', {})
            source_var = next(iter(variable_map), None)
            model = models[0] if models else ''
            url = f"{base_url}/{model}/{source_var}" if model and source_var else base_url
            logger.info(
                "build_probe_url: xarray_slice opendap base_url='%s', model='%s', var='%s' -> url='%s'",
                base_url, model, source_var, url,
            )
            return {'url': url, 'format': fmt, 'method': 'HEAD', 'note': ''}

        elif fmt == 'grib2_bands':
            base_url = fetch_config.get('base_url', '')
            variable_map = fetch_config.get('variable_map', {})
            grib_var = next(iter(variable_map), None)
            if not grib_var:
                logger.warning("build_probe_url: grib2_bands format but no variable_map entries")
                return {'url': None, 'format': fmt, 'method': 'HEAD', 'note': 'No variables configured'}
            init_str = probe_date.strftime('%Y%m%d')
            url = f'{base_url}/cfs.{init_str}/00/time_grib_01/{grib_var}.01.{init_str}00.daily.grb2'
            logger.info("build_probe_url: grib2_bands var='%s', init='%s' -> url='%s'", grib_var, init_str, url)
            return {'url': url, 'format': fmt, 'method': 'HEAD', 'note': ''}

        logger.warning("build_probe_url: unknown format '%s'", fmt)
        return {'url': None, 'format': fmt, 'method': 'HEAD', 'note': f'Unknown format: {fmt}'}

    # ------------------------------------------------------------------
    # DISCOVER phase — 5 transport-specific discovery methods
    # ------------------------------------------------------------------

    def _discover_file_download(self, fetch_config, bbox, subroutines, con, schema, table_prefix):
        """Discover available file-download granules and filter against PostGIS."""
        variables = fetch_config['variables']
        file_format = fetch_config.get('file_format', 'tif')
        manifest = []
        current_date = self.start_date
        while current_date <= self.end_date:
            for var_entry in variables:
                target_var = var_entry['target_variable']
                table = f'{table_prefix}_{target_var}'
                self._ensure_table_exists(con, schema, table)
                if not self._is_loaded(con, schema, table, current_date):
                    url = self._substitute_date_placeholders(
                        var_entry['url_template'], current_date,
                    )
                    manifest.append({
                        'date': current_date, 'target_var': target_var,
                        'url': url, 'file_format': file_format,
                    })
            current_date += timedelta(days=1)
        return manifest

    def _discover_regional_json_api(self, fetch_config, bbox, subroutines, con, schema, table_prefix):
        """Discover what regional JSON API data needs fetching.

        Respects ``query_limits.max_days_per_request`` — if the requested date
        range exceeds the limit, the range is split into multiple chunks, each
        producing a separate manifest entry for the same variable.
        """
        import datetime as _dt

        variable_map = fetch_config.get('variable_map', {})
        unit_conversions = subroutines.get('unit_conversions', {})
        api_bbox = self._apply_expand_bbox(bbox, subroutines)

        if not api_bbox:
            logger.error('Regional JSON API fetch requires a bounding box')
            return []

        logger.info('Regional JSON API bbox: original=%s, expanded=%s', bbox, api_bbox)

        # Build date chunks respecting query_limits
        q_limits = fetch_config.get('query_limits', {})
        max_days = q_limits.get('max_days_per_request')
        date_chunks = []
        if max_days and (self.end_date - self.start_date).days + 1 > max_days:
            chunk_start = self.start_date
            while chunk_start <= self.end_date:
                chunk_end = min(chunk_start + _dt.timedelta(days=max_days - 1), self.end_date)
                date_chunks.append((chunk_start, chunk_end))
                chunk_start = chunk_end + _dt.timedelta(days=1)
            logger.info('Splitting %d-day range into %d chunks of ≤%d days',
                        (self.end_date - self.start_date).days + 1, len(date_chunks), max_days)
        else:
            date_chunks = [(self.start_date, self.end_date)]

        manifest = []
        for source_var in variable_map:
            conv = unit_conversions.get(source_var, {})
            target_var = conv.get('target_name', variable_map.get(source_var, source_var))
            table = f'{table_prefix}_{target_var}'
            self._ensure_table_exists(con, schema, table)
            loaded_dates = self._get_loaded_dates(con, schema, table, bbox=api_bbox)

            for chunk_start, chunk_end in date_chunks:
                url = (
                    fetch_config['url_template']
                    .replace('{parameter}', source_var)
                    .replace('{bbox_west}', str(api_bbox['west']))
                    .replace('{bbox_south}', str(api_bbox['south']))
                    .replace('{bbox_east}', str(api_bbox['east']))
                    .replace('{bbox_north}', str(api_bbox['north']))
                    .replace('{start_date}', chunk_start.strftime('%Y%m%d'))
                    .replace('{end_date}', chunk_end.strftime('%Y%m%d'))
                )
                manifest.append({
                    'source_var': source_var, 'url': url,
                    'loaded_dates': loaded_dates, 'api_bbox': api_bbox,
                })
        return manifest

    def _discover_point_api_grid(self, fetch_config, bbox, subroutines, con, schema, table_prefix):
        """Discover grid points to query for point-based APIs."""
        if not bbox:
            logger.error('Point API grid fetch requires a bounding box')
            return []

        res = fetch_config.get('grid_resolution', 0.25)
        west, south = float(bbox['west']), float(bbox['south'])
        east, north = float(bbox['east']), float(bbox['north'])

        lons = []
        lon_val = west
        while lon_val <= east:
            lons.append(round(lon_val, 4))
            lon_val += res
        lats = []
        lat_val = north
        while lat_val >= south:
            lats.append(round(lat_val, 4))
            lat_val -= res

        if not lons or not lats:
            return []

        start_str = self.start_date.strftime('%Y-%m-%d')
        end_str = self.end_date.strftime('%Y-%m-%d')

        manifest = []
        for lat in lats:
            for lon in lons:
                url = (
                    fetch_config['url_template']
                    .replace('{lat}', str(lat))
                    .replace('{lon}', str(lon))
                    .replace('{start_date}', start_str)
                    .replace('{end_date}', end_str)
                )
                manifest.append({'lat': lat, 'lon': lon, 'url': url})

        logger.info(
            'Point API grid: %d x %d = %d points', len(lons), len(lats), len(manifest),
        )
        return manifest

    def _discover_xarray_slice(self, fetch_config, bbox, subroutines, con, schema, table_prefix):
        """Discover xarray datasets to open (opendap or cds_api)."""
        transport = fetch_config.get('transport', 'opendap')
        variable_map = fetch_config.get('variable_map', {})
        manifest = []

        if transport == 'opendap':
            for model in fetch_config.get('models', []):
                for source_var in variable_map:
                    url = f"{fetch_config['base_url']}/{model}/{source_var}"
                    manifest.append({
                        'url': url, 'model': model, 'source_var': source_var,
                        'transport': 'opendap',
                    })
        elif transport == 'cds_api':
            cds_groups = fetch_config.get('cds_variable_groups')
            if cds_groups:
                # Split into separate requests per variable group
                # (needed when variables require different request params,
                #  e.g. AgERA5 statistic per variable)
                for group in cds_groups:
                    manifest.append({
                        'cds_product': fetch_config['cds_product'],
                        'cds_variables': group['variables'],
                        'cds_group_extras': group.get('extras', {}),
                        'transport': 'cds_api',
                    })
            else:
                manifest.append({
                    'cds_product': fetch_config['cds_product'],
                    'cds_variables': list(variable_map.keys()),
                    'transport': 'cds_api',
                })
        return manifest

    def _discover_grib2_bands(self, fetch_config, bbox, subroutines, con, schema, table_prefix):
        """Discover available GRIB2 files for forecast data."""
        if not bbox:
            logger.error('GRIB2 fetch requires a bounding box')
            return []

        base_url = fetch_config['base_url']
        variable_map = fetch_config.get('variable_map', {})
        members = fetch_config.get('members', [1, 2, 3, 4])
        init_hours = fetch_config.get('init_hours', ['00'])

        # Use today as init_date; fallback for 404s handled in extract phase
        init_date = datetime.date.today()
        init_str = init_date.strftime('%Y%m%d')

        manifest = []
        for member in members:
            for init_hour in init_hours:
                for grib_var in variable_map:
                    url = (
                        f'{base_url}/cfs.{init_str}/{init_hour}/'
                        f'time_grib_{member:02d}/'
                        f'{grib_var}.{member:02d}.{init_str}{init_hour}.daily.grb2'
                    )
                    manifest.append({
                        'url': url, 'member': member, 'grib_var': grib_var,
                        'init_date': init_date, 'init_hour': init_hour,
                        'base_url': base_url,
                    })

        logger.info(
            'GRIB2 discovery: init_date=%s, %d work items', init_str, len(manifest),
        )
        return manifest

    # ------------------------------------------------------------------
    # EXTRACT phase — 5 transport-specific iterators
    # ------------------------------------------------------------------

    def _extract_file_download(self, manifest, fetch_config, bbox, subroutines):
        """EXTRACT: download files and yield raw records."""
        for item in manifest:
            try:
                logger.info('Fetching %s for %s: %s', item['target_var'], item['date'], item['url'])
                resp = requests.get(item['url'], timeout=120)
                if resp.status_code != 200:
                    logger.warning(
                        'HTTP %d for %s %s, skipping',
                        resp.status_code, item['target_var'], item['date'],
                    )
                    continue

                # Detect non-data responses (e.g. HTML rate-limit pages,
                # plain-text error messages). Binary raster/archive files
                # always have non-printable bytes in the header.
                content_type = resp.headers.get('Content-Type', '')
                head = resp.content[:200]
                is_text = (
                    'text/' in content_type
                    or head.startswith(b'<!DOCTYPE')
                    or head.startswith(b'<html')
                    or (len(head) > 20 and all(32 <= b < 127 or b in (9, 10, 13) for b in head))
                )
                if is_text:
                    logger.warning(
                        'Received text instead of data for %s %s '
                        '(Content-Type=%s, body=%s), skipping',
                        item['target_var'], item['date'],
                        content_type, head.decode('utf-8', errors='replace'),
                    )
                    continue

                with tempfile.TemporaryDirectory() as tmp_dir:
                    tiff_path = self._convert_to_geotiff(resp.content, item['file_format'], tmp_dir)
                    if bbox:
                        tiff_path = self._clip_raster(tiff_path, bbox, tmp_dir)
                    with rasterio.open(tiff_path) as src:
                        data = src.read(1).astype(np.float32)
                        geo_info = {
                            'transform': src.transform,
                            'width': src.width,
                            'height': src.height,
                        }
                yield (item['date'], None, item['target_var'], data, geo_info)
            except Exception as e:
                logger.error('Extract error for %s %s: %s', item['target_var'], item['date'], e)

    def _extract_regional_json_api(self, manifest, fetch_config, bbox, subroutines):
        """EXTRACT: call regional JSON API and yield raw 2D grids per date."""
        res = fetch_config.get('grid_resolution', 0.5)
        nodata = fetch_config.get('nodata_value', -999)

        for item in manifest:
            source_var = item['source_var']
            logger.info('Regional API request for %s: %s', source_var, item['url'])

            try:
                resp = requests.get(item['url'], timeout=300)
                if resp.status_code != 200:
                    logger.error(
                        'Regional API HTTP %d for %s: %s',
                        resp.status_code, source_var, resp.text[:500],
                    )
                    continue
            except Exception as e:
                logger.error('Regional API request failed for %s: %s', source_var, e)
                continue

            try:
                data = resp.json()
            except Exception as e:
                logger.error('Failed to parse regional API response for %s: %s', source_var, e)
                continue

            features = data.get('features', [])
            if not features:
                props = data.get('properties', {})
                if props:
                    features = [data]
            if not features:
                logger.error('No features in response for %s. Keys: %s', source_var, list(data.keys()))
                continue

            logger.info('Regional API returned %d grid points for %s', len(features), source_var)

            # Build coordinate grid and feature lookup
            coords = set()
            feature_lookup = {}
            for feat in features:
                geom = feat.get('geometry', {})
                c = geom.get('coordinates', [])
                if len(c) >= 2:
                    lon_val, lat_val = float(c[0]), float(c[1])
                    coords.add((lon_val, lat_val))
                    key = (round(lon_val, 4), round(lat_val, 4))
                    feature_lookup[key] = feat.get('properties', {}).get('parameter', {})

            if not coords:
                logger.error('No valid coordinates for %s', source_var)
                continue

            lons = sorted(set(c[0] for c in coords))
            lats = sorted(set(c[1] for c in coords), reverse=True)  # N to S

            # Get date keys from first feature
            sample_data = None
            for fp in feature_lookup.values():
                if source_var in fp:
                    sample_data = fp[source_var]
                    break
            if not sample_data:
                logger.warning('No data for %s in response', source_var)
                continue

            date_keys = sorted(sample_data.keys())
            loaded_dates = item.get('loaded_dates', set())

            logger.info(
                'Processing %s: %d dates, %d grid points',
                source_var, len(date_keys), len(feature_lookup),
            )

            for date_key in date_keys:
                try:
                    dt = datetime.date(
                        int(date_key[:4]), int(date_key[4:6]), int(date_key[6:8]),
                    )
                except (ValueError, IndexError):
                    continue

                if dt < self.start_date or dt > self.end_date:
                    continue
                if dt in loaded_dates:
                    continue

                # Build 2D grid from feature data
                grid = np.full((len(lats), len(lons)), np.nan, dtype=np.float32)
                for i, lat in enumerate(lats):
                    for j, lon in enumerate(lons):
                        key = (round(lon, 4), round(lat, 4))
                        fp = feature_lookup.get(key, {})
                        val = fp.get(source_var, {}).get(date_key)
                        if val is not None and val != nodata:
                            grid[i, j] = float(val)

                geo_info = {
                    'lats': lats, 'lons': lons, 'resolution': res,
                }
                yield (dt, None, source_var, grid, geo_info)

    def _extract_point_api_grid(self, manifest, fetch_config, bbox, subroutines):
        """EXTRACT: query per-point API and assemble 2D grids."""
        variable_map = fetch_config.get('variable_map', {})
        res = fetch_config.get('grid_resolution', 0.25)

        # Collect all grid point responses
        all_lons = sorted(set(item['lon'] for item in manifest))
        all_lats = sorted(set(item['lat'] for item in manifest), reverse=True)

        grid_data = {}
        for item in manifest:
            try:
                resp = requests.get(item['url'], timeout=60)
                if resp.status_code != 200:
                    logger.warning(
                        'Point API HTTP %d for (%.2f, %.2f)',
                        resp.status_code, item['lat'], item['lon'],
                    )
                    continue
                grid_data[(item['lon'], item['lat'])] = resp.json()
            except Exception as e:
                logger.warning(
                    'Point API request failed for (%.2f, %.2f): %s',
                    item['lat'], item['lon'], e,
                )

        if not grid_data:
            logger.error('Point API grid: no data retrieved')
            return

        # Determine dates and ensemble members from first response
        sample = next(iter(grid_data.values()))
        daily = sample.get('daily', {})
        dates = daily.get('time', [])

        sample_key = next(iter(variable_map.keys()))
        sample_values = daily.get(sample_key, [])
        if sample_values and isinstance(sample_values[0], list):
            n_members = len(sample_values)
        else:
            n_members = 1

        logger.info('Point API grid: %d dates, %d ensemble members', len(dates), n_members)

        lon_idx = {lon: i for i, lon in enumerate(all_lons)}
        lat_idx = {lat: i for i, lat in enumerate(all_lats)}

        for api_var in variable_map:
            for date_idx, date_str_val in enumerate(dates):
                try:
                    dt = datetime.date.fromisoformat(date_str_val)
                except (ValueError, TypeError):
                    continue
                if dt < self.start_date or dt > self.end_date:
                    continue

                for member in range(n_members):
                    ens_num = member + 1

                    grid = np.full((len(all_lats), len(all_lons)), np.nan, dtype=np.float32)
                    for (lon, lat), point_data in grid_data.items():
                        pd_daily = point_data.get('daily', {})
                        values = pd_daily.get(api_var, [])
                        if n_members > 1 and isinstance(values[0] if values else None, list):
                            val = (
                                values[member][date_idx]
                                if member < len(values) and date_idx < len(values[member])
                                else None
                            )
                        else:
                            val = values[date_idx] if date_idx < len(values) else None
                        if val is not None:
                            li = lat_idx.get(lat)
                            lo = lon_idx.get(lon)
                            if li is not None and lo is not None:
                                grid[li, lo] = float(val)

                    if np.all(np.isnan(grid)):
                        continue

                    geo_info = {
                        'lats': all_lats, 'lons': all_lons, 'resolution': res,
                    }
                    yield (dt, ens_num, api_var, grid, geo_info)

    def _extract_xarray_slices(self, manifest, fetch_config, bbox, subroutines):
        """EXTRACT: open xarray datasets and yield 2D slices."""
        variable_map = fetch_config.get('variable_map', {})
        ensemble_dims = fetch_config.get(
            'ensemble_dims',
            ['ensemble', 'ens', 'member', 'M', 'number', 'realization'],
        )

        ens_counter = 0

        for item in manifest:
            transport = item.get('transport', 'opendap')

            if transport == 'opendap':
                try:
                    ds = xr.open_dataset(item['url'], engine='netcdf4')
                except Exception as e:
                    logger.warning('Failed to open xarray dataset %s: %s', item['url'], e)
                    continue

                source_var = item['source_var']

                try:
                    sel_kwargs = {}
                    if bbox:
                        west, south = float(bbox['west']), float(bbox['south'])
                        east, north = float(bbox['east']), float(bbox['north'])
                        # An xarray label slice must run in the same direction as
                        # the coordinate's index, else .sel returns EMPTY. Many
                        # climate grids are N->S (descending lat), so orient each
                        # slice to the actual coordinate order.
                        lat_idx = ds['lat'].values
                        if len(lat_idx) > 1 and lat_idx[0] > lat_idx[-1]:
                            sel_kwargs['lat'] = slice(north, south)
                        else:
                            sel_kwargs['lat'] = slice(south, north)
                        lon_idx = ds['lon'].values
                        if len(lon_idx) > 1 and lon_idx[0] > lon_idx[-1]:
                            sel_kwargs['lon'] = slice(east, west)
                        else:
                            sel_kwargs['lon'] = slice(west, east)
                    sel_kwargs['time'] = slice(
                        self.start_date.strftime('%Y-%m-%d'),
                        self.end_date.strftime('%Y-%m-%d'),
                    )
                    ds = ds.sel(**sel_kwargs)
                except Exception as e:
                    logger.warning('Failed to slice dataset: %s', e)
                    ds.close()
                    continue

                # Find ensemble dimension
                ens_dim = None
                for dim in ensemble_dims:
                    if dim in ds.dims:
                        ens_dim = dim
                        break
                # ds.sizes (not ds.dims): xarray >=2024 makes Dataset.dims
                # set-like; the dim-name -> length mapping is Dataset.sizes.
                n_ens = ds.sizes.get(ens_dim, 1) if ens_dim else 1

                for var_name in ds.data_vars:
                    if source_var.lower() not in var_name.lower():
                        continue

                    for ei in range(n_ens):
                        ens_counter += 1

                        for ti in range(ds.sizes.get('time', 0)):
                            try:
                                if ens_dim:
                                    arr = ds[var_name].isel(**{ens_dim: ei, 'time': ti})
                                else:
                                    arr = ds[var_name].isel(time=ti)

                                dt = pd.Timestamp(arr.time.values).date()
                                if dt < self.start_date or dt > self.end_date:
                                    continue

                                data = arr.values.astype(np.float32)

                                lat_vals = arr.lat.values
                                lon_vals = arr.lon.values

                                # Flip if lat is ascending (raster expects N→S)
                                if len(lat_vals) > 1 and lat_vals[0] < lat_vals[-1]:
                                    data = data[::-1]
                                    lat_vals = lat_vals[::-1]

                                res_val = abs(lat_vals[1] - lat_vals[0]) if len(lat_vals) > 1 else 1.0
                                geo_info = {
                                    'lats': lat_vals, 'lons': lon_vals,
                                    'resolution': res_val,
                                }
                                yield (dt, ens_counter, source_var, data, geo_info)
                            except Exception as e:
                                logger.error('xarray extract error: %s', e)

                ds.close()

            elif transport == 'cds_api':
                try:
                    import cdsapi
                except ImportError:
                    logger.error('cdsapi required for CDS transport')
                    continue

                with tempfile.TemporaryDirectory() as tmp_dir:
                    nc_path = os.path.join(tmp_dir, 'cds_download.nc')

                    try:
                        cds_key = os.environ.get('CDS_API_KEY', '')
                        cds_url = os.environ.get('CDS_API_URL', 'https://cds.climate.copernicus.eu/api')
                        if cds_key:
                            client = cdsapi.Client(url=cds_url, key=cds_key)
                        else:
                            client = cdsapi.Client()  # Falls back to ~/.cdsapirc
                        request_params = {
                            'variable': item['cds_variables'],
                            'year': self.start_date.strftime('%Y'),
                            'month': self.start_date.strftime('%m'),
                            'data_format': 'netcdf',
                            'download_format': 'unarchived',
                        }
                        # Add day range if configured (needed for ERA5, AgERA5)
                        if fetch_config.get('include_day_range'):
                            days = []
                            d = self.start_date
                            while d <= self.end_date and d.month == self.start_date.month:
                                days.append(f'{d.day:02d}')
                                d += datetime.timedelta(days=1)
                            request_params['day'] = days
                        # Merge product-specific extras (e.g. product_type, time)
                        request_params.update(fetch_config.get('cds_request_extras', {}))
                        # Merge per-group extras (e.g. statistic for AgERA5)
                        request_params.update(item.get('cds_group_extras', {}))
                        if bbox:
                            request_params['area'] = [
                                float(bbox['north']), float(bbox['west']),
                                float(bbox['south']), float(bbox['east']),
                            ]
                        client.retrieve(item['cds_product'], request_params, nc_path)
                    except Exception as e:
                        logger.error('CDS API download failed: %s', e)
                        continue

                    # The new CDS API may wrap downloads in a ZIP archive
                    # even when download_format='unarchived' is requested.
                    # AgERA5 in particular returns one .nc file per requested
                    # day, so we extract every data file and merge them on
                    # the time dimension below.
                    extra_data_paths = []
                    if zipfile.is_zipfile(nc_path):
                        logger.info('CDS download is a ZIP archive — extracting')
                        with zipfile.ZipFile(nc_path) as zf:
                            names = zf.namelist()
                            data_names = [
                                n for n in names
                                if n.endswith(('.nc', '.grib', '.grb', '.grib2'))
                            ] or names[:1]
                            if not data_names:
                                logger.error('ZIP archive from CDS is empty')
                                continue
                            out_dir = os.path.dirname(nc_path)
                            extracted = []
                            for n in data_names:
                                zf.extract(n, out_dir)
                                extracted.append(os.path.join(out_dir, n))
                        if len(extracted) > 1:
                            logger.info(
                                'CDS ZIP contained %d data files — merging',
                                len(extracted),
                            )
                        nc_path = extracted[0]
                        extra_data_paths = extracted[1:]

                    # Open the downloaded file — try NetCDF first, then GRIB
                    datasets = []
                    try:
                        if extra_data_paths:
                            per_file = [xr.open_dataset(p)
                                        for p in [nc_path] + extra_data_paths]
                            time_dim = next(
                                (td for td in ('time', 'valid_time', 'forecast_time')
                                 if td in per_file[0].dims),
                                None,
                            )
                            if time_dim:
                                merged = xr.concat(per_file, dim=time_dim)
                                for d in per_file:
                                    d.close()
                                datasets = [merged]
                            else:
                                # No recognisable time dim — fall back to first file
                                logger.warning(
                                    'Multi-file CDS ZIP has no time dim; '
                                    'using only %s', os.path.basename(nc_path),
                                )
                                datasets = [per_file[0]]
                                for d in per_file[1:]:
                                    d.close()
                        else:
                            datasets = [xr.open_dataset(nc_path)]
                        logger.info(
                            'Opened NetCDF: vars=%s, dims=%s',
                            list(datasets[0].data_vars), dict(datasets[0].sizes),
                        )
                    except Exception as e1:
                        logger.info('NetCDF open failed (%s), trying GRIB...', e1)
                        # CDS may return GRIB despite requesting NetCDF.
                        # cfgrib splits multi-variable GRIB files into
                        # separate datasets (one per parameter group), so
                        # use cfgrib.open_datasets() to get them all.
                        try:
                            import cfgrib
                            datasets = cfgrib.open_datasets(nc_path)
                            logger.info('Opened GRIB with %d dataset group(s)', len(datasets))
                        except Exception as e2:
                            logger.error('Failed to open CDS download (tried netcdf4 and cfgrib): %s', e2)
                            continue

                    if not datasets:
                        logger.error('No datasets obtained from CDS download')
                        continue

                    # Process each dataset group (usually 1 for NetCDF,
                    # possibly multiple for GRIB)
                    for ds in datasets:
                        # Find ensemble dimension
                        ens_dim = None
                        for dim in ensemble_dims:
                            if dim in ds.dims:
                                ens_dim = dim
                                break
                        n_ens = ds.sizes.get(ens_dim, 1) if ens_dim else 1
                        logger.info('CDS dataset: %d ensemble members, dims=%s, vars=%s',
                                    n_ens, list(ds.sizes), list(ds.data_vars))

                        # ── Daily aggregation path (ERA5 hourly → daily) ──
                        daily_agg = fetch_config.get('daily_aggregation')
                        if daily_agg:
                            yield from self._cds_daily_aggregation(ds, daily_agg)
                            ds.close()
                            continue

                        # ── Standard per-timestep path (SEAS5, AgERA5, etc.) ──
                        # Map dataset variables to source variable names
                        matched_any = False
                        for ds_var in ds.data_vars:
                            source_var = None
                            for cds_var in variable_map:
                                if self._match_cds_var(ds_var, cds_var):
                                    source_var = cds_var
                                    break
                            if source_var is None:
                                logger.debug('CDS: no variable_map match for dataset var %r', ds_var)
                                continue
                            matched_any = True

                            # Handle multi-target variables (e.g. 2m_temperature → [tmax, tmin])
                            target_vars = variable_map[source_var]
                            if isinstance(target_vars, list):
                                ds_var_lower = ds_var.lower()
                                if 'max' in ds_var_lower or 'mx' in ds_var_lower:
                                    yield_source = f'{source_var}_max'
                                elif 'min' in ds_var_lower or 'mn' in ds_var_lower:
                                    yield_source = f'{source_var}_min'
                                else:
                                    yield_source = source_var
                            else:
                                yield_source = source_var

                            time_dim = None
                            for td in ['time', 'forecast_time', 'valid_time', 'step']:
                                if td in ds.dims:
                                    time_dim = td
                                    break
                            if time_dim is None:
                                continue

                            for ei in range(n_ens):
                                ens_num = ei + 1

                                for ti in range(ds.sizes[time_dim]):
                                    try:
                                        sel = {time_dim: ti}
                                        if ens_dim:
                                            sel[ens_dim] = ei

                                        arr = ds[ds_var].isel(**sel)
                                        time_coord = (arr.coords.get('time')
                                                      or arr.coords.get('valid_time')
                                                      or arr.coords.get('forecast_time'))
                                        if time_coord is not None:
                                            dt = pd.Timestamp(time_coord.values).date()
                                        else:
                                            continue

                                        if dt < self.start_date or dt > self.end_date:
                                            continue

                                        data = arr.values.astype(np.float32)

                                        lat_key = 'latitude' if 'latitude' in arr.coords else 'lat'
                                        lon_key = 'longitude' if 'longitude' in arr.coords else 'lon'
                                        lat_vals = arr[lat_key].values
                                        lon_vals = arr[lon_key].values

                                        if len(lat_vals) > 1 and lat_vals[0] < lat_vals[-1]:
                                            data = data[::-1]
                                            lat_vals = lat_vals[::-1]

                                        res_val = abs(lat_vals[1] - lat_vals[0]) if len(lat_vals) > 1 else 1.0
                                        geo_info = {
                                            'lats': lat_vals, 'lons': lon_vals,
                                            'resolution': res_val,
                                        }
                                        yield (dt, ens_num, yield_source, data, geo_info)
                                    except Exception as e:
                                        logger.error('CDS extract error for %s ens=%d: %s', yield_source, ens_num, e)

                        if not matched_any:
                            logger.warning(
                                'CDS: no dataset variables matched variable_map. '
                                'Dataset vars=%s, variable_map keys=%s',
                                list(ds.data_vars), list(variable_map.keys()),
                            )
                        ds.close()

    # Mapping of common CDS variable names to their GRIB short names.
    # Used to match variables when CDS returns GRIB instead of NetCDF.
    _CDS_TO_GRIB = {
        '2m_temperature': 't2m',
        'total_precipitation': 'tp',
        'surface_solar_radiation_downwards': 'ssrd',
        'maximum_2m_temperature_in_the_last_24_hours': 'mx2t24',
        'minimum_2m_temperature_in_the_last_24_hours': 'mn2t24',
        '10m_u_component_of_wind': 'u10',
        '10m_v_component_of_wind': 'v10',
        'mean_sea_level_pressure': 'msl',
        'sea_surface_temperature': 'sst',
        'snow_depth': 'sd',
    }
    _GRIB_TO_CDS = {v: k for k, v in _CDS_TO_GRIB.items()}

    @classmethod
    def _match_cds_var(cls, ds_var, cds_var):
        """Check if a dataset variable name matches a CDS variable name.

        Handles NetCDF names, GRIB short names, and AgERA5-style long names
        (e.g. Temperature_Air_2m_Max_24h matching 2m_temperature).
        """
        ds_norm = ds_var.replace('_', '').lower()
        cds_norm = cds_var.replace('_', '').lower()
        # Direct fuzzy match (NetCDF names)
        if cds_norm in ds_norm:
            return True
        # GRIB short name match
        grib_name = cls._CDS_TO_GRIB.get(cds_var)
        if grib_name and ds_var.lower() == grib_name.lower():
            return True
        # Token-based match: all significant CDS tokens appear in the
        # dataset variable name (handles AgERA5-style long names like
        # Temperature_Air_2m_Max_24h matching 2m_temperature).
        cds_tokens = [t for t in cds_var.lower().split('_') if len(t) > 1]
        if cds_tokens:
            ds_lower = ds_var.lower()
            if all(t in ds_lower for t in cds_tokens):
                return True
        return False

    def _cds_daily_aggregation(self, ds, daily_agg):
        """Aggregate sub-daily CDS data (e.g. ERA5 hourly) to daily records.

        daily_agg maps yield-names to aggregation specs:
            {
                't2m_max': {'cds_var': '2m_temperature', 'method': 'max'},
                't2m_min': {'cds_var': '2m_temperature', 'method': 'min'},
                'total_precipitation': {'cds_var': 'total_precipitation', 'method': 'sum'},
            }
        """
        # Build lookup: dataset_var_name → [(yield_name, method), ...]
        agg_tasks = {}
        unmatched = []
        for yield_name, spec in daily_agg.items():
            cds_var = spec['cds_var']
            matched = False
            for dv in ds.data_vars:
                if self._match_cds_var(dv, cds_var):
                    agg_tasks.setdefault(dv, []).append((yield_name, spec['method']))
                    matched = True
                    break
            if not matched:
                unmatched.append(yield_name)

        if not agg_tasks:
            logger.warning(
                'Daily aggregation: no dataset variables matched. '
                'Dataset vars=%s, expected CDS vars=%s',
                list(ds.data_vars),
                [spec['cds_var'] for spec in daily_agg.values()],
            )
            return

        if unmatched:
            # Partial match is normal for GRIB files (split across datasets)
            logger.debug(
                'Daily aggregation: %d/%d entries matched this dataset. '
                'Unmatched: %s, dataset vars=%s',
                len(daily_agg) - len(unmatched), len(daily_agg),
                unmatched, list(ds.data_vars),
            )

        logger.info(
            'Daily aggregation: matched %d dataset vars: %s',
            len(agg_tasks),
            {dv: [(yn, m) for yn, m in t] for dv, t in agg_tasks.items()},
        )

        for ds_var, tasks in agg_tasks.items():
            da = ds[ds_var]

            # Determine lat/lon coordinate names
            lat_key = 'latitude' if 'latitude' in da.coords else 'lat'
            lon_key = 'longitude' if 'longitude' in da.coords else 'lon'

            # Find the time dimension name (cfgrib may use 'valid_time' or 'step')
            resample_dim = None
            for td in ['time', 'valid_time']:
                if td in da.dims:
                    resample_dim = td
                    break
            if resample_dim is None:
                logger.warning(
                    'No time dimension found for daily aggregation of %s (dims=%s)',
                    ds_var, list(da.dims),
                )
                continue

            for yield_name, method in tasks:
                try:
                    daily = da.resample({resample_dim: '1D'})
                    if method == 'max':
                        agg_result = daily.max()
                    elif method == 'min':
                        agg_result = daily.min()
                    elif method == 'sum':
                        agg_result = daily.sum()
                    elif method == 'mean':
                        agg_result = daily.mean()
                    else:
                        logger.warning('Unknown aggregation method %s, skipping', method)
                        continue
                except Exception as e:
                    logger.error('Daily aggregation failed for %s: %s', yield_name, e)
                    continue

                n_times = agg_result.sizes.get(resample_dim, 0)
                logger.info(
                    'Daily aggregation for %s (%s.%s): %d daily timesteps',
                    yield_name, ds_var, method, n_times,
                )
                for ti in range(n_times):
                    try:
                        arr = agg_result.isel({resample_dim: ti})
                        time_val = arr.coords.get(resample_dim)
                        dt = pd.Timestamp(time_val.values).date()

                        if dt < self.start_date or dt > self.end_date:
                            logger.debug(
                                'Skipping %s date %s (outside %s-%s)',
                                yield_name, dt, self.start_date, self.end_date,
                            )
                            continue

                        data = arr.values.astype(np.float32)
                        lat_vals = arr[lat_key].values
                        lon_vals = arr[lon_key].values

                        if len(lat_vals) > 1 and lat_vals[0] < lat_vals[-1]:
                            data = data[::-1]
                            lat_vals = lat_vals[::-1]

                        res_val = abs(lat_vals[1] - lat_vals[0]) if len(lat_vals) > 1 else 1.0
                        geo_info = {
                            'lats': lat_vals, 'lons': lon_vals,
                            'resolution': res_val,
                        }
                        yield (dt, None, yield_name, data, geo_info)
                    except Exception as e:
                        logger.error('CDS daily agg extract error for %s: %s', yield_name, e)

    def _extract_grib2_bands(self, manifest, fetch_config, bbox, subroutines):
        """EXTRACT: download GRIB2 files and yield per-band records."""
        base_url = fetch_config['base_url']

        if bbox:
            west, south = float(bbox['west']), float(bbox['south'])
            east, north = float(bbox['east']), float(bbox['north'])

        for item in manifest:
            resp = None
            try:
                resp = requests.get(item['url'], timeout=300, stream=True)
                if resp.status_code != 200:
                    # Try older init dates
                    found = False
                    today = datetime.date.today()
                    for days_back in range(1, 8):
                        alt_date = today - timedelta(days=days_back)
                        alt_str = alt_date.strftime('%Y%m%d')
                        alt_url = (
                            f'{base_url}/cfs.{alt_str}/{item["init_hour"]}/'
                            f'time_grib_{item["member"]:02d}/'
                            f'{item["grib_var"]}.{item["member"]:02d}.{alt_str}{item["init_hour"]}.daily.grb2'
                        )
                        resp = requests.get(alt_url, timeout=300, stream=True)
                        if resp.status_code == 200:
                            logger.info('GRIB2: found data at init_date=%s', alt_str)
                            found = True
                            break
                    if not found:
                        logger.warning(
                            'GRIB2: no data found for %s member %d',
                            item['grib_var'], item['member'],
                        )
                        continue
            except Exception as e:
                logger.error('GRIB2 download failed for %s: %s', item['grib_var'], e)
                continue

            with tempfile.TemporaryDirectory() as tmp_dir:
                grib_path = os.path.join(tmp_dir, f'{item["grib_var"]}_{item["member"]}.grb2')
                with open(grib_path, 'wb') as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        f.write(chunk)

                try:
                    with rasterio.open(grib_path) as src:
                        n_bands = src.count
                        logger.info(
                            'GRIB2: %s has %d bands (forecast days)',
                            item['grib_var'], n_bands,
                        )

                        for band_idx in range(1, n_bands + 1):
                            tags = src.tags(band_idx)
                            valid_time = tags.get('GRIB_VALID_TIME')

                            if valid_time:
                                try:
                                    ts = int(valid_time.split()[0])
                                    dt = datetime.date.fromtimestamp(ts)
                                except (ValueError, OSError):
                                    dt = item['init_date'] + timedelta(days=band_idx - 1)
                            else:
                                dt = item['init_date'] + timedelta(days=band_idx - 1)

                            if dt < self.start_date or dt > self.end_date:
                                continue

                            data = src.read(band_idx).astype(np.float32)

                            if bbox:
                                src_bounds = src.bounds
                                src_height, src_width = data.shape

                                col_start = max(0, int(
                                    (west - src_bounds.left) /
                                    (src_bounds.right - src_bounds.left) * src_width
                                ))
                                col_end = min(src_width, int(
                                    (east - src_bounds.left) /
                                    (src_bounds.right - src_bounds.left) * src_width
                                ) + 1)
                                row_start = max(0, int(
                                    (src_bounds.top - north) /
                                    (src_bounds.top - src_bounds.bottom) * src_height
                                ))
                                row_end = min(src_height, int(
                                    (src_bounds.top - south) /
                                    (src_bounds.top - src_bounds.bottom) * src_height
                                ) + 1)

                                clipped = data[row_start:row_end, col_start:col_end]
                                if clipped.size == 0:
                                    continue

                                pixel_width = (src_bounds.right - src_bounds.left) / src_width
                                pixel_height = (src_bounds.top - src_bounds.bottom) / src_height
                                clip_west = src_bounds.left + col_start * pixel_width
                                clip_north = src_bounds.top - row_start * pixel_height

                                clip_transform = from_bounds(
                                    clip_west,
                                    clip_north - clipped.shape[0] * pixel_height,
                                    clip_west + clipped.shape[1] * pixel_width,
                                    clip_north,
                                    clipped.shape[1], clipped.shape[0],
                                )
                                geo_info = {
                                    'transform': clip_transform,
                                    'width': clipped.shape[1],
                                    'height': clipped.shape[0],
                                }
                                yield (dt, item['member'], item['grib_var'], clipped, geo_info)
                            else:
                                geo_info = {
                                    'transform': src.transform,
                                    'width': src.width,
                                    'height': src.height,
                                }
                                yield (dt, item['member'], item['grib_var'], data, geo_info)

                except Exception as e:
                    logger.error('GRIB2 read error %s: %s', grib_path, e)

    # ------------------------------------------------------------------
    # TRANSFORM phase
    # ------------------------------------------------------------------

    def _transform_record(self, source_var, data, unit_conversions, variable_map):
        """
        Apply unit_conversions to determine target variable and transform data.

        Lookup order for target variable name:
        1. unit_conversions[source_var]['target_name']
        2. variable_map[source_var]
        3. source_var (as-is)

        Returns (target_var, transformed_data) or (None, None) if all-NaN.
        """
        conv = unit_conversions.get(source_var)
        if conv:
            target_var = conv.get('target_name', variable_map.get(source_var, source_var))
            op = conv.get('operation', 'rename')
            val = conv.get('value', 1)
            if op == 'multiply':
                data = data * val
            elif op == 'divide':
                data = data / val
            elif op == 'add':
                data = data + val
            elif op == 'subtract':
                data = data - val
            # 'rename' — no numerical transformation
        else:
            target_var = variable_map.get(source_var, source_var)

        if np.all(np.isnan(data)):
            return None, None
        return target_var, data

    # ------------------------------------------------------------------
    # LOAD phase
    # ------------------------------------------------------------------

    def _load_record(self, date, ens, target_var, data, geo_info,
                     con, schema, table_prefix, tmp_dir):
        """Write GeoTIFF → load to PostGIS."""
        table = f'{table_prefix}_{target_var}'

        # Ensure table exists
        if ens is not None:
            self._ensure_forecast_table_exists(con, schema, table)
        else:
            self._ensure_table_exists(con, schema, table)

        # Determine transform
        if 'transform' in geo_info:
            transform = geo_info['transform']
            height = geo_info['height']
            width = geo_info['width']
            west, north = transform * (0, 0)
            east, south = transform * (width, height)
            new_bbox = {'west': west, 'south': south, 'east': east, 'north': north}
        else:
            lats = geo_info['lats']
            lons = geo_info['lons']
            res = geo_info['resolution']
            height = len(lats)
            width = len(lons)
            transform = from_bounds(
                float(min(lons)) - res / 2, float(min(lats)) - res / 2,
                float(max(lons)) + res / 2, float(max(lats)) + res / 2,
                width, height,
            )
            new_bbox = {
                'west': float(min(lons)) - res / 2,
                'south': float(min(lats)) - res / 2,
                'east': float(max(lons)) + res / 2,
                'north': float(max(lats)) + res / 2,
            }

        # Dedup check — skip only if an existing raster already fully
        # contains the new raster's footprint. This preserves idempotency
        # for re-runs while still allowing coverage to grow over time.
        if self._is_loaded(con, schema, table, date, ens=ens, bbox=new_bbox):
            return

        ens_tag = f'_e{ens}' if ens else ''
        tiff_path = os.path.join(tmp_dir, f'{target_var}_{date}{ens_tag}.tif')
        with rasterio.open(
            tiff_path, 'w', driver='GTiff',
            height=height, width=width,
            count=1, dtype='float32', crs='EPSG:4326',
            transform=transform,
        ) as dst:
            dst.write(data, 1)

        self._load_tiff_to_postgis(tiff_path, date, con, schema, table, ens=ens)
        logger.info('Loaded %s %s ens=%s → %s.%s', target_var, date, ens, schema, table)
