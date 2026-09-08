#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
exec ./venv/bin/daphne -b "${SUPPORTDESK_HOST:-0.0.0.0}" -p "${SUPPORTDESK_PORT:-8000}" config.asgi:application
