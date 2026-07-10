import argparse
import json
from pathlib import Path

from taroai.db.migrations import MigrationRunner
from taroai.db.models import (
    MigrationCommandConfig,
    MigrationPlan,
    MigrationResult,
)


def parse_args(argv: list[str] | None = None) -> MigrationCommandConfig:
    parser = argparse.ArgumentParser(
        description="Plan or apply Taroai database migrations."
    )
    parser.add_argument("--database-url", required=True)
    parser.add_argument(
        "--migrations-path",
        default="/app/migrations",
        type=Path,
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply pending migrations. Omit for dry-run plan output.",
    )
    parsed = parser.parse_args(argv)
    return MigrationCommandConfig(
        database_url=parsed.database_url,
        migrations_path=parsed.migrations_path,
        mode="apply" if parsed.apply else "plan",
    )


def run_migration_command(
    config: MigrationCommandConfig,
) -> MigrationPlan | MigrationResult:
    runner = MigrationRunner(
        config=config.database_config(),
        migrations_path=config.migrations_path,
    )
    if config.mode == "apply":
        return runner.apply()
    return runner.plan()


def main(argv: list[str] | None = None) -> int:
    result = run_migration_command(parse_args(argv))
    print(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
