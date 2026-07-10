#!/usr/bin/env sh
set -eu

exec python - "$@" <<'PY'
import os
from pathlib import Path
import pwd
import sys

from taroai.config import load_settings
from taroai.db import DatabaseConfig, MigrationRunner

RUN_USER = "taroai"
DATA_DIR = Path("/data/taroai")


def chown_tree(path: Path, uid: int, gid: int) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for root, dirnames, filenames in os.walk(path):
        os.chown(root, uid, gid)
        for dirname in dirnames:
            os.chown(Path(root) / dirname, uid, gid)
        for filename in filenames:
            os.chown(Path(root) / filename, uid, gid)


settings = load_settings()
run_user = pwd.getpwnam(RUN_USER)

if os.getuid() == 0:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    Path(settings.sandbox_root_dir).mkdir(parents=True, exist_ok=True)
    chown_tree(DATA_DIR, run_user.pw_uid, run_user.pw_gid)
    os.setgroups([])
    os.setgid(run_user.pw_gid)
    os.setuid(run_user.pw_uid)

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
