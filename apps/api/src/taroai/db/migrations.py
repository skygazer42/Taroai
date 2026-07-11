import re
from pathlib import Path

from pydantic import BaseModel

from taroai.db.connection import connect_database
from taroai.db.models import (
    DatabaseConfig,
    MigrationPlan,
    MigrationResult,
)


class MigrationRunner(BaseModel):
    config: DatabaseConfig
    migrations_path: Path

    def connect(self):
        return connect_database(self.config)

    def plan(self) -> MigrationPlan:
        available_versions = self._available_versions()
        available_version_set = set(available_versions)
        with self.connect() as connection:
            existing_versions = self._existing_versions(connection)
        applied_versions = [
            version for version in available_versions if version in existing_versions
        ]
        pending_versions = [
            version for version in available_versions if version not in existing_versions
        ]
        unknown_applied_versions = sorted(existing_versions - available_version_set)
        return MigrationPlan(
            available_versions=available_versions,
            applied_versions=applied_versions,
            pending_versions=pending_versions,
            unknown_applied_versions=unknown_applied_versions,
            up_to_date=pending_versions == [] and unknown_applied_versions == [],
        )

    def apply(self) -> MigrationResult:
        applied_versions: list[str] = []
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            existing_versions = self._existing_versions(connection)
            for migration in self._migration_files():
                if migration.name in existing_versions:
                    continue
                self._execute_script(connection, self._migration_sql(migration.read_text()))
                connection.execute(
                    "INSERT INTO schema_migrations (version) VALUES (?)",
                    (migration.name,),
                )
                applied_versions.append(migration.name)
        return MigrationResult(applied_versions=applied_versions)

    def _migration_files(self) -> list[Path]:
        return sorted(self.migrations_path.glob("*.sql"))

    def _available_versions(self) -> list[str]:
        return [migration.name for migration in self._migration_files()]

    def _existing_versions(self, connection) -> set[str]:
        try:
            rows = connection.execute("SELECT version FROM schema_migrations").fetchall()
        except Exception as error:
            if self._is_missing_schema_migrations_error(error):
                return set()
            raise
        return {self._row_version(row) for row in rows}

    def _row_version(self, row) -> str:
        try:
            return str(row["version"])
        except (TypeError, KeyError):
            return str(row[0])

    def _is_missing_schema_migrations_error(self, error: Exception) -> bool:
        error_text = str(error).lower()
        error_sqlstate = getattr(error, "sqlstate", "")
        return (
            "schema_migrations" in error_text
            and (
                "no such table" in error_text
                or "does not exist" in error_text
                or error_sqlstate == "42P01"
            )
        )

    def _migration_sql(self, sql: str) -> str:
        sql = self._filter_dialect_only_blocks(sql)
        if self.config.dialect == "sqlite":
            return self._sqlite_sql(sql)
        return sql

    def _filter_dialect_only_blocks(self, sql: str) -> str:
        lines: list[str] = []
        inside_dialect_block: str | None = None
        for line in sql.splitlines():
            directive = line.strip()
            block_start = re.fullmatch(r"-- taroai:(postgresql|sqlite)-only-start", directive)
            block_end = re.fullmatch(r"-- taroai:(postgresql|sqlite)-only-end", directive)
            if block_start is not None:
                if inside_dialect_block is not None:
                    raise ValueError("nested dialect-only migration block is not supported")
                inside_dialect_block = block_start.group(1)
                continue
            if block_end is not None:
                if inside_dialect_block != block_end.group(1):
                    raise ValueError("dialect-only migration block end without matching start")
                inside_dialect_block = None
                continue
            if inside_dialect_block is not None and inside_dialect_block != self.config.dialect:
                continue
            lines.append(line)
        if inside_dialect_block is not None:
            raise ValueError("dialect-only migration block is missing an end marker")
        return "\n".join(lines)

    def _sqlite_sql(self, sql: str) -> str:
        translated = sql
        translated = translated.replace("TIMESTAMPTZ", "TEXT")
        translated = translated.replace("JSONB", "TEXT")
        translated = translated.replace("DOUBLE PRECISION", "REAL")
        translated = translated.replace("BIGINT", "INTEGER")
        translated = translated.replace("DEFAULT now()", "DEFAULT CURRENT_TIMESTAMP")
        translated = translated.replace("DEFAULT '{}'::jsonb", "DEFAULT '{}'")
        translated = translated.replace("DEFAULT '[]'::jsonb", "DEFAULT '[]'")
        translated = re.sub(
            r"REFERENCES\s+[A-Za-z_]+\s*\([^)]*\)(?:\s+ON\s+DELETE\s+[A-Za-z]+)?",
            "",
            translated,
            flags=re.IGNORECASE,
        )
        translated = "\n".join(
            line
            for line in translated.splitlines()
            if not line.strip().startswith(("FOREIGN KEY", "REFERENCES", "ON DELETE"))
        )
        translated = re.sub(r",\s*\)", "\n)", translated)
        return translated

    def _execute_script(self, connection, sql: str) -> None:
        for statement in [part.strip() for part in sql.split(";") if part.strip()]:
            connection.execute("SAVEPOINT taroai_migration_statement")
            try:
                connection.execute(statement)
                connection.execute("RELEASE SAVEPOINT taroai_migration_statement")
            except Exception as error:
                connection.execute("ROLLBACK TO SAVEPOINT taroai_migration_statement")
                connection.execute("RELEASE SAVEPOINT taroai_migration_statement")
                if self._is_skippable_partial_schema_error(statement, error):
                    continue
                raise

    def _is_skippable_partial_schema_error(self, statement: str, error: Exception) -> bool:
        normalized = " ".join(statement.lower().split())
        error_text = str(error).lower()
        error_sqlstate = getattr(error, "sqlstate", "")
        duplicate_column = (
            normalized.startswith("alter table")
            and " add column " in normalized
            and (
                "duplicate column name" in error_text
                or "already exists" in error_text
                or error_sqlstate == "42701"
            )
        )
        missing_sqlite_upgrade_table = (
            self.config.dialect == "sqlite"
            and (
                "no such table" in error_text
                or "no such column" in error_text
            )
            and normalized.startswith(("alter table", "update ", "create index"))
        )
        return duplicate_column or missing_sqlite_upgrade_table
