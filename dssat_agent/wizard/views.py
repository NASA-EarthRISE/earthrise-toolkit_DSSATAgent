"""
Views for the experiment wizard.
"""

from django.shortcuts import render
from django.views import View


class ExperimentWizardView(View):
    """Render the experiment wizard page."""

    def get(self, request):
        return render(request, 'dssat_agent/wizard/experiment_wizard.html')
