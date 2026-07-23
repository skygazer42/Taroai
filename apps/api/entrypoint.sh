#!/usr/bin/env sh
set -eu

exec python - "$@" <<'PY'
import os
from pathlib import Path
import sys

from taroai.config import load_settings
from taroai.db import DatabaseConfig, MigrationRunner

settings = load_settings()

if os.environ.get("TAROAI_RUN_MIGRATIONS", "false") == "true":
    result = MigrationRunner(
        config=DatabaseConfig(url=settings.database_url),
        migrations_path=Path("/app/migrations"),
    ).apply()
    print(f"Applied migrations: {','.join(result.applied_versions) or 'none'}")

if len(sys.argv) < 2:
    raise SystemExit("entrypoint command is required")

os.execvp(sys.argv[1], sys.argv[1:])
PY
