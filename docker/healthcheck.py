import os
from pathlib import Path
import socket
import sys
import time
from urllib.request import Request, urlopen

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.conf import settings
from django.db import connection
from config.celery import app

with connection.cursor() as cursor:
    cursor.execute('SELECT 1')
app.connection_for_write().ensure_connection(max_retries=0)

role = sys.argv[1]
if role == 'backend':
    host = next((host for host in settings.ALLOWED_HOSTS if host != '*'), 'localhost').lstrip('.')
    request = Request('http://localhost:8000/health/', headers={'Host': host})
    with urlopen(request, timeout=5) as response:
        assert response.status == 200
elif role == 'worker':
    assert app.control.inspect(destination=[f'worker@{socket.gethostname()}'], timeout=5).ping()
elif role == 'beat':
    assert time.time() - Path('/app/run/beat-heartbeat').stat().st_mtime < 90
else:
    raise ValueError(role)
