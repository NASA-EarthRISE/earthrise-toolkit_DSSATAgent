"""
Celery tasks for async simulation execution with Redis progress publishing.
"""

import json
import logging

import redis
from celery import chord, group, shared_task
from django.conf import settings

logger = logging.getLogger(__name__)


def _get_redis():
    """Return a Redis client from the Celery broker URL."""
    return redis.Redis.from_url(settings.CELERY_BROKER_URL)


def _publish_progress(channel, stage, title, description=''):
    """Publish a progress event to a Redis pub/sub channel."""
    try:
        r = _get_redis()
        r.publish(channel, json.dumps({
            'type': 'progress',
            'stage': stage,
            'title': title,
            'description': description,
        }))
    except Exception as e:
        logger.warning("Failed to publish progress to %s: %s", channel, e)


@shared_task(bind=True, queue='dssat_agent', time_limit=900, soft_time_limit=840)
def run_wizard_experiment_task(self, chat_id, message_id, params, wizard_params, user_id=None):
    """
    Execute a wizard-submitted experiment directly, bypassing the ChatAgent
    LLM loop.

    The wizard view already has fully-structured params, so routing them
    through a ReAct tool-calling loop just invites the LLM to re-interpret
    (and drop) values the user already chose. This task calls
    `run_full_simulation` directly and hands the resulting envelope to the
    generic `write_envelope_to_message` helper, which any subagent doing the
    same direct-submission pattern can use.
    """
    from earthrise_agents_base.agent.direct_result import (
        write_envelope_to_message, synthesize_envelope_narrative,
    )
    from dssat_agent.services.workflow import run_full_simulation

    try:
        envelope = run_full_simulation(
            params=params,
            wizard_params=wizard_params,
            chat_id=chat_id,
            user_id=user_id,
        )
    except Exception as e:
        logger.exception("Wizard experiment task failed for chat %s", chat_id)
        envelope = {'status': 'error', 'error': str(e)}

    # On a successful run, replace any pre-built ``response_text`` with
    # an LLM-synthesized narrative — so the wizard chat looks like a
    # ChatAgent reply instead of a deterministic key-result dump. Best
    # effort: synth failure leaves the existing ``response_text`` (or
    # the generic fallback) in place.
    if (envelope.get('status') == 'completed'
            and envelope.get('key_results')):
        try:
            from earthrise_agents_base.models import Chat as _Chat
            user_query = ''
            try:
                _ch = _Chat.objects.get(pk=chat_id)
                first_user = _ch.messages.filter(
                    message_type='user').order_by('id').first()
                if first_user and first_user.content:
                    user_query = first_user.content[:2000]
            except Exception:
                pass
            narrative = synthesize_envelope_narrative(
                envelope,
                user_query=user_query,
                agent_label='dssat_agent',
            )
            if narrative:
                envelope['response_text'] = narrative
        except Exception:
            logger.warning(
                "Wizard narrative synthesis failed for chat %s",
                chat_id, exc_info=True,
            )

    # If this experiment originated from a WizardDraft, wire the FK + M2M
    # back-references so the draft, the run record, and the resulting
    # ExperimentSession all link up. Failure here is non-fatal — the
    # experiment ran, we just lose the audit trail.
    try:
        from earthrise_agents_base.models import Chat
        chat = Chat.objects.get(pk=chat_id)
        draft_id = (chat.agent_state or {}).get('draft_id')
        experiment_id = envelope.get('experiment_id')
        if draft_id and experiment_id:
            from dssat_agent.services.draft_submit import link_experiment_to_draft
            link_experiment_to_draft(experiment_id, draft_id)
    except Exception:
        logger.warning(
            "Failed to link draft to experiment for chat %s", chat_id,
            exc_info=True)

    write_envelope_to_message(chat_id, message_id, envelope)
    return {'status': envelope.get('status'), 'chat_id': chat_id}


