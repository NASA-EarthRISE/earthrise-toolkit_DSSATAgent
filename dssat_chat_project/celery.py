import os
from celery import Celery

config_env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.env")
if os.path.exists(config_env_path):
    try:
        from dotenv import load_dotenv
        load_dotenv(config_env_path)
    except ImportError:
        with open(config_env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ.setdefault(key.strip(), value.strip().strip('"\''))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dssat_chat_project.settings')

app = Celery('dssat_chat_project')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()
app.conf.beat_schedule = {}
app.conf.timezone = 'UTC'
