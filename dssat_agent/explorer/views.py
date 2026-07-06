"""
Explorer views for browsing soils, cultivars, weather, and codes.
All data is fetched via direct service imports — no A2A HTTP calls.
"""

import json
import logging

from django.shortcuts import render
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.views import View

from dssat_agent.services import crop_service, soil_service
from dssat_agent.services.codes_service import get_codes_with_descriptions
from dssat_agent.models import StoredSoilProfile, StoredCropFile
from data_agent import services as data_services

logger = logging.getLogger(__name__)


# =============================================================================
# Soil Profiles (Full CRUD via direct service calls)
# =============================================================================

class SoilListView(View):
    """Paginated soil profile list — data loaded dynamically via JS."""

    def get(self, request):
        return render(request, 'dssat_agent/explorer/soils.html')


class SoilDetailView(View):
    """Single soil profile with layers."""

    def get(self, request, soil_id):
        try:
            profile = soil_service.get_soil_profile(soil_id)
        except Exception as e:
            profile = {'error': str(e)}

        return render(request, 'dssat_agent/explorer/soil_detail.html', {
            'profile': profile,
            'soil_id': soil_id,
        })


class SoilCreateView(View):
    """Create a new soil profile (manual)."""

    def get(self, request):
        return render(request, 'dssat_agent/explorer/soil_create.html')

    def post(self, request):
        try:
            data = {
                'soil_id': request.POST.get('soil_id', ''),
                'name': request.POST.get('name', ''),
                'salb': float(request.POST.get('salb', 0.13)),
                'slu1': float(request.POST.get('slu1', 6.0)),
                'sldr': float(request.POST.get('sldr', 0.6)),
                'slro': float(request.POST.get('slro', 73.0)),
                'slnf': float(request.POST.get('slnf', 1.0)),
                'slpf': float(request.POST.get('slpf', 1.0)),
            }
            for field in ['country', 'source', 'texture', 'classification']:
                val = request.POST.get(field, '')
                if val:
                    data[field] = val

            result = soil_service.create_soil_profile(data)
            return JsonResponse({'success': True, 'result': result})
        except (ValueError, Exception) as e:
            return JsonResponse({'error': str(e)}, status=400)


class SoilFromTextureView(View):
    """Create soil from texture with auto-estimated hydraulic properties."""

    def get(self, request):
        return render(request, 'dssat_agent/explorer/soil_create.html', {'from_texture': True})

    def post(self, request):
        try:
            data = json.loads(request.body) if request.content_type == 'application/json' else None
            if not data:
                data = {
                    'soil_id': request.POST.get('soil_id', ''),
                    'name': request.POST.get('name', ''),
                    'layers': json.loads(request.POST.get('layers', '[]')),
                }

            result = soil_service.create_soil_from_texture(data)
            return JsonResponse({'success': True, 'result': result})
        except (ValueError, json.JSONDecodeError, Exception) as e:
            return JsonResponse({'error': str(e)}, status=400)


@method_decorator(csrf_exempt, name='dispatch')
class SoilAddLayerView(View):
    """Add a layer to a soil profile."""

    def post(self, request, soil_id):
        try:
            data = json.loads(request.body)
            result = soil_service.add_soil_layer(soil_id, data)
            return JsonResponse({'success': True, 'result': result})
        except (json.JSONDecodeError, Exception) as e:
            return JsonResponse({'error': str(e)}, status=400)


@method_decorator(csrf_exempt, name='dispatch')
class SoilEditLayerView(View):
    """Edit a soil layer."""

    def post(self, request, soil_id, layer_idx):
        try:
            data = json.loads(request.body)
            result = soil_service.update_soil_layer(soil_id, layer_idx, data)
            return JsonResponse({'success': True, 'result': result})
        except (json.JSONDecodeError, Exception) as e:
            return JsonResponse({'error': str(e)}, status=400)


@method_decorator(csrf_exempt, name='dispatch')
class SoilDeleteLayerView(View):
    """Delete a soil layer."""

    def post(self, request, soil_id, layer_idx):
        try:
            result = soil_service.remove_soil_layer(soil_id, layer_idx)
            return JsonResponse({'success': True, 'result': result})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)


@method_decorator(csrf_exempt, name='dispatch')
class SoilEstimateAPI(View):
    """Estimate soil properties from texture."""

    def post(self, request):
        try:
            data = json.loads(request.body)
            result = soil_service.estimate_soil_properties(
                clay_pct=data.get('clay_pct', 0),
                silt_pct=data.get('silt_pct', 0),
                bulk_density=data.get('bulk_density'),
                organic_carbon=data.get('organic_carbon'),
            )
            return JsonResponse({'success': True, 'result': result})
        except (json.JSONDecodeError, Exception) as e:
            return JsonResponse({'error': str(e)}, status=400)


# =============================================================================
# File Downloads (Soils + Cultivars)
# =============================================================================