@shared_task(bind=True, queue='dssat_agent', time_limit=600, soft_time_limit=540)
def run_simulation_async(self, experiment_id, params):
    """
    Run a DSSAT simulation asynchronously.

    Publishes progress events to Redis channel ``experiment:<experiment_id>``
    at each major build stage so SSE consumers can relay them to the client.

    Parameters
    ----------
    experiment_id : str
        UUID of the ExperimentSession.
    params : dict
        Full experiment parameters.
    """
    from dssat_agent.models import ExperimentSession, SimulationResult
    from dssat_agent.services.experiment_service import run_simulation

    channel = f"experiment:{experiment_id}"

    try:
        session = ExperimentSession.objects.get(pk=experiment_id)
        session.status = 'running'
        session.raw_params = params
        session.save(update_fields=['status', 'raw_params'])
    except ExperimentSession.DoesNotExist:
        logger.error("ExperimentSession %s not found", experiment_id)
        return {"error": f"Session {experiment_id} not found"}

    def progress_callback(stage, title, description=''):
        _publish_progress(channel, stage, title, description)

    try:
        result = run_simulation(params, progress_callback=progress_callback)

        if result.get('status') == 'completed':
            SimulationResult.objects.create(
                experiment=session,
                summary=result.get('summary', {}),
                plant_growth=result.get('plant_growth'),
                soil_water=result.get('soil_water'),
                soil_organic=result.get('soil_organic'),
                soil_nitrogen=result.get('soil_nitrogen'),
                weather_output=result.get('weather_output'),
                overview=result.get('overview', ''),
                stdout=result.get('stdout', ''),
                dssat_files=result.get('dssat_files'),
            )
            session.status = 'completed'
        else:
            session.status = 'failed'
            session.validation_errors = result.get('errors', [])

        session.save(update_fields=['status', 'validation_errors'])

        # Inject experiment_id into result
        result['experiment_id'] = experiment_id

        # Publish completion event (strip large file content from SSE)
        sse_result = {k: v for k, v in result.items() if k != 'dssat_files'}
        if result.get('dssat_files'):
            sse_result['has_dssat_files'] = True
        sse_result['experiment_id'] = experiment_id
        try:
            r = _get_redis()
            r.publish(channel, json.dumps({
                'type': 'completed',
                'result': sse_result,
            }))
        except Exception:
            pass

        return result

    except Exception as e:
        logger.exception("Async simulation failed for %s", experiment_id)
        session.status = 'failed'
        session.validation_errors = [str(e)]
        session.save(update_fields=['status', 'validation_errors'])

        # Publish error event
        try:
            r = _get_redis()
            r.publish(channel, json.dumps({
                'type': 'error',
                'message': str(e),
            }))
        except Exception:
            pass

        return {"status": "failed", "errors": [str(e)]}


@shared_task(bind=True, queue='dssat_agent', time_limit=7200, soft_time_limit=7100)
def run_monte_carlo_async(self, experiment_id, params):
    """
    Run a Monte Carlo simulation asynchronously.

    Same pattern as run_simulation_async but calls monte_carlo_service.
    Higher time limit (15 min) due to multiple weather queries + batch run.
    """
    from dssat_agent.models import ExperimentSession, SimulationResult
    from dssat_agent.services.monte_carlo_service import run_monte_carlo

    channel = f"experiment:{experiment_id}"

    try:
        session = ExperimentSession.objects.get(pk=experiment_id)
        session.status = 'running'
        session.raw_params = params
        session.save(update_fields=['status', 'raw_params'])
    except ExperimentSession.DoesNotExist:
        logger.error("ExperimentSession %s not found", experiment_id)
        return {"error": f"Session {experiment_id} not found"}

    def progress_callback(stage, title, description=''):
        _publish_progress(channel, stage, title, description)

    try:
        result = run_monte_carlo(params, progress_callback=progress_callback)

        if result.get('status') == 'completed':
            # Per-treatment results so the captured DSSAT files (shared
            # multi-treatment FileX bundle) are available for inspection.
            treatments = result.get('treatments', [])
            for t in treatments:
                SimulationResult.objects.create(
                    experiment=session,
                    summary=t.get('summary', {}),
                    dssat_files=t.get('dssat_files'),
                )
            # Aggregate summary lives on the session itself.
            session.aggregate_summary = result.get('quantiles')
            session.status = 'completed'
        else:
            session.status = 'failed'
            session.validation_errors = result.get('errors', [])

        session.save(update_fields=['status', 'validation_errors', 'aggregate_summary'])

        # Inject experiment_id into result
        result['experiment_id'] = experiment_id

        # Publish completion — strip large treatment details from SSE
        sse_result = {k: v for k, v in result.items() if k != 'treatments'}
        sse_result['treatments_count'] = len(result.get('treatments', []))
        sse_result['experiment_id'] = experiment_id
        try:
            r = _get_redis()
            r.publish(channel, json.dumps({
                'type': 'completed',
                'result': sse_result,
            }))
        except Exception:
            pass

        return result

    except Exception as e:
        logger.exception("Async MC simulation failed for %s", experiment_id)
        session.status = 'failed'
        session.validation_errors = [str(e)]
        session.save(update_fields=['status', 'validation_errors'])

        try:
            r = _get_redis()
            r.publish(channel, json.dumps({
                'type': 'error',
                'message': str(e),
            }))
        except Exception:
            pass

        return {"status": "failed", "errors": [str(e)]}


