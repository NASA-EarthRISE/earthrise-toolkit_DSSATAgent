"""
Management command to run the ETL pipeline for weather data sources.
Adapted from Generalized-ETL/generalETL/etl/management/commands/start_etl_pipeline.py
"""

import sys
import os
import datetime
from django.core.management.base import BaseCommand
from data_agent.models import RasterDataset
from data_agent.etl.etl_pipeline import ETL_Pipeline


class Command(BaseCommand):
    help = 'Run the ETL pipeline for enabled datasets (or a specific dataset by UUID)'

    def add_arguments(self, parser):
        parser.add_argument('--etl_dataset_uuid', nargs='?', type=str, default=None)
        parser.add_argument('--start_date', nargs='?', type=str, default=None)
        parser.add_argument('--end_date', nargs='?', type=str, default=None)

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS(
            'Initializing ETL pipeline for all pipeline enabled datasets'
        ))

        ds_objects = RasterDataset.objects.all()
        uuid = options.get('etl_dataset_uuid')

        start_date = options.get('start_date')
        end_date = options.get('end_date')

        if start_date is not None:
            try:
                start_date = datetime.date.fromisoformat(start_date)
                self.stdout.write(self.style.SUCCESS(
                    f'Successfully set start_date [{start_date}]'
                ))
            except ValueError:
                self.stdout.write(self.style.ERROR(
                    f'start_date [{start_date}] is not a valid date. '
                    f'Format: YYYY-MM-DD'
                ))
                return

        if end_date is not None:
            try:
                end_date = datetime.date.fromisoformat(end_date)
                self.stdout.write(self.style.SUCCESS(
                    f'Successfully set end_date [{end_date}]'
                ))
            except ValueError:
                self.stdout.write(self.style.ERROR(
                    f'end_date [{end_date}] is not a valid date. '
                    f'Format: YYYY-MM-DD'
                ))
                return

        for dataset in ds_objects:
            try:
                if uuid and dataset.uuid == uuid:
                    self._run_pipeline(dataset, start_date, end_date)
                elif not uuid and dataset.is_pipeline_enabled:
                    self._run_pipeline(dataset, start_date, end_date)
                else:
                    self.stdout.write(self.style.WARNING(
                        f'Dataset [{dataset}] (uuid={dataset.uuid}) is not enabled. '
                        f'Set is_pipeline_enabled=True to process.'
                    ))
            except Exception as error:
                exc_type, exc_obj, exc_tb = sys.exc_info()
                fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
                self.stdout.write(self.style.ERROR(
                    f'Uncaught exception at line {exc_tb.tb_lineno} in {fname}: {error}'
                ))
            finally:
                self.stdout.write(self.style.SUCCESS(
                    f'ETL Pipeline step for [{dataset}] (uuid={dataset.uuid}) completed.'
                ))

        self.stdout.write(self.style.SUCCESS('ETL Pipeline completed for all datasets.'))

    def _run_pipeline(self, dataset, start_date, end_date):
        self.stdout.write(self.style.SUCCESS(
            f'Initializing ETL pipeline for [{dataset}] (uuid={dataset.uuid})'
        ))
        etl_pipeline = ETL_Pipeline(dataset, self.stdout, self.style, start_date, end_date)
        etl_pipeline.execute_etl_pipeline()
