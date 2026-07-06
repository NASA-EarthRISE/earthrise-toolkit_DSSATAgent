"""
Experiment detail views.

Queries dssat_agent models directly — no client abstraction.
"""

import io
import json
import logging
import zipfile

from django.http import JsonResponse, HttpResponse, HttpResponseForbidden
from django.shortcuts import render
from django.views import View

from earthrise_agents_base.mixins import is_admin, is_agent_admin
from dssat_agent.models import BatchExperiment, ExperimentSession, SimulationResult
from dssat_agent.services.crop_service import CROP_NAMES

logger = logging.getLogger(__name__)


class ExperimentListView(View):
    """List experiments, optionally filtered by chat_id."""

    def get(self, request, chat_id=None):
        # Per-user filtering: admins/dssat_admins see all, others see own
        if is_admin(request.user) or is_agent_admin(request.user, 'dssat_admin'):
            qs = ExperimentSession.objects.order_by('-created_at')
        else:
            qs = ExperimentSession.objects.filter(user=request.user).order_by('-created_at')
        if chat_id:
            qs = qs.filter(chat_id=chat_id)

        # Pre-fetch chat titles for experiments with chat_id
        from earthrise_agents_base.models import Chat
        chat_ids = set()
        for session in qs[:100]:
            if session.chat_id:
                chat_ids.add(session.chat_id)
        chat_titles = {}
        if chat_ids:
            for c in Chat.objects.filter(id__in=chat_ids).values_list('id', 'title'):
                chat_titles[c[0]] = c[1]

        show_owner = is_admin(request.user) or is_agent_admin(request.user, 'dssat_admin')
        experiments = []
        for session in qs[:100]:
            chat_title = chat_titles.get(session.chat_id, '') if session.chat_id else ''
            entry = {
                'experiment_id': str(session.pk),
                'experiment_type': session.experiment_type,
                'label': session.label or f"{session.crop_code} {session.experiment_type}",
                'crop_code': session.crop_code,
                'status': session.status,
                'chat_id': str(session.chat_id) if session.chat_id else None,
                'chat_title': chat_title,
                'chat_url': f"/chats/{session.chat_id}/" if session.chat_id else None,
                'created_at': session.created_at,
            }
            if show_owner and session.user:
                entry['owner'] = session.user.get_full_name() or session.user.username
            experiments.append(entry)

        return render(request, 'dssat_agent/explorer/experiment_list.html', {
            'experiments': experiments,
            'chat_id': str(chat_id) if chat_id else None,
        })


class ExperimentDetailView(View):
    """Full experiment detail from dssat_agent models."""

    def get(self, request, experiment_id):
        experiment = None
        try:
            session = ExperimentSession.objects.get(pk=experiment_id)
            # Ownership check
            if (session.user and session.user != request.user
                    and not is_admin(request.user)
                    and not is_agent_admin(request.user, 'dssat_admin')):
                return HttpResponseForbidden("You do not have access to this experiment.")
            # Build human-readable title
            crop_name = CROP_NAMES.get(session.crop_code, session.crop_code)
            exp_type_label = session.get_experiment_type_display()
            if session.label:
                title = session.label
            elif crop_name:
                title = f"{crop_name} {exp_type_label} Simulation"
            else:
                title = f"{exp_type_label} Simulation"

            experiment = {
                'id': str(session.pk),
                'title': title,
                'status': session.status,
                'experiment_type': session.experiment_type,
                'crop_code': session.crop_code,
                'cultivar_code': session.cultivar_code,
                'soil_profile': session.soil_profile.soil_id if session.soil_profile else None,
                'raw_params': session.raw_params,
                'aggregate_summary': session.aggregate_summary,
                'chat_id': str(session.chat_id) if session.chat_id else None,
                'chat_url': f"/chats/{session.chat_id}/" if session.chat_id else None,
                'label': session.label,
                'created_at': session.created_at.isoformat(),
                'updated_at': session.updated_at.isoformat(),
                'results': [],
            }
            for r in session.results.order_by('completed_at'):
                result_data = {
                    'id': str(r.pk),
                    'summary': r.summary,
                    'has_plant_growth': r.plant_growth is not None,
                    'has_dssat_files': r.dssat_files is not None and bool(r.dssat_files),
                    'dssat_files_listing': None,
                    'overview': r.overview[:500] if r.overview else '',
                    'completed_at': r.completed_at.isoformat() if r.completed_at else None,
                }
                if r.dssat_files:
                    listing = {'input': {}, 'output': {}}
                    for ftype in ('input', 'output'):
                        for key, content in r.dssat_files.get(ftype, {}).items():
                            listing[ftype][key] = len(content) if isinstance(content, str) else 0
                    result_data['dssat_files_listing'] = listing
                experiment['results'].append(result_data)
        except ExperimentSession.DoesNotExist:
            logger.error("Experiment %s not found", experiment_id)
        except Exception as e:
            logger.error("Failed to fetch experiment %s: %s", experiment_id, e)

        return render(request, 'dssat_agent/explorer/experiment_detail.html', {
            'experiment': experiment or {'error': 'Experiment not found'},
            'experiment_id': str(experiment_id),
        })


