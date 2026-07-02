from pathlib import Path

from pydantic import BaseModel, Field


class DatabaseConfig(BaseModel):
    url: str = Field(min_length=1)

    @property
    def sqlite_path(self) -> Path:
        prefix = "sqlite:///"
        if not self.url.startswith(prefix):
            raise ValueError("Only sqlite:/// URLs are supported by the local SQL repository")
        return Path(self.url.removeprefix(prefix))


class MigrationResult(BaseModel):
    applied_versions: list[str] = Field(default_factory=list)
