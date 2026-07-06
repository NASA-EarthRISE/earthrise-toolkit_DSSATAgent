#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys

config_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.env")
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


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dssat_chat_project.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
