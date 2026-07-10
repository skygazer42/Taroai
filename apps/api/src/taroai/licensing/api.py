from typing import Literal

from pydantic import BaseModel, Field

from taroai.licensing.models import LicenseStatus, LicenseValidationResult
from taroai.licensing.signing import SignedLicenseEnvelope


DeploymentMode = Literal["cloud", "byoc", "vpc", "private", "air_gapped"]


class LicenseImportRequest(BaseModel):
    deployment_mode: DeploymentMode
    envelope: SignedLicenseEnvelope


class LicenseImportResponse(BaseModel):
    license_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    customer_name: str = Field(min_length=1)
    status: LicenseStatus
    deployment_mode: str = Field(min_length=1)
    source: str = Field(min_length=1)
    entitlements_count: int = Field(ge=0)
    activated: bool = False

    @classmethod
    def from_validation(
        cls,
        validation: LicenseValidationResult,
        activated: bool,
    ) -> "LicenseImportResponse":
        return cls(
            license_id=validation.license.id,
            tenant_id=validation.license.tenant_id,
            customer_name=validation.license.customer_name,
            status=validation.status,
            deployment_mode=validation.deployment_mode,
            source=validation.source,
            entitlements_count=len(validation.license.entitlements),
            activated=activated,
        )