# ---------------------------------------------------------------------------
# Batch (multi-location) tasks
# ---------------------------------------------------------------------------

@shared_task(bind=True, queue='dssat_agent', time_limit=600, soft_time_limit=540)
def run_batch_child(self, child_experiment_id, per_location_params, batch_id):
    """
    Run a single child experiment within a batch.

    Calls run_full_simulation for one location and publishes progress
    to the batch channel.
    """
    from dssat_agent.models import BatchExperiment, ExperimentSession
    from dssat_agent.services.workflow import run_full_simulation

    batch_channel = f"batch:{batch_id}"
    location_name = per_location_params.get('location_name', 'Unknown')

    try:
        result = run_full_simulation(
            params=per_location_params,
            chat_id=per_location_params.get('chat_id'),
        )

        child_status = 'completed' if result.get('status') == 'completed' else 'failed'

        # Update batch counters atomically
        from django.db.models import F
        if child_status == 'completed':
            BatchExperiment.objects.filter(pk=batch_id).update(
                completed_count=F('completed_count') + 1,
            )
        else:
            BatchExperiment.objects.filter(pk=batch_id).update(
                failed_count=F('failed_count') + 1,
            )

        # Publish child completion to batch channel
        try:
            r = _get_redis()
            batch = BatchExperiment.objects.get(pk=batch_id)
            r.publish(batch_channel, json.dumps({
                'type': 'batch_progress',
                'child_experiment_id': child_experiment_id,
                'location': location_name,
                'child_status': child_status,
                'completed': batch.completed_count,
                'failed': batch.failed_count,
                'total': batch.total_locations,
            }))
        except Exception:
            pass

        return {
            'child_id': child_experiment_id,
            'status': child_status,
            'location': location_name,
        }

    except Exception as e:
        logger.exception("Batch child %s failed: %s", child_experiment_id, e)

        # Mark child as failed
        try:
            child = ExperimentSession.objects.get(pk=child_experiment_id)
            child.status = 'failed'
            child.validation_errors = [str(e)]
            child.save(update_fields=['status', 'validation_errors'])
        except Exception:
            pass

        # Update batch failed count
        from django.db.models import F
        BatchExperiment.objects.filter(pk=batch_id).update(
            failed_count=F('failed_count') + 1,
        )

        return {
            'child_id': child_experiment_id,
            'status': 'failed',
            'location': location_name,
            'error': str(e),
        }


@shared_task(bind=True, queue='dssat_agent')
def finalize_batch(self, child_results, batch_id):
    """
    Chord callback: aggregate results after all children complete.

    Parameters
    ----------
    child_results : list
        Results from each run_batch_child task.
    batch_id : str
        UUID of the BatchExperiment.
    """
    from dssat_agent.models import BatchExperiment
    from dssat_agent.services.batch_service import aggregate_batch_results

    batch_channel = f"batch:{batch_id}"

    try:
        batch = BatchExperiment.objects.get(pk=batch_id)

        # Aggregate cross-location results
        summary = aggregate_batch_results(batch_id)
        batch.aggregate_summary = summary

        # Determine final status
        if batch.failed_count == 0:
            batch.status = 'completed'
        elif batch.completed_count == 0:
            batch.status = 'failed'
        else:
            batch.status = 'partially_completed'

        batch.save(update_fields=['status', 'aggregate_summary',
                                  'completed_count', 'failed_count'])

        # Publish batch completion
        try:
            r = _get_redis()
            r.publish(batch_channel, json.dumps({
                'type': 'completed',
                'batch_id': batch_id,
                'status': batch.status,
                'completed': batch.completed_count,
                'failed': batch.failed_count,
                'total': batch.total_locations,
                'aggregate_summary': summary,
            }))
        except Exception:
            pass

        logger.info("Batch %s finalized: %s (%d/%d completed)",
                     batch_id, batch.status, batch.completed_count,
                     batch.total_locations)

    except Exception as e:
        logger.exception("Batch finalization failed for %s", batch_id)
        try:
            BatchExperiment.objects.filter(pk=batch_id).update(status='failed')
        except Exception:
            pass


