#!/bin/sh
set -eu
python manage.py migrate --noinput
exec gunicorn pmo.wsgi:application --bind 0.0.0.0:8000 --workers 2 --threads 2 --timeout 40 --access-logfile - --error-logfile -
