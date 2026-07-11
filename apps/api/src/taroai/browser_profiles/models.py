from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from taroai.domain import new_id, utc_now


def normalize_domains(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        domain = value.strip().lower().strip(".")
        if not domain or "/" in domain or ":" in domain or " " in domain:
            raise ValueError("Browser profile domains must be hostnames without paths")
        if domain not in normalized:
            normalized.append(domain)
    return normalized


class BrowserProfileCreate(BaseModel):
    workspace_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    allowed_domains: list[str] = Field(default_factory=list, max_length=100)
    is_default: bool = False

    model_config = ConfigDict(extra="forbid")

    @field_validator("allowed_domains")
    @classmethod
    def validate_domains(cls, value: list[str]) -> list[str]:
        return normalize_domains(value)


class BrowserProfilePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    status: Literal["active", "disabled"] | None = None
    allowed_domains: list[str] | None = Field(default=None, max_length=100)
    is_default: bool | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("allowed_domains")
    @classmethod
    def validate_domains(cls, value: list[str] | None) -> list[str] | None:
        return normalize_domains(value) if value is not None else None


class BrowserProfile(BaseModel):
    id: str = Field(default_factory=lambda: new_id("browser_profile"))
    tenant_id: str
    workspace_id: str
    name: str
    description: str = ""
    status: Literal["active", "disabled"] = "active"
    secret_ref_id: str | None = None
    secret_backend: str | None = None
    secret_external_name: str | None = None
    allowed_domains: list[str] = Field(default_factory=list)
    is_default: bool = False
    revision: int = Field(default=0, ge=0)
    created_by_user_id: str
    last_used_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class BrowserProfileSessionCreate(BaseModel):
    start_url: str | None = Field(default=None, max_length=4000)

    model_config = ConfigDict(extra="forbid")


class BrowserProfileSession(BaseModel):
    session_id: str
    tenant_id: str
    workspace_id: str
    profile_id: str | None = None
    run_id: str | None = None
    status: Literal["active", "closed", "failed"] = "active"
    current_url: str | None = None
    created_by_user_id: str | None = None
    started_at: datetime = Field(default_factory=utc_now)
    last_seen_at: datetime = Field(default_factory=utc_now)
    closed_at: datetime | None = None
