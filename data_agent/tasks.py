"""
Celery tasks for the DataAgent data service.
"""

import logging
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def fetch_data_task(self, job_id):
    """
    Fetch data for a DataFetchJob.

    This task:
    1. Loads the RasterDataset configuration
    2. Sets the date range from the job
    3. Checks for existing dates in PostGIS (dedup)
    4. Runs the ETL pipeline (download, transform, load to PostGIS)
       - For combined sources (source_groups), the pipeline fetches each
         group independently and writes to the combined tables.
    5. Updates job status throughout
    """
    from data_agent.models import DataFetchJob
    from data_agent.etl.etl_pipeline import ETL_Pipeline

    try:
        job = DataFetchJob.objects.get(id=job_id)
    except DataFetchJob.DoesNotExist:
        logger.error(f'DataFetchJob {job_id} not found')
        return {'error': f'Job {job_id} not found'}

    dataset = job.raster_dataset
    logger.info(
        f'Starting fetch for {dataset.dataset_name}: '
        f'{job.start_date} to {job.end_date}'
    )

    try:
        # Update status
        job.status = 'downloading'
        job.save()

        # Create a simple stdout/style wrapper for the pipeline
        stdout = _TaskStdout(logger)
        style = _TaskStyle()

        # Build bbox dict from job fields
        bbox = {
            'west': job.bbox_west,
            'south': job.bbox_south,
            'east': job.bbox_east,
            'north': job.bbox_north,
        }

        # Determine if we need to write to a non-default schema
        target_schema = (
            job.schema_name if job.schema_name != 'dataagent' else None
        )

        # Instantiate pipeline
        pipeline = ETL_Pipeline(
            dataset, stdout, style,
            job.start_date, job.end_date,
            bbox=bbox,
            target_schema=target_schema,
        )

        # Check which dates already exist in PostGIS
        ds_info = dataset.dataset_information or {}
        subroutines = ds_info.get('subroutines', {})
        postgis_config = subroutines.get('load_to_postgis', {})
        schema = job.schema_name if target_schema else postgis_config.get('schema', job.schema_name)
        table_prefix = postgis_config.get('table_prefix', '')

        if table_prefix:
            try:
                con = pipeline._get_postgis_connection()
                # Check first available variable
                unit_conversions = subroutines.get('unit_conversions', {})
                first_var = None
                for k, v in unit_conversions.items():
                    first_var = v.get('target_name', k)
                    break

                if first_var:
                    existing_dates = pipeline.check_availability(
                        con, schema, table_prefix, first_var,
                        job.start_date, job.end_date,
                    )
                    logger.info(
                        f'Found {len(existing_dates)} existing dates for '
                        f'{table_prefix}_{first_var}'
                    )
                    job.dates_loaded = [str(d) for d in existing_dates]
                con.close()
            except Exception as e:
                logger.warning(f'Could not check existing dates: {e}')

        # Update status
        job.status = 'transforming'
        job.progress_pct = 25
        job.save()

        # Run the ETL pipeline — handles both single-source and source_groups
        pipeline.execute_etl_pipeline()

        # Mark as completed
        job.status = 'completed'
        job.progress_pct = 100
        job.save()

        logger.info(f'Fetch completed for job {job_id}')
        return {'status': 'completed', 'job_id': str(job_id)}

    except Exception as e:
        logger.exception(f'Fetch failed for job {job_id}')
        job.status = 'failed'
        job.error_message = str(e)
        job.save()

        # Retry with exponential backoff
        raise self.retry(exc=e, countdown=60 * (2 ** self.request.retries))


class _TaskStdout:
    """Simple stdout wrapper that redirects to logger."""

    def __init__(self, logger):
        self._logger = logger

    def write(self, message):
        if message.strip():
            self._logger.info(message.strip())


class _TaskStyle:
    """Minimal style wrapper for ETL pipeline compatibility."""

    def SUCCESS(self, message):
        return message

    def ERROR(self, message):
        return message

    def WARNING(self, message):
        return message
