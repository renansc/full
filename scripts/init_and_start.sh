#!/usr/bin/env bash
set -euo pipefail

echo "Inicializando banco do Zap (init-db)..."
python -m flask --app zap.wsgi init-db

echo "Iniciando servidor WSGI..."
exec gunicorn app:app --bind "0.0.0.0:${PORT:-5000}" --workers "${WEB_CONCURRENCY:-2}"
