import pytest
from pydantic import ValidationError

from taroai.config import Settings
from taroai.lifecycle import (
    DataResidencyReportRequest,
    DataResidencyResourceType,
    DataResidencyService,
)


def test_data_residency_report_accepts_resources_in_allowed_regions():
    settings = Settings(
        data_residency_primary_region="us-east-1",
        data_residency_allowed_regions=["us-east-1", "us-west-2"],
        data_residency_cross_region_replication_mode="approved_regions",
        object_storage_region="us-west-2",
        vector_index_region="us-east-1",
        sandbox_provider="e2b",
        sandbox_provider_region="us-east-1",
        _env_file=None,
    )
    service = DataResidencyService(settings=settings)

    report = service.create_report(
        DataResidencyReportRequest(
            tenant_id="tenant_acme",
            requested_by_user_id="user_admin",
        )
    )

    assert report.compliant is True
    assert report.primary_region == "us-east-1"
    assert report.allowed_regions == ["us-east-1", "us-west-2"]
    assert report.cross_region_replication_mode == "approved_regions"
    assert [check.resource_type for check in report.checks] == [
        DataResidencyResourceType.OBJECT_STORAGE,
        DataResidencyResourceType.VECTOR_INDEX,
        DataResidencyResourceType.SANDBOX_PROVIDER,
    ]
    assert all(check.allowed for check in report.checks)


def test_data_residency_report_flags_resources_outside_allowed_regions():
    settings = Settings(
        data_residency_primary_region="eu-central-1",
        data_residency_allowed_regions=["eu-central-1"],
        object_storage_region="us-east-1",
        vector_index_region="eu-central-1",
        sandbox_provider="e2b",
        sandbox_provider_region="us-west-2",
        _env_file=None,
    )
    service = DataResidencyService(settings=settings)

    report = service.create_report(
        DataResidencyReportRequest(
            tenant_id="tenant_acme",
            requested_by_user_id="user_admin",
        )
    )

    assert report.compliant is False
    disallowed = [check for check in report.checks if not check.allowed]
    assert [check.resource_type for check in disallowed] == [
        DataResidencyResourceType.OBJECT_STORAGE,
        DataResidencyResourceType.SANDBOX_PROVIDER,
    ]
    assert all("not in allowed regions" in check.reason for check in disallowed)


def test_data_residency_settings_reject_primary_region_outside_allowed_regions():
    with pytest.raises(ValidationError):
        Settings(
            data_residency_primary_region="us-east-1",
            data_residency_allowed_regions=["eu-central-1"],
            _env_file=None,
        )
