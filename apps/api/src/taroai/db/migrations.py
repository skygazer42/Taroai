import re
import sqlite3
from pathlib import Path

from pydantic import BaseModel

from taroai.db.models import DatabaseConfig, MigrationResult


class MigrationRunner(BaseModel):
    config: DatabaseConfig
    migrations_path: Path

    def connect(self):
        path = self.config.sqlite_path
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        return connection

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
            existing_versions = {
                row["version"]
                for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
            }
            for migration in sorted(self.migrations_path.glob("*.sql")):
                if migration.name in existing_versions:
                    continue
                connection.executescript(self._sqlite_sql(migration.read_text()))
                connection.execute(
                    "INSERT INTO schema_migrations (version) VALUES (?)",
                    (migration.name,),
                )
                applied_versions.append(migration.name)
        return MigrationResult(applied_versions=applied_versions)

    def _sqlite_sql(self, sql: str) -> str:
        translated = sql
        translated = translated.replace("TIMESTAMPTZ", "TEXT")
        translated = translated.replace("JSONB", "TEXT")
        translated = translated.replace("DOUBLE PRECISION", "REAL")
        translated = translated.replace("BIGINT", "INTEGER")
        translated = translated.replace("DEFAULT now()", "DEFAULT CURRENT_TIMESTAMP")
        translated = translated.replace("DEFAULT '{}'::jsonb", "DEFAULT '{}'")
        translated = translated.replace("DEFAULT '[]'::jsonb", "DEFAULT '[]'")
        translated = re.sub(r"REFERENCES [A-Za-z_]+\([^)]*\)", "", translated)
        translated = "\n".join(
            line
            for line in translated.splitlines()
            if not line.strip().startswith("FOREIGN KEY")
        )
        translated = re.sub(r",\s*\)", "\n)", translated)
        return translated
