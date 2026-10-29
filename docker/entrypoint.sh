#!/bin/sh
set -e

python -c 'import django; django.setup()'
exec "$@"
