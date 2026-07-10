from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, model_validator


DatabaseDialect = Literal["sqlite", "postgresql"]


class DatabaseConfig(BaseModel):
    url: str = Field(min_length=1)
    pool_min_size: int = Field(default=1, ge=0)
    pool_max_size: int = Field(default=10, ge=1)
    pool_timeout_seconds: int = Field(default=30, ge=1)

    @model_validator(mode="after")
    def validate_supported_database_url(self) -> "DatabaseConfig":
        _ = self.dialect
        if self.pool_max_size < self.pool_min_size:
            raise ValueError("database pool max size must be greater than or equal to min size")
        return self

    @property
    def dialect(self) -> DatabaseDialect:
        scheme = urlparse(self.url).scheme
        if scheme == "sqlite":
            return "sqlite"
        if scheme in {"postgresql", "postgres"}:
            return "postgresql"
        raise ValueError(f"Unsupported database URL scheme: {scheme}")

    @property
    def is_postgresql(self) -> bool:
        return self.dialect == "postgresql"

    @property
    def sqlite_path(self) -> Path:
        prefix = "sqlite:///"
        if self.dialect != "sqlite":
            raise ValueError("sqlite_path is only available for sqlite database URLs")
        if not self.url.startswith(prefix):
            raise ValueError("sqlite database URLs must start with sqlite:///")
        return Path(self.url.removeprefix(prefix))


class MigrationResult(BaseModel):
    applied_versions: list[str] = Field(default_factory=list)


class MigrationPlan(BaseModel):
    available_versions: list[str] = Field(default_factory=list)
    applied_versions: list[str] = Field(default_factory=list)
    pending_versions: list[str] = Field(default_factory=list)
    unknown_applied_versions: list[str] = Field(default_factory=list)
    up_to_date: bool = False


MigrationCommandMode = Literal["plan", "apply"]


class MigrationCommandConfig(BaseModel):
    database_url: str = Field(min_length=1)
    migrations_path: Path
    mode: MigrationCommandMode = "plan"

    def database_config(self) -> DatabaseConfig:
        return DatabaseConfig(url=self.database_url)