@shared_task(bind=True, queue='dssat_agent', time_limit=7200, soft_time_limit=7100)
def run_batch_async(self, batch_id, params):
    """
    Launch a batch multi-location experiment.

    Creates child experiments and fans them out as a Celery chord:
    group(run_batch_child for each location) | finalize_batch.
    """
    from dssat_agent.models import BatchExperiment
    from dssat_agent.services.batch_service import (
        resolve_batch_locations,
        create_batch_children,
        create_batch_children_from_pairs,
        MAX_BATCH_LOCATIONS,
    )

    batch_channel = f"batch:{batch_id}"

    try:
        batch = BatchExperiment.objects.get(pk=batch_id)
        batch.status = 'running'
        batch.save(update_fields=['status'])
    except BatchExperiment.DoesNotExist:
        logger.error("BatchExperiment %s not found", batch_id)
        return {"error": f"Batch {batch_id} not found"}

    try:
        sub_type = params.get('sub_experiment_type', 'single')
        pair_params = params.get('pair_params')

        if pair_params:
            # Wizard-draft per-pair path: each pre-built params dict becomes
            # one child as-is. No location resolution / template merge.
            if len(pair_params) > MAX_BATCH_LOCATIONS:
                batch.status = 'failed'
                batch.save(update_fields=['status'])
                return {
                    "status": "error",
                    "error": f"Too many pairs ({len(pair_params)}). "
                             f"Maximum is {MAX_BATCH_LOCATIONS}.",
                }
            batch.total_locations = len(pair_params)
            batch.save(update_fields=['total_locations'])
            children = create_batch_children_from_pairs(
                batch, pair_params, sub_experiment_type=sub_type)
            location_names = [
                (p.get('location_name') or p.get('location_label')
                 or f'Pair {i+1}')
                for i, p in enumerate(pair_params)
            ]
        else:
            # Legacy template + location_config path.
            location_config = params.get('location_config', {})
            locations = resolve_batch_locations(location_config)
            if len(locations) > MAX_BATCH_LOCATIONS:
                batch.status = 'failed'
                batch.save(update_fields=['status'])
                return {
                    "status": "error",
                    "error": f"Too many locations ({len(locations)}). "
                             f"Maximum is {MAX_BATCH_LOCATIONS}.",
                }
            batch.total_locations = len(locations)
            batch.save(update_fields=['total_locations'])
            template_params = params.get('template_params', {})
            children = create_batch_children(
                batch, locations, template_params, sub_type)
            location_names = [loc['name'] for loc in locations]

        # Publish start event
        try:
            r = _get_redis()
            r.publish(batch_channel, json.dumps({
                'type': 'batch_started',
                'batch_id': batch_id,
                'total': len(children),
                'locations': location_names,
            }))
        except Exception:
            pass

        # Fan out as chord: group of children -> finalize callback
        child_tasks = group(
            run_batch_child.s(child_id, child_params, batch_id)
            for child_id, child_params in children
        )
        callback = finalize_batch.s(batch_id)
        chord(child_tasks)(callback)

        return {
            "status": "running",
            "batch_id": batch_id,
            "total_locations": len(children),
        }

    except Exception as e:
        logger.exception("Batch setup failed for %s", batch_id)
        batch.status = 'failed'
        batch.save(update_fields=['status'])

        try:
            r = _get_redis()
            r.publish(batch_channel, json.dumps({
                'type': 'error',
                'message': str(e),
            }))
        except Exception:
            pass

        return {"status": "failed", "errors": [str(e)]}