class SoilDownloadSOLView(View):
    """Download pre-generated .SOL file for a soil profile."""

    def get(self, request, soil_id):
        try:
            profile = StoredSoilProfile.objects.get(soil_id=soil_id)
        except StoredSoilProfile.DoesNotExist:
            return HttpResponse(f"Soil profile '{soil_id}' not found", status=404)

        if not profile.sol_file:
            return HttpResponse(f"No .SOL file generated for '{soil_id}'", status=404)

        response = HttpResponse(profile.sol_file, content_type='text/plain')
        response['Content-Disposition'] = f'attachment; filename="{soil_id}.SOL"'
        return response


class CropDownloadCULView(View):
    """Download pre-generated .CUL file for a crop type."""

    def get(self, request, crop_code):
        crop_code = crop_code.upper()
        try:
            cf = StoredCropFile.objects.get(crop_code=crop_code)
        except StoredCropFile.DoesNotExist:
            return HttpResponse(f"No stored .CUL file for crop '{crop_code}'", status=404)

        response = HttpResponse(cf.cul_file, content_type='text/plain')
        response['Content-Disposition'] = f'attachment; filename="{crop_code}.CUL"'
        return response


class CropDownloadECOView(View):
    """Download pre-generated .ECO file for a crop type."""

    def get(self, request, crop_code):
        crop_code = crop_code.upper()
        try:
            cf = StoredCropFile.objects.get(crop_code=crop_code)
        except StoredCropFile.DoesNotExist:
            return HttpResponse(f"No stored .ECO file for crop '{crop_code}'", status=404)

        if not cf.eco_file:
            return HttpResponse(f"Crop '{crop_code}' does not have an .ECO file", status=404)

        response = HttpResponse(cf.eco_file, content_type='text/plain')
        response['Content-Disposition'] = f'attachment; filename="{crop_code}.ECO"'
        return response


# =============================================================================
# Cultivars (Read-only browser)
# =============================================================================

class CropListView(View):
    """Crop Browser — lists all supported crops (one card per model for
    multi-model crops), grouped by ``crop_group`` for easier scanning."""

    # Presentation order for known groups; unknown groups sort to the end.
    _GROUP_ORDER = [
        'cereal', 'legume', 'tuber', 'vegetable', 'fruit',
        'oilseed', 'sugar', 'fiber', 'forage',
    ]

    def get(self, request):
        try:
            crops = crop_service.list_crop_entries()
        except Exception:
            crops = []

        # Group crops by crop_group for the template's sectioned grid.
        buckets = {}
        for c in crops:
            key = c.get('crop_group') or 'other'
            buckets.setdefault(key, []).append(c)

        def _group_rank(g):
            try:
                return self._GROUP_ORDER.index(g)
            except ValueError:
                return len(self._GROUP_ORDER) + (1 if g == 'other' else 0)

        grouped_crops = [
            {'group': g, 'crops': sorted(buckets[g], key=lambda x: x['name'])}
            for g in sorted(buckets.keys(), key=_group_rank)
        ]
        crop_group_labels = [g['group'] for g in grouped_crops]

        return render(request, 'dssat_agent/explorer/cultivars.html', {
            'crops': crops,
            'grouped_crops': grouped_crops,
            'crop_group_labels': crop_group_labels,
        })


class CropDetailView(View):
    """List cultivars for a specific crop — data loaded dynamically via JS."""

    def get(self, request, crop_code):
        dssat_model = request.GET.get('dssat_model', '')
        return render(request, 'dssat_agent/explorer/cultivars_crop.html', {
            'crop_code': crop_code,
            'dssat_model': dssat_model,
        })


class CropCultivarDetailView(View):
    """Full cultivar parameters."""

    def get(self, request, crop_code, cultivar_code):
        dssat_model = request.GET.get('dssat_model', None)
        try:
            result = crop_service.get_cultivar_details(
                crop_code, cultivar_code, dssat_model=dssat_model)
        except Exception as e:
            logger.error("Failed to load cultivar %s/%s: %s", crop_code, cultivar_code, e)
            result = {'error': str(e)}

        return render(request, 'dssat_agent/explorer/cultivar_detail.html', {
            'cultivar': result,
            'crop_code': crop_code,
            'cultivar_code': cultivar_code,
            'dssat_model': dssat_model or '',
        })


class CropCultivarCreateView(View):
    """Dynamic form for creating a new custom cultivar."""

    def get(self, request, crop_code):
        dssat_model = request.GET.get('dssat_model', '')
        clone_source_id = request.GET.get('clone', '')
        return render(request, 'dssat_agent/explorer/cultivar_form.html', {
            'crop_code': crop_code,
            'dssat_model': dssat_model,
            'edit_mode': False,
            'cultivar_id': '',
            'cultivar_code': '',
            'clone_source_id': clone_source_id,
        })


class CropCultivarEditView(View):
    """Dynamic form for editing a custom/cloned cultivar."""

    def get(self, request, crop_code, cultivar_id):
        dssat_model = request.GET.get('dssat_model', '')
        return render(request, 'dssat_agent/explorer/cultivar_form.html', {
            'crop_code': crop_code,
            'dssat_model': dssat_model,
            'edit_mode': True,
            'cultivar_id': cultivar_id,
            'cultivar_code': '',
            'clone_source_id': '',
        })


