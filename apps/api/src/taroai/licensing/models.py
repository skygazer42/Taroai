from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class LicenseEntitlementDeniedError(PermissionError):
    def __init__(self, message: str, metadata: dict | None = None):
        super().__init__(message)
        self.metadata = metadata or {}


class LicensedFeature(str, Enum):
    SSO = "sso"
    SCIM = "scim"
    PRIVATE_CONNECTOR_COUNT = "private_connector_count"
    SANDBOX_CONCURRENCY = "sandbox_concurrency"
    SOLUTION_PACKS = "solution_packs"
    AUDIT_RETENTION_DAYS = "audit_retention_days"


class LicenseStatus(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    INVALID = "invalid"


class Entitlement(BaseModel):
    feature: LicensedFeature
    enabled: bool = True
    limit: int | None = Field(default=None, ge=0)
    expires_at: datetime | None = None


class LicenseKey(BaseModel):
    id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    customer_name: str = Field(min_length=1)
    issued_at: datetime
    expires_at: datetime
    deployment_modes: list[Literal["cloud", "byoc", "vpc", "private", "air_gapped"]] = Field(
        min_length=1
    )
    entitlements: list[Entitlement] = Field(default_factory=list)
    offline_validation_allowed: bool = False

    @model_validator(mode="after")
    def validate_license_window(self) -> "LicenseKey":
        if self.issued_at >= self.expires_at:
            raise ValueError("license issued_at must be before expires_at")
        return self


class LicenseValidationResult(BaseModel):
    license: LicenseKey
    status: LicenseStatus
    deployment_mode: str
    source: Literal["document", "offline_file", "signed_offline_file"] = "document"
    reason: str = ""


class EntitlementDecision(BaseModel):
    feature: LicensedFeature
    allowed: bool
    requested_amount: int = Field(default=1, ge=0)
    limit: int | None = None
    reason: str = ""
