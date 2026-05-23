import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("config")
# Read config from Django settings, keys prefixed with CELERY_.
app.config_from_object("django.conf:settings", namespace="CELERY")
# Discover tasks.py in every installed app.
app.autodiscover_tasks()
