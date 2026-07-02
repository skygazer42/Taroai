from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from taroai.config import Settings
from taroai.domain import new_id, utc_now


class DataResidencyResourceType(str, Enum):
    OBJECT_STORAGE = "object_storage"
    VECTOR_INDEX = "vector_index"
    SANDBOX_PROVIDER = "sandbox_provider"


class DataResidencyReportRequest(BaseModel):
    tenant_id: str = Field(min_length=1)
    requested_by_user_id: str = Field(min_length=1)


class DataResidencyResourceCheck(BaseModel):
    resource_type: DataResidencyResourceType
    region: str
    allowed: bool
    reason: str


class DataResidencyReport(BaseModel):
    id: str = Field(default_factory=lambda: new_id("data_residency_report"))
    tenant_id: str
    requested_by_user_id: str
    environment: str
    primary_region: str
    allowed_regions: list[str]
    cross_region_replication_mode: str
    compliant: bool
    checks: list[DataResidencyResourceCheck]
    created_at: datetime = Field(default_factory=utc_now)


class DataResidencyService(BaseModel):
    settings: Settings

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def create_report(self, request: DataResidencyReportRequest) -> DataResidencyReport:
        checks = [
            self._check_region(
                resource_type=DataResidencyResourceType.OBJECT_STORAGE,
                region=self.settings.object_storage_region,
            ),
            self._check_region(
                resource_type=DataResidencyResourceType.VECTOR_INDEX,
                region=self.settings.vector_index_region,
            ),
        ]
        if self.settings.sandbox_provider != "disabled":
            checks.append(
                self._check_region(
                    resource_type=DataResidencyResourceType.SANDBOX_PROVIDER,
                    region=self.settings.sandbox_provider_region,
                )
            )
        return DataResidencyReport(
            tenant_id=request.tenant_id,
            requested_by_user_id=request.requested_by_user_id,
            environment=self.settings.environment,
            primary_region=self.settings.data_residency_primary_region,
            allowed_regions=self.settings.data_residency_allowed_regions,
            cross_region_replication_mode=self.settings.data_residency_cross_region_replication_mode,
            compliant=all(check.allowed for check in checks),
            checks=checks,
        )

    def _check_region(
        self,
        resource_type: DataResidencyResourceType,
        region: str,
    ) -> DataResidencyResourceCheck:
        allowed = region in self.settings.data_residency_allowed_regions
        if allowed:
            reason = "region is allowed by data residency policy"
        else:
            reason = (
                f"{resource_type.value} region {region} is not in allowed regions "
                f"{self.settings.data_residency_allowed_regions}"
            )
        return DataResidencyResourceCheck(
            resource_type=resource_type,
            region=region,
            allowed=allowed,
            reason=reason,
        )
