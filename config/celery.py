"""Celery application configuration for SQL Analytics.

Auto-discovers tasks from all installed Django apps.
Start the worker with:

    uv run celery -A config worker --loglevel=info
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("sqlanalytics")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
