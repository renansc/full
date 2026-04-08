#!/usr/bin/env bash
set -euo pipefail

wait_for_mysql() {
  python - <<'PY'
import os
import sys
import time
from urllib.parse import urlparse

import pymysql

database_url = os.environ.get("DATABASE_URL", "")
if not database_url.startswith("mysql"):
    sys.exit(0)

parsed = urlparse(database_url.replace("mysql+pymysql://", "mysql://"))
host = parsed.hostname or "db"
port = parsed.port or 3306
user = parsed.username or "root"
password = parsed.password or ""
database = parsed.path.lstrip("/") or ""

for attempt in range(60):
    try:
        conn = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            connect_timeout=5,
        )
        conn.close()
        sys.exit(0)
    except Exception:
        time.sleep(2)

print("MySQL did not become ready in time.", file=sys.stderr)
sys.exit(1)
PY
}

wait_for_mysql

python -m flask --app wsgi:app init-db
exec gunicorn --bind 0.0.0.0:8000 --workers 2 --threads 4 wsgi:app
