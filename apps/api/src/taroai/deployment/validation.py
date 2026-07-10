from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, model_validator


class InstallValidationCheckName(str, Enum):
    RELEASE_PACKAGE_INTEGRITY = "release_package_integrity"
    DATABASE_MIGRATION = "database_migration"
    REDIS_CONNECTIVITY = "redis_connectivity"
    OBJECT_STORAGE_READ_WRITE = "object_storage_read_write"
    SECRET_MANAGER_READ = "secret_manager_read"
    MODEL_GATEWAY_HEALTH = "model_gateway_health"
    SANDBOX_HEALTH = "sandbox_health"
    BROWSER_CONTROLLER_HEALTH = "browser_controller_health"
    WEB_WORKSPACE_HEALTH = "web_workspace_health"
    API_HEALTH = "api_health"
    EVENT_STREAM = "event_stream"
    WORKER_QUEUE = "worker_queue"
    AUDIT_WRITE = "audit_write"
    TRACE_COLLECTOR = "trace_collector"
    SUPPORT_BUNDLE_REDACTION = "support_bundle_redaction"
    BACKUP_RESTORE_DRILL = "backup_restore_drill"
    RUNTIME_CLOSED_LOOP = "runtime_closed_loop"


class InstallValidationStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


REQUIRED_INSTALL_VALIDATION_CHECKS = set(InstallValidationCheckName)


class InstallValidationCheck(BaseModel):
    name: InstallValidationCheckName
    status: InstallValidationStatus
    dependency: str = Field(min_length=1)
    message: str = Field(min_length=1)
    remediation: str = ""
    observed_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_failed_check_has_remediation(self) -> "InstallValidationCheck":
        if self.status == InstallValidationStatus.FAILED and not self.remediation.strip():
            raise ValueError("failed validation checks require remediation")
        return self


class InstallValidationReport(BaseModel):
    deployment_id: str = Field(min_length=1)
    deployment_mode: str = Field(min_length=1)
    checked_at: datetime
    checks: list[InstallValidationCheck] = Field(min_length=1)
    status: InstallValidationStatus = InstallValidationStatus.PASSED

    @model_validator(mode="after")
    def validate_report_contract(self) -> "InstallValidationReport":
        check_names = [check.name for check in self.checks]
        duplicates = sorted({name.value for name in check_names if check_names.count(name) > 1})
        if duplicates:
            raise ValueError(f"install validation report duplicate checks: {duplicates}")

        missing = sorted(
            check.value for check in REQUIRED_INSTALL_VALIDATION_CHECKS - set(check_names)
        )
        if missing:
            raise ValueError(f"install validation report missing checks: {missing}")

        if any(check.status == InstallValidationStatus.FAILED for check in self.checks):
            self.status = InstallValidationStatus.FAILED
        elif any(check.status == InstallValidationStatus.SKIPPED for check in self.checks):
            self.status = InstallValidationStatus.SKIPPED
        else:
            self.status = InstallValidationStatus.PASSED
        return self

    @property
    def is_ready(self) -> bool:
        return self.status == InstallValidationStatus.PASSED

    def failed_checks(self) -> list[InstallValidationCheck]:
        return [check for check in self.checks if check.status == InstallValidationStatus.FAILED]

    def failure_summary(self) -> list[str]:
        return [
            f"{check.name.value}: {check.dependency} - {check.message} "
            f"(remediation: {check.remediation})"
            for check in self.failed_checks()
        ]
