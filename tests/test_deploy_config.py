import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DeployConfigTests(unittest.TestCase):
    def settings(self, **changes):
        env = {k: v for k, v in os.environ.items() if not k.startswith(('DJANGO_', 'POSTGRES_', 'CELERY_', 'WEBHOOK_', 'APP_ENV', 'REDIS_THROTTLE_'))}
        env.update(DJANGO_SECRET_KEY='a' * 60, WEBHOOK_SECRET='b' * 60,
                   DJANGO_DEBUG='False', POSTGRES_HOST='localhost',
                   POSTGRES_DB='chargeflow', POSTGRES_USER='chargeflow',
                   POSTGRES_PASSWORD='c' * 40, CELERY_BROKER_URL='redis://localhost:6379/0',
                   REDIS_THROTTLE_URL='redis://localhost:6379/1')
        env.update(changes)
        code = "import json; from config import settings as s; print(json.dumps([s.DATABASES['default']['ENGINE'], s.CELERY_TASK_ALWAYS_EAGER]))"
        return subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env, text=True, capture_output=True)

    def test_postgres_is_the_only_database(self):
        result = self.settings()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0], 'django.db.backends.postgresql')
        result = self.settings(POSTGRES_HOST='', DJANGO_DEBUG='True')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('POSTGRES_HOST', result.stderr)

    def test_secrets_are_required_and_not_placeholders(self):
        for name in ('DJANGO_SECRET_KEY', 'WEBHOOK_SECRET', 'POSTGRES_PASSWORD'):
            for value in ('', 'your-secret-key-here'):
                with self.subTest(name=name, value=value):
                    result = self.settings(**{name: value})
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(name, result.stderr)

    def test_production_requires_a_broker(self):
        result = self.settings(CELERY_BROKER_URL='')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('CELERY_BROKER_URL', result.stderr)

    def test_production_cannot_run_eagerly(self):
        result = self.settings(CELERY_TASK_ALWAYS_EAGER='True')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('CELERY_TASK_ALWAYS_EAGER', result.stderr)

    def test_debug_can_explicitly_run_eagerly(self):
        result = self.settings(DJANGO_DEBUG='True', CELERY_TASK_ALWAYS_EAGER='True', CELERY_BROKER_URL='')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)[1])

    def test_production_requires_shared_throttling(self):
        result = self.settings(REDIS_THROTTLE_URL='')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('REDIS_THROTTLE_URL', result.stderr)


if __name__ == '__main__':
    unittest.main()