class ExperimentFileAPI(View):
    """Serve a single DSSAT file from experiment results."""

    def get(self, request, experiment_id, file_type, file_key, result_id=None):
        try:
            if result_id:
                result = SimulationResult.objects.get(pk=result_id)
            else:
                result = SimulationResult.objects.filter(
                    experiment_id=experiment_id
                ).order_by('-completed_at').first()

            if not result or not result.dssat_files:
                return JsonResponse({'error': 'No files found'}, status=404)

            content = result.dssat_files.get(file_type, {}).get(file_key)
            if content is None:
                return JsonResponse({'error': f'File {file_type}/{file_key} not found'}, status=404)

            if request.GET.get('download'):
                label = result.experiment.label
                filename = f"{label}_{file_key}" if label else file_key
                response = HttpResponse(content, content_type='text/plain')
                response['Content-Disposition'] = f'attachment; filename="{filename}"'
                return response

            return JsonResponse({'content': content, 'file_key': file_key})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=404)


class ExperimentFilesZipAPI(View):
    """Bundle all DSSAT files for an experiment as a ZIP download."""

    def get(self, request, experiment_id, result_id=None):
        try:
            if result_id:
                result = SimulationResult.objects.get(pk=result_id)
            else:
                result = SimulationResult.objects.filter(
                    experiment_id=experiment_id
                ).order_by('-completed_at').first()

            if not result or not result.dssat_files:
                return HttpResponse('No files found', status=404)

            label = result.experiment.label
            zip_name = label or f"experiment_{str(experiment_id)[:8]}"

            buf = io.BytesIO()
            with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
                for file_type in ('input', 'output'):
                    for file_key, content in result.dssat_files.get(file_type, {}).items():
                        if content:
                            zf.writestr(f"{file_type}/{file_key}", content)

            buf.seek(0)
            response = HttpResponse(buf.read(), content_type='application/zip')
            response['Content-Disposition'] = (
                f'attachment; filename="{zip_name}.zip"'
            )
            return response
        except Exception as e:
            return HttpResponse(str(e), status=404)


class BatchDetailView(View):
    """Batch experiment detail — shows per-location results and aggregate stats."""

    def get(self, request, batch_id):
        try:
            batch = BatchExperiment.objects.get(pk=batch_id)
        except BatchExperiment.DoesNotExist:
            return render(request, 'dssat_agent/explorer/batch_detail.html', {
                'error': f'Batch {batch_id} not found',
            })

        # Build per-location data
        children = []
        for child in batch.children.order_by('location_label'):
            entry = {
                'experiment_id': str(child.pk),
                'location_label': child.location_label,
                'status': child.status,
                'yield_kg_ha': None,
                'maturity_days': None,
            }
            result = child.results.order_by('-completed_at').first()
            if result and result.summary:
                s = result.summary
                y = s.get('yield_kg_ha') or s.get('hwam')
                m = s.get('maturity_days') or s.get('mat')
                if y is not None:
                    try:
                        entry['yield_kg_ha'] = round(float(y), 1)
                    except (TypeError, ValueError):
                        pass
                if m is not None:
                    try:
                        entry['maturity_days'] = round(float(m), 1)
                    except (TypeError, ValueError):
                        pass
            children.append(entry)

        crop_name = CROP_NAMES.get(
            batch.template_params.get('crop_code', ''),
            batch.template_params.get('crop_code', ''),
        )

        context = {
            'batch': {
                'id': str(batch.pk),
                'label': batch.label,
                'status': batch.status,
                'sub_experiment_type': batch.get_sub_experiment_type_display(),
                'location_mode': batch.get_location_selection_mode_display(),
                'total_locations': batch.total_locations,
                'completed': batch.completed_count,
                'failed': batch.failed_count,
                'crop_name': crop_name,
                'aggregate_summary': batch.aggregate_summary,
                'chat_url': f"/chats/{batch.chat_id}/" if batch.chat_id else None,
                'created_at': batch.created_at,
            },
            'children': children,
        }
        return render(request, 'dssat_agent/explorer/batch_detail.html', context)