class CropEcotypeCreateView(View):
    """Dynamic form for creating a new custom ecotype."""

    def get(self, request, crop_code):
        dssat_model = request.GET.get('dssat_model', '')
        return render(request, 'dssat_agent/explorer/ecotype_form.html', {
            'crop_code': crop_code,
            'dssat_model': dssat_model,
            'edit_mode': False,
            'ecotype_id': '',
            'ecotype_code': '',
        })


class CropEcotypeEditView(View):
    """Dynamic form for editing a custom/cloned ecotype."""

    def get(self, request, crop_code, ecotype_id):
        dssat_model = request.GET.get('dssat_model', '')
        return render(request, 'dssat_agent/explorer/ecotype_form.html', {
            'crop_code': crop_code,
            'dssat_model': dssat_model,
            'edit_mode': True,
            'ecotype_id': ecotype_id,
            'ecotype_code': '',
        })


class CropEcotypeDetailView(View):
    """Full ecotype parameters."""

    def get(self, request, crop_code, ecotype_code):
        dssat_model = request.GET.get('dssat_model', None)
        from dssat_agent.models import DSSATEcotype
        qs = DSSATEcotype.objects.filter(
            ecotype_code=ecotype_code, crop_code=crop_code.upper())
        if dssat_model:
            qs = qs.filter(dssat_model=dssat_model.upper())
        eco = qs.first()
        if eco:
            result = {
                'id': str(eco.id),
                'ecotype_code': eco.ecotype_code,
                'ecotype_name': eco.ecotype_name,
                'crop_code': eco.crop_code,
                'dssat_model': eco.dssat_model,
                'source': eco.source,
                'params': eco.params,
            }
        else:
            result = {'error': f'Ecotype {ecotype_code} not found'}

        return render(request, 'dssat_agent/explorer/ecotype_detail.html', {
            'ecotype': result,
            'crop_code': crop_code,
            'ecotype_code': ecotype_code,
            'dssat_model': dssat_model or '',
        })


# =============================================================================
# DSSAT Codes Reference
# =============================================================================

class CodesListView(View):
    """List all code categories."""

    def get(self, request):
        categories = [
            {'key': 'planting', 'label': 'Planting Methods'},
            {'key': 'fertilizer', 'label': 'Fertilizer Materials'},
            {'key': 'application', 'label': 'Application Methods'},
            {'key': 'irrigation', 'label': 'Irrigation Methods'},
            {'key': 'tillage', 'label': 'Tillage Implements'},
            {'key': 'chemical', 'label': 'Chemicals'},
            {'key': 'residue', 'label': 'Residues & Organic Fertilizer'},
            {'key': 'crop', 'label': 'Crop Codes'},
            {'key': 'soil', 'label': 'Soil Textures'},
            {'key': 'drainage', 'label': 'Drainage Types'},
        ]
        return render(request, 'dssat_agent/explorer/codes.html', {'categories': categories})


# Category name -> DSSAT variable name mapping for codes lookup
_CODE_CATEGORIES = {
    'fertilizer_materials': 'fmcd',
    'application_methods': 'facd',
    'planting_methods': 'plme',
    'planting_distributions': 'plds',
    'drainage_types': 'fldt',
    'soil_textures': 'sltx',
    'soil_classifications': 'soil_clasification',
    'soil_colors': 'scom',
    'evaporation_methods': 'evapo',
    'som_methods': 'mesom',
    'harvest_components': 'hcom',
    'harvest_sizes': 'hsize',
    'crop_codes': 'cr',
    'water_switch': 'water',
    'nitrogen_switch': 'nitro',
    'plant_management': 'plant',
    'irrigation_management': 'irrig',
    'harvest_management': 'harvs',
    'tillage_implements': 'timpl',
    'chemical_codes': 'chcod',
    'residue_codes': 'rcod',
    'irrigation_methods': 'irop',
}

_CATEGORY_ALIASES = {
    'planting': 'planting_methods',
    'fertilizer': 'fertilizer_materials',
    'irrigation': 'irrigation_methods',
    'tillage': 'tillage_implements',
    'chemical': 'chemical_codes',
    'residue': 'residue_codes',
    'crop': 'crop_codes',
    'application': 'application_methods',
    'soil': 'soil_textures',
    'drainage': 'drainage_types',
    'harvest': 'harvest_components',
}


class CodesDetailView(View):
    """List codes for a category."""

    def get(self, request, category):
        # Resolve alias to canonical name
        resolved = _CATEGORY_ALIASES.get(category, category)
        code_var_name = _CODE_CATEGORIES.get(resolved)

        if code_var_name is None:
            result = {"error": f"Unknown category: {category}"}
        else:
            codes = get_codes_with_descriptions(code_var_name)
            result = {
                "category": resolved,
                "dssat_variable": code_var_name,
                "codes": codes,
            }

        return render(request, 'dssat_agent/explorer/codes.html', {
            'category': category,
            'codes': result,
        })
