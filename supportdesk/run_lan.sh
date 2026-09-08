#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
exec ./venv/bin/python manage.py runserver 0.0.0.0:"${SUPPORTDESK_PORT:-8000}"
