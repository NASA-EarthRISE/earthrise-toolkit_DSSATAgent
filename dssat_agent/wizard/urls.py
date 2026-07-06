from django.urls import path
from . import views

# These are included under the dssat_agent namespace
urlpatterns = [
    path('', views.ExperimentWizardView.as_view(), name='experiment_wizard'),
]
