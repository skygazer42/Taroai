import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener

from pydantic import BaseModel, Field, model_validator

from taroai.config import CUSTOMER_OPERATED_DEPLOYMENT_MODES, DEFAULT_OPERATOR_TOKEN_VALUES
from taroai.deployment.install_evidence import (
    AuditWriteVerificationResult,
    BrowserControllerVerificationResult,
    EventStreamVerificationResult,
    RestoreDrillVerificationResult,
    SandboxLifecycleVerificationResult,
)
from taroai.deployment.local_cloud_poc_demo_gate import (
    DemoReadinessGateConfig,
    DemoReadinessGateReport,
    run_demo_readiness_gate,
)
from taroai.deployment.local_cloud_poc_verification import (
    LocalCloudPocVerificationResult,
)
from taroai.deployment.release_package import (
    ReleasePackageVerificationConfig,
    atomic_write_text,
    parse_trusted_public_keys,
    verify_release_package,
)
from taroai.deployment.transfer_evidence import ReleaseTransferEvidenceReport
from taroai.deployment.validation import (
    InstallValidationCheck,
    InstallValidationCheckName,
    InstallValidationReport,
)
from taroai.db.models import MigrationPlan
from taroai.model_gateway.verification import OpenAICompatibleModelGatewayVerificationResult
from taroai.observability.verification import TraceCollectorVerificationResult
from taroai.sandbox.image_policy import (
    sandbox_runtime_image_digest_pinned,
    sandbox_runtime_image_has_non_latest_tag,
    sandbox_runtime_image_has_registry,
    sandbox_runtime_image_policy_failure_details,
)
from taroai.sandbox.kubernetes_verification import KubernetesSandboxVerificationResult
from taroai.secrets.verification import SecretManagerVerificationResult
from taroai.storage.object_storage_verification import ObjectStorageVerificationResult
from taroai.support.redaction import SupportBundleRedactionReport
from taroai.workers.models import JobStatus
from taroai.workers.redis_verification import RedisQueueVerificationResult


PRODUCTION_INSTALL_VALIDATION_MODES = {"prod", "production"}
CUSTOMER_OPERATED_INSTALL_VALIDATION_MODES = (
    CUSTOMER_OPERATED_DEPLOYMENT_MODES | PRODUCTION_INSTALL_VALIDATION_MODES
)
RUNTIME_EXECUTION_EVIDENCE_MODES = (
    CUSTOMER_OPERATED_INSTALL_VALIDATION_MODES | {"cloud"}
)
RELEASE_ACCEPTANCE_EVIDENCE_MODES = RUNTIME_EXECUTION_EVIDENCE_MODES
ENTERPRISE_SANDBOX_EVIDENCE_PROVIDERS = {"k8s", "kubernetes", "e2b"}
MODEL_GATEWAY_EXPECTED_PLANNING_TOOL = "planning.record"
SANDBOX_READINESS_REQUIRED_CAPABILITY_FLAGS = {
    "network_isolation_declared": "sandbox_network_isolation_declared",
    "filesystem_isolation_declared": "sandbox_filesystem_isolation_declared",
    "resource_limits_declared": "sandbox_resource_limits_declared",
    "destroy_supported_declared": "sandbox_destroy_supported_declared",
    "session_ttl_enforced_declared": "sandbox_session_ttl_enforced_declared",
    "runtime_isolation_declared": "sandbox_runtime_isolation_declared",
    "image_policy_enforced_declared": "sandbox_image_policy_enforced_declared",
}
SANDBOX_READINESS_REQUIRED_CAPACITY_FIELDS = {
    "max_session_ttl_seconds": "sandbox_max_session_ttl_seconds",
    "max_sessions": "sandbox_max_sessions",
    "max_sessions_per_tenant": "sandbox_max_sessions_per_tenant",
    "max_sessions_per_run": "sandbox_max_sessions_per_run",
}
BROWSER_READINESS_REQUIRED_CAPABILITY_FLAGS = {
    "auth_required_declared": "browser_auth_required_declared",
    "session_ttl_enforced_declared": "browser_session_ttl_enforced_declared",
}
BROWSER_READINESS_REQUIRED_CAPACITY_FIELDS = {
    "max_session_ttl_seconds": "browser_max_session_ttl_seconds",
    "max_sessions": "browser_max_sessions",
    "max_sessions_per_tenant": "browser_max_sessions_per_tenant",
    "max_sessions_per_run": "browser_max_sessions_per_run",
}


class InstallValidationRunConfig(BaseModel):
    deployment_id: str = Field(default="local", min_length=1)
    deployment_mode: str = Field(default="private", min_length=1)
    api_base_url: str = Field(default="http://localhost:8000", min_length=1)
    browser_base_url: str = Field(default="http://localhost:8001", min_length=1)
    sandbox_controller_api_key: str = Field(default="", repr=False)
    browser_controller_api_key: str = Field(default="", repr=False)
    web_base_url: str | None = None
    release_package_path: str | None = None
    expected_release_package_checksum_sha256: str | None = Field(
        default=None,
        pattern=r"^[a-fA-F0-9]{64}$",
    )
    release_package_signature_path: str | None = None
    release_package_trusted_public_keys: dict[str, str] = Field(default_factory=dict)
    release_transfer_evidence_path: str | None = None
    migration_plan_path: str | None = None
    object_storage_verification_path: str | None = None
    redis_queue_verification_path: str | None = None
    secret_manager_verification_path: str | None = None
    model_gateway_verification_path: str | None = None
    sandbox_verification_path: str | None = None
    kubernetes_sandbox_verification_path: str | None = None
    browser_controller_verification_path: str | None = None
    event_stream_verification_path: str | None = None
    audit_write_verification_path: str | None = None
    trace_collector_verification_path: str | None = None
    support_bundle_redaction_evidence_path: str | None = None
    restore_drill_verification_path: str | None = None
    runtime_closed_loop_evidence_path: str | None = None
    output_path: str | None = None
    timeout_seconds: int = Field(default=30, ge=1)

    @model_validator(mode="after")
    def validate_urls(self) -> "InstallValidationRunConfig":
        validate_http_url(self.api_base_url, "api_base_url")
        validate_http_url(self.browser_base_url, "browser_base_url")
        if self.web_base_url is not None:
            validate_http_url(self.web_base_url, "web_base_url")
        return self


class InstallValidationHttpResponse(BaseModel):
    status_code: int = Field(ge=100)
    body: str = ""

    def json_body(self) -> dict[str, Any]:
        if not self.body.strip():
            return {}
        parsed = json.loads(self.body)
        if not isinstance(parsed, dict):
            raise RuntimeError("install validation expected a JSON object response")
        return parsed


class InstallValidationHttpClient:
    def __init__(self, timeout_seconds: int = 30):
        self.timeout_seconds = timeout_seconds
        self.opener = build_opener(ProxyHandler({}))

    def get(self, url: str, headers: dict | None = None) -> InstallValidationHttpResponse:
        request = Request(url, headers=headers or {}, method="GET")
        try:
            with self.opener.open(request, timeout=self.timeout_seconds) as response:
                return InstallValidationHttpResponse(
                    status_code=response.status,
                    body=response.read().decode("utf-8", errors="replace"),
                )
        except HTTPError as error:
            return InstallValidationHttpResponse(
                status_code=error.code,
                body=error.read().decode("utf-8", errors="replace"),
            )
        except (TimeoutError, URLError) as error:
            raise RuntimeError(f"install validation request failed: {error}") from error


def run_install_validation(
    config: InstallValidationRunConfig,
    http_client: InstallValidationHttpClient | None = None,
) -> InstallValidationReport:
    client = http_client or InstallValidationHttpClient(
        timeout_seconds=config.timeout_seconds
    )
    checked_at = datetime.now(timezone.utc)
    readiness_body: dict[str, Any] = {}
    checks: list[InstallValidationCheck] = []
    redis_check, worker_queue_check = check_redis_queue_verification_result(config)

    api_check, readiness_body = check_api_health(config, client)
    checks.extend(
        [
            check_release_package_integrity(config),
            check_database_migration_plan(config),
            redis_check,
            check_object_storage_verification_result(config),
            check_secret_manager_verification_result(config),
            check_model_gateway_readiness(config, readiness_body),
            check_sandbox_readiness(config, readiness_body),
            check_browser_controller_health(config, client, readiness_body),
            check_web_workspace_health(config, client),
            api_check,
            check_event_stream_verification_result(config),
            worker_queue_check,
            check_audit_write_verification_result(config),
            check_trace_collector_verification_result(config),
            check_support_bundle_redaction_evidence_result(config),
            check_restore_drill_verification_result(config),
            check_runtime_closed_loop_evidence_result(config),
        ]
    )
    return InstallValidationReport(
        deployment_id=config.deployment_id,
        deployment_mode=config.deployment_mode,
        checked_at=checked_at,
        checks=checks,
    )


def check_release_package_integrity(
    config: InstallValidationRunConfig,
) -> InstallValidationCheck:
    metadata: dict[str, str | int | float | bool] = {}
    release_transfer_evidence: ReleaseTransferEvidenceReport | None = None
    if config.release_transfer_evidence_path:
        metadata["release_transfer_evidence_path"] = config.release_transfer_evidence_path
        try:
            release_transfer_evidence = ReleaseTransferEvidenceReport.model_validate_json(
                Path(config.release_transfer_evidence_path).read_text()
            )
        except Exception:
            return InstallValidationCheck(
                name=InstallValidationCheckName.RELEASE_PACKAGE_INTEGRITY,
                status="failed",
                dependency="release_package",
                message="release transfer evidence could not be read or did not match schema",
                remediation=(
                    "rerun scripts/build-release-transfer-evidence.sh from a verified "
                    "signed package and provide the generated evidence JSON"
                ),
                metadata=metadata,
            )
        metadata.update(release_transfer_evidence_metadata(release_transfer_evidence))
        if (
            not release_transfer_evidence.valid
            or not release_transfer_evidence.verification_valid
            or not release_transfer_evidence.signature_valid
        ):
            return InstallValidationCheck(
                name=InstallValidationCheckName.RELEASE_PACKAGE_INTEGRITY,
                status="failed",
                dependency="release_package",
                message="release transfer evidence is not valid",
                remediation=(
                    "rerun scripts/build-release-transfer-evidence.sh after release "
                    "package verification passes"
                ),
                metadata=metadata,
            )

    release_package_path = config.release_package_path
    if release_package_path is None and release_transfer_evidence is not None:
        release_package_path = str(
            resolve_release_transfer_evidence_reference(
                release_transfer_evidence.package_path,
                config.release_transfer_evidence_path,
            )
        )
    if not release_package_path:
        if release_acceptance_evidence_required(config):
            return failed_check(
                InstallValidationCheckName.RELEASE_PACKAGE_INTEGRITY,
                "release_package",
                "release package integrity validation requires --release-package",
                (
                    "build and verify the release package, then provide the "
                    "release package path or transfer evidence before accepting "
                    "the install"
                ),
            )
        return skipped_check(
            InstallValidationCheckName.RELEASE_PACKAGE_INTEGRITY,
            "release_package",
            "release package integrity validation requires --release-package",
        )
    expected_checksum = (
        config.expected_release_package_checksum_sha256
        or (
            release_transfer_evidence.package_sha256
            if release_transfer_evidence is not None
            else None
        )
    )
    signature_path = config.release_package_signature_path
    if signature_path is None and release_transfer_evidence is not None:
        signature_path = str(
            resolve_release_transfer_evidence_reference(
                release_transfer_evidence.signature_path,
                config.release_transfer_evidence_path,
            )
        )
    transfer_path_error = release_transfer_evidence_path_safety_error(
        release_package_path=release_package_path,
        release_package_signature_path=signature_path,
        release_transfer_evidence=release_transfer_evidence,
        explicit_release_package_path=config.release_package_path,
        explicit_signature_path=config.release_package_signature_path,
        release_transfer_evidence_path=config.release_transfer_evidence_path,
    )
    if transfer_path_error:
        return InstallValidationCheck(
            name=InstallValidationCheckName.RELEASE_PACKAGE_INTEGRITY,
            status="failed",
            dependency="release_package",
            message=transfer_path_error,
            remediation=(
                "rerun scripts/build-release-transfer-evidence.sh beside the "
                "signed release package or pass explicit trusted package and "
                "signature paths"
            ),
            metadata=metadata | {"package_path": release_package_path},
        )
    trusted_public_keys = (
        config.release_package_trusted_public_keys
        or (
            {release_transfer_evidence.signature_key_id: release_transfer_evidence.public_key_base64}
            if release_transfer_evidence is not None
            else {}
        )
    )
    report = verify_release_package(
        ReleasePackageVerificationConfig(
            package_path=Path(release_package_path),
            expected_checksum_sha256=expected_checksum,
            signature_path=Path(signature_path) if signature_path else None,
            trusted_public_keys=trusted_public_keys,
            signature_required=release_acceptance_evidence_required(config),
        )
    )
    metadata["package_path"] = release_package_path
    if report.checksum_sha256:
        metadata["checksum_sha256"] = report.checksum_sha256
    if report.expected_checksum_sha256:
        metadata["expected_checksum_sha256"] = report.expected_checksum_sha256
    if report.signature_valid is not None:
        metadata["signature_valid"] = report.signature_valid
    if report.signature_key_id:
        metadata["signature_key_id"] = report.signature_key_id
    metadata.update(release_package_failure_count_metadata(report))
    if report.valid:
        return InstallValidationCheck(
            name=InstallValidationCheckName.RELEASE_PACKAGE_INTEGRITY,
            status="passed",
            dependency="release_package",
            message="release package passed verifier checks",
            metadata=metadata,
        )

    failure_details = release_package_failure_details(report)
    return InstallValidationCheck(
        name=InstallValidationCheckName.RELEASE_PACKAGE_INTEGRITY,
        status="failed",
        dependency="release_package",
        message=f"release package verification failed: {failure_details}",
        remediation=(
            "rerun scripts/verify-release-package.sh with the expected checksum, "
            "then transfer or install the verified package artifact"
        ),
        metadata=metadata,
    )


def release_transfer_evidence_metadata(
    evidence: ReleaseTransferEvidenceReport,
) -> dict[str, str | int | float | bool]:
    return {
        "transfer_evidence_package_version": evidence.package_version,
        "transfer_evidence_app_version": evidence.app_version,
        "transfer_evidence_migration_count": evidence.migration_count,
        "transfer_evidence_image_count": evidence.image_count,
        "transfer_evidence_required_service_count": evidence.required_service_count,
        "transfer_evidence_signature_key_id": evidence.signature_key_id,
    }


def release_transfer_evidence_path_safety_error(
    release_package_path: str,
    release_package_signature_path: str | None,
    release_transfer_evidence: ReleaseTransferEvidenceReport | None,
    explicit_release_package_path: str | None,
    explicit_signature_path: str | None,
    release_transfer_evidence_path: str | None,
) -> str | None:
    if release_transfer_evidence is None:
        return None

    if (
        explicit_release_package_path is None
        and release_transfer_evidence_path is not None
    ):
        evidence_parent = Path(release_transfer_evidence_path).resolve(strict=False).parent
        package_path = Path(release_package_path).resolve(strict=False)
        if package_path.parent != evidence_parent:
            return "release transfer evidence package path must stay with transfer evidence"

    if explicit_signature_path or release_package_signature_path is None:
        return None

    package_parent = Path(release_package_path).resolve(strict=False).parent
    signature_path = Path(release_package_signature_path).resolve(strict=False)
    try:
        signature_path.relative_to(package_parent)
    except ValueError:
        return "release transfer evidence signature path must stay with release package"
    return None


def resolve_release_transfer_evidence_reference(
    reference_path: Path,
    release_transfer_evidence_path: str | None,
) -> Path:
    if reference_path.is_absolute() or release_transfer_evidence_path is None:
        return reference_path.resolve(strict=False)
    evidence_parent = Path(release_transfer_evidence_path).resolve(strict=False).parent
    evidence_relative_path = (evidence_parent / reference_path).resolve(strict=False)
    if evidence_relative_path.exists():
        return evidence_relative_path
    return reference_path.resolve(strict=False)


def release_package_failure_details(report) -> str:
    detail_groups = [
        report.checksum_mismatch_errors,
        report.manifest_image_errors,
        report.manifest_schema_errors,
        report.upgrade_matrix_errors,
        report.signature_errors,
        report.duplicate_entries,
        report.unsafe_entries,
        report.symlink_entries,
        report.forbidden_entries,
        report.non_executable_script_entries,
        report.invalid_python_entries,
        report.missing_required_entries,
        report.missing_import_dependency_entries,
        report.missing_script_module_entries,
        report.missing_migration_entries,
        report.migration_checksum_mismatches,
        report.secret_pattern_entries,
        report.errors,
    ]
    details = [str(item) for group in detail_groups for item in group]
    return "; ".join(details) or "unknown package verification error"


def release_package_failure_count_metadata(report) -> dict[str, int]:
    count_fields = {
        "checksum_mismatch_error_count": report.checksum_mismatch_errors,
        "manifest_image_error_count": report.manifest_image_errors,
        "manifest_schema_error_count": report.manifest_schema_errors,
        "upgrade_matrix_error_count": report.upgrade_matrix_errors,
        "signature_error_count": report.signature_errors,
        "duplicate_entry_count": report.duplicate_entries,
        "unsafe_entry_count": report.unsafe_entries,
        "symlink_entry_count": report.symlink_entries,
        "forbidden_entry_count": report.forbidden_entries,
        "non_executable_script_entry_count": report.non_executable_script_entries,
        "invalid_python_error_count": report.invalid_python_entries,
        "missing_required_entry_count": report.missing_required_entries,
        "missing_import_dependency_count": report.missing_import_dependency_entries,
        "missing_script_module_count": report.missing_script_module_entries,
        "missing_migration_entry_count": report.missing_migration_entries,
        "migration_checksum_mismatch_count": report.migration_checksum_mismatches,
        "secret_pattern_entry_count": report.secret_pattern_entries,
        "release_package_error_count": report.errors,
    }
    return {
        key: len(values)
        for key, values in count_fields.items()
        if values
    }


def check_database_migration_plan(
    config: InstallValidationRunConfig,
) -> InstallValidationCheck:
    if not config.migration_plan_path:
        if release_acceptance_evidence_required(config):
            return failed_check(
                InstallValidationCheckName.DATABASE_MIGRATION,
                "database",
                "database migration validation requires --migration-plan",
                (
                    "run python -m taroai.db.migration_cli against the customer "
                    "database and provide an up-to-date migration plan before "
                    "accepting the install"
                ),
            )
        return skipped_check(
            InstallValidationCheckName.DATABASE_MIGRATION,
            "database",
            "database migration validation requires --migration-plan",
        )
    metadata: dict[str, str | int | float | bool] = {
        "plan_path": config.migration_plan_path,
    }
    try:
        plan = MigrationPlan.model_validate_json(
            Path(config.migration_plan_path).read_text()
        )
    except Exception:
        return failed_check(
            InstallValidationCheckName.DATABASE_MIGRATION,
            "database",
            "database migration plan could not be read or matched to the schema",
            "rerun python -m taroai.db.migration_cli and provide the generated plan JSON",
        )

    metadata.update(
        {
            "available_count": len(plan.available_versions),
            "applied_count": len(plan.applied_versions),
            "pending_count": len(plan.pending_versions),
            "unknown_applied_count": len(plan.unknown_applied_versions),
            "up_to_date": plan.up_to_date,
        }
    )
    if plan.up_to_date and not plan.pending_versions and not plan.unknown_applied_versions:
        return InstallValidationCheck(
            name=InstallValidationCheckName.DATABASE_MIGRATION,
            status="passed",
            dependency="database",
            message="database migration plan is up to date",
            metadata=metadata,
        )

    details = database_migration_plan_failure_details(plan)
    return InstallValidationCheck(
        name=InstallValidationCheckName.DATABASE_MIGRATION,
        status="failed",
        dependency="database",
        message=f"database migration plan is not ready: {details}",
        remediation=(
            "apply pending migrations with python -m taroai.db.migration_cli --apply; "
            "investigate unknown applied migrations before accepting the install"
        ),
        metadata=metadata,
    )


def database_migration_plan_failure_details(plan: MigrationPlan) -> str:
    details: list[str] = []
    if plan.pending_versions:
        details.append(f"pending migrations: {', '.join(plan.pending_versions)}")
    if plan.unknown_applied_versions:
        details.append(
            f"unknown applied migrations: {', '.join(plan.unknown_applied_versions)}"
        )
    if not details and not plan.up_to_date:
        details.append("plan did not report up_to_date=true")
    return "; ".join(details)


def check_object_storage_verification_result(
    config: InstallValidationRunConfig,
) -> InstallValidationCheck:
    if not config.object_storage_verification_path:
        if release_acceptance_evidence_required(config):
            return failed_check(
                InstallValidationCheckName.OBJECT_STORAGE_READ_WRITE,
                "object_storage",
                "object storage read/write validation requires --object-storage-verification",
                (
                    "run object storage verification against the customer object "
                    "storage endpoint and provide the generated JSON before "
                    "accepting the install"
                ),
            )
        return skipped_check(
            InstallValidationCheckName.OBJECT_STORAGE_READ_WRITE,
            "object_storage",
            "object storage read/write validation requires --object-storage-verification",
        )
    metadata: dict[str, str | int | float | bool] = {
        "result_path": config.object_storage_verification_path,
    }
    try:
        result = ObjectStorageVerificationResult.model_validate_json(
            Path(config.object_storage_verification_path).read_text()
        )
    except Exception:
        return failed_check(
            InstallValidationCheckName.OBJECT_STORAGE_READ_WRITE,
            "object_storage",
            (
                "object storage verification result could not be read or matched "
                "to the schema"
            ),
            "rerun python -m taroai.storage.object_storage_verification and provide the generated JSON",
        )

    metadata.update(
        {
            "bucket": result.bucket,
            "object_key": result.object_key,
            "uploaded_bytes": result.uploaded_bytes,
            "downloaded_bytes": result.downloaded_bytes,
            "deleted": result.deleted,
            "object_missing_after_delete": result.object_missing_after_delete,
        }
    )
    details = object_storage_verification_failure_details(result)
    if not details:
        return InstallValidationCheck(
            name=InstallValidationCheckName.OBJECT_STORAGE_READ_WRITE,
            status="passed",
            dependency="object_storage",
            message="object storage verification result confirms read/write/delete",
            metadata=metadata,
        )
    return InstallValidationCheck(
        name=InstallValidationCheckName.OBJECT_STORAGE_READ_WRITE,
        status="failed",
        dependency="object_storage",
        message=f"object storage verification result is not ready: {details}",
        remediation=(
            "rerun python -m taroai.storage.object_storage_verification with the "
            "customer object storage endpoint and fix bucket credentials or policy"
        ),
        metadata=metadata,
    )


def object_storage_verification_failure_details(
    result: ObjectStorageVerificationResult,
) -> str:
    details: list[str] = []
    if result.uploaded_bytes <= 0:
        details.append("uploaded bytes were empty")
    if result.downloaded_bytes != result.uploaded_bytes:
        details.append("downloaded bytes did not match uploaded bytes")
    if not result.deleted:
        details.append("object was not deleted")
    if not result.object_missing_after_delete:
        details.append("object remained visible after delete")
    return "; ".join(details)


def check_redis_queue_verification_result(
    config: InstallValidationRunConfig,
) -> tuple[InstallValidationCheck, InstallValidationCheck]:
    if not config.redis_queue_verification_path:
        if release_acceptance_evidence_required(config):
            return (
                failed_check(
                    InstallValidationCheckName.REDIS_CONNECTIVITY,
                    "redis",
                    "Redis connectivity validation requires --redis-queue-verification",
                    (
                        "run Redis queue verification against the customer Redis "
                        "endpoint and provide the generated JSON before "
                        "accepting the install"
                    ),
                ),
                failed_check(
                    InstallValidationCheckName.WORKER_QUEUE,
                    "worker",
                    "worker queue validation requires --redis-queue-verification",
                    (
                        "run worker queue verification against the customer Redis "
                        "queue and provide the generated JSON before accepting "
                        "the install"
                    ),
                ),
            )
        return (
            skipped_check(
                InstallValidationCheckName.REDIS_CONNECTIVITY,
                "redis",
                "Redis connectivity validation requires --redis-queue-verification",
            ),
            skipped_check(
                InstallValidationCheckName.WORKER_QUEUE,
                "worker",
                "worker queue validation requires --redis-queue-verification",
            ),
        )
    try:
        result = RedisQueueVerificationResult.model_validate_json(
            Path(config.redis_queue_verification_path).read_text()
        )
    except Exception:
        return (
            failed_check(
                InstallValidationCheckName.REDIS_CONNECTIVITY,
                "redis",
                "Redis queue verification result could not be read or matched to the schema",
                "rerun python -m taroai.workers.redis_verification and provide the generated JSON",
            ),
            failed_check(
                InstallValidationCheckName.WORKER_QUEUE,
                "worker",
                "Redis queue verification result could not be read or matched to the schema",
                "rerun python -m taroai.workers.redis_verification and provide the generated JSON",
            ),
        )

    redis_metadata = {
        "result_path": config.redis_queue_verification_path,
        "key_prefix": result.key_prefix,
        "ping_ok": result.ping_ok,
    }
    worker_metadata = {
        "result_path": config.redis_queue_verification_path,
        "acknowledged_job_status": result.acknowledged_job_status.value,
        "recovered_job_status": result.recovered_job_status.value,
        "recovered_job_attempts": result.recovered_job_attempts,
        "dead_letter_job_status": result.dead_letter_job_status.value,
        "dead_letter_count": result.dead_letter_count,
    }
    redis_details = redis_queue_connectivity_failure_details(result)
    worker_details = redis_queue_worker_failure_details(result)
    if redis_details:
        redis_check = InstallValidationCheck(
            name=InstallValidationCheckName.REDIS_CONNECTIVITY,
            status="failed",
            dependency="redis",
            message=f"Redis queue verification result is not ready: {redis_details}",
            remediation=(
                "rerun python -m taroai.workers.redis_verification and fix Redis URL, "
                "network policy, authentication, or database selection"
            ),
            metadata=redis_metadata,
        )
    else:
        redis_check = InstallValidationCheck(
            name=InstallValidationCheckName.REDIS_CONNECTIVITY,
            status="passed",
            dependency="redis",
            message="Redis queue verification result confirms connectivity",
            metadata=redis_metadata,
        )
    if worker_details:
        worker_check = InstallValidationCheck(
            name=InstallValidationCheckName.WORKER_QUEUE,
            status="failed",
            dependency="worker",
            message=f"Redis queue verification result is not ready: {worker_details}",
            remediation=(
                "rerun python -m taroai.workers.redis_verification and fix queue "
                "claim, ack, expired-lease recovery, or dead-letter behavior"
            ),
            metadata=worker_metadata,
        )
    else:
        worker_check = InstallValidationCheck(
            name=InstallValidationCheckName.WORKER_QUEUE,
            status="passed",
            dependency="worker",
            message="Redis queue verification result confirms worker queue lifecycle",
            metadata=worker_metadata,
        )
    return redis_check, worker_check


def redis_queue_connectivity_failure_details(
    result: RedisQueueVerificationResult,
) -> str:
    if not result.ping_ok:
        return "Redis ping failed"
    return ""


def redis_queue_worker_failure_details(
    result: RedisQueueVerificationResult,
) -> str:
    details: list[str] = []
    if result.acknowledged_job_status != JobStatus.SUCCEEDED:
        details.append("acknowledged job status was not succeeded")
    if result.recovered_job_status != JobStatus.RUNNING:
        details.append("recovered job status was not running")
    if result.recovered_job_attempts < 2:
        details.append("expired lease recovery did not increment attempts")
    if result.dead_letter_job_status != JobStatus.DEAD_LETTER:
        details.append("dead-letter job status was not dead_letter")
    if result.dead_letter_count < 1:
        details.append("dead-letter list was empty")
    return "; ".join(details)


def check_secret_manager_verification_result(
    config: InstallValidationRunConfig,
) -> InstallValidationCheck:
    if not config.secret_manager_verification_path:
        if release_acceptance_evidence_required(config):
            return failed_check(
                InstallValidationCheckName.SECRET_MANAGER_READ,
                "secrets_manager",
                "secret manager read validation requires --secret-manager-verification",
                (
                    "run the approved secret manager validation harness and "
                    "provide a redacted JSON result before accepting the install"
                ),
            )
        return skipped_check(
            InstallValidationCheckName.SECRET_MANAGER_READ,
            "secrets_manager",
            "secret manager read validation requires --secret-manager-verification",
        )
    metadata: dict[str, str | int | float | bool] = {
        "result_path": config.secret_manager_verification_path,
    }
    try:
        result = SecretManagerVerificationResult.model_validate_json(
            Path(config.secret_manager_verification_path).read_text()
        )
    except Exception:
        return failed_check(
            InstallValidationCheckName.SECRET_MANAGER_READ,
            "secrets_manager",
            "secret manager verification result could not be read or matched to the schema",
            (
                "rerun the approved secret manager validation harness and provide a "
                "redacted JSON result that contains no secret values"
            ),
        )

    metadata.update(
        {
            "backend": result.backend,
            "reference_checked": result.reference_checked,
            "lease_created": result.lease_created,
            "read_succeeded": result.read_succeeded,
            "scoped_context_enforced": result.scoped_context_enforced,
            "output_redacted": result.output_redacted,
            "secret_value_exposed": result.secret_value_exposed,
        }
    )
    details = secret_manager_verification_failure_details(result)
    if not details:
        return InstallValidationCheck(
            name=InstallValidationCheckName.SECRET_MANAGER_READ,
            status="passed",
            dependency="secrets_manager",
            message="secret manager verification result confirms scoped read",
            metadata=metadata,
        )
    return InstallValidationCheck(
        name=InstallValidationCheckName.SECRET_MANAGER_READ,
        status="failed",
        dependency="secrets_manager",
        message=f"secret manager verification result is not ready: {details}",
        remediation=(
            "fix secret manager credentials, lease scope, read policy, or output "
            "redaction before accepting the install"
        ),
        metadata=metadata,
    )


def secret_manager_verification_failure_details(
    result: SecretManagerVerificationResult,
) -> str:
    details: list[str] = []
    if not result.reference_checked:
        details.append("secret reference was not checked")
    if not result.lease_created:
        details.append("secret lease was not created")
    if not result.read_succeeded:
        details.append("secret read did not succeed")
    if not result.scoped_context_enforced:
        details.append("scoped context was not enforced")
    if not result.output_redacted:
        details.append("verification output was not redacted")
    if result.secret_value_exposed:
        details.append("verification output exposed a secret value")
    return "; ".join(details)


def check_event_stream_verification_result(
    config: InstallValidationRunConfig,
) -> InstallValidationCheck:
    if not config.event_stream_verification_path:
        if release_acceptance_evidence_required(config):
            return failed_check(
                InstallValidationCheckName.EVENT_STREAM,
                "api",
                "event stream validation requires --event-stream-verification",
                (
                    "run the authenticated event stream validation harness and "
                    "provide a redacted JSON result before accepting the install"
                ),
            )
        return skipped_check(
            InstallValidationCheckName.EVENT_STREAM,
            "api",
            "event stream validation requires --event-stream-verification",
        )
    metadata: dict[str, str | int | float | bool] = {
        "result_path": config.event_stream_verification_path,
    }
    try:
        result = EventStreamVerificationResult.model_validate_json(
            Path(config.event_stream_verification_path).read_text()
        )
    except Exception:
        return failed_check(
            InstallValidationCheckName.EVENT_STREAM,
            "api",
            "event stream verification result could not be read or matched to the schema",
            (
                "rerun the authenticated event stream validation harness and "
                "provide a redacted JSON result"
            ),
        )

    metadata.update(result.model_dump(exclude_none=True))
    redact_report_url_metadata(metadata, "api_base_url")
    details = event_stream_verification_failure_details(
        result,
        expected_api_base_url=config.api_base_url,
    )
    if not details:
        return InstallValidationCheck(
            name=InstallValidationCheckName.EVENT_STREAM,
            status="passed",
            dependency="api",
            message="event stream verification result confirms reconnectable run events",
            metadata=metadata,
        )
    return InstallValidationCheck(
        name=InstallValidationCheckName.EVENT_STREAM,
        status="failed",
        dependency="api",
        message=f"event stream verification result is not ready: {details}",
        remediation=(
            "fix authenticated run event streaming, replay cursors, tenant scope, "
            "or event payload redaction before accepting the install"
        ),
        metadata=metadata,
    )


def event_stream_verification_failure_details(
    result: EventStreamVerificationResult,
    expected_api_base_url: str | None = None,
) -> str:
    details: list[str] = []
    if not result.api_base_url:
        details.append("event stream api base URL was not recorded")
    elif expected_api_base_url and normalized_report_url(
        result.api_base_url
    ) != normalized_report_url(expected_api_base_url):
        details.append(
            "event stream api base URL did not match install validation API"
        )
    if not result.run_id:
        details.append("event stream run id was not recorded")
    if result.first_event_sequence is None:
        details.append("event stream first event sequence was not recorded")
    if not result.stream_opened:
        details.append("event stream did not open")
    if not result.event_id_received:
        details.append("event id was not received")
    if not result.after_sequence_replay_succeeded:
        details.append("after_sequence replay did not succeed")
    if not result.last_event_id_replay_succeeded:
        details.append("Last-Event-ID replay did not succeed")
    if not result.tenant_scope_enforced:
        details.append("event stream tenant scope was not enforced")
    if not result.safe_payload_confirmed:
        details.append("event stream payload safety was not confirmed")
    return "; ".join(details)


def check_audit_write_verification_result(
    config: InstallValidationRunConfig,
) -> InstallValidationCheck:
    if not config.audit_write_verification_path:
        if release_acceptance_evidence_required(config):
            return failed_check(
                InstallValidationCheckName.AUDIT_WRITE,
                "audit",
                "audit write validation requires --audit-write-verification",
                (
                    "run the authenticated audit validation harness and provide "
                    "a redacted JSON result before accepting the install"
                ),
            )
        return skipped_check(
            InstallValidationCheckName.AUDIT_WRITE,
            "audit",
            "audit write validation requires --audit-write-verification",
        )
    metadata: dict[str, str | int | float | bool] = {
        "result_path": config.audit_write_verification_path,
    }
    try:
        result = AuditWriteVerificationResult.model_validate_json(
            Path(config.audit_write_verification_path).read_text()
        )
    except Exception:
        return failed_check(
            InstallValidationCheckName.AUDIT_WRITE,
            "audit",
            "audit write verification result could not be read or matched to the schema",
            (
                "rerun the authenticated audit validation harness and provide a "
                "redacted JSON result"
            ),
        )

    metadata.update(result.model_dump(exclude_none=True))
    redact_report_url_metadata(metadata, "api_base_url")
    details = join_failure_details(
        audit_write_verification_failure_details(
            result,
            expected_api_base_url=config.api_base_url,
        ),
        audit_write_event_stream_consistency_failure_details(config, result),
    )
    if not details:
        return InstallValidationCheck(
            name=InstallValidationCheckName.AUDIT_WRITE,
            status="passed",
            dependency="audit",
            message="audit write verification result confirms write and read-back",
            metadata=metadata,
        )
    return InstallValidationCheck(
        name=InstallValidationCheckName.AUDIT_WRITE,
        status="failed",
        dependency="audit",
        message=f"audit write verification result is not ready: {details}",
        remediation=(
            "fix authenticated audit write/read permissions, tenant scope, or "
            "metadata redaction before accepting the install"
        ),
        metadata=metadata,
    )


def audit_write_verification_failure_details(
    result: AuditWriteVerificationResult,
    expected_api_base_url: str | None = None,
) -> str:
    details: list[str] = []
    if not result.api_base_url:
        details.append("audit api base URL was not recorded")
    elif expected_api_base_url and normalized_report_url(
        result.api_base_url
    ) != normalized_report_url(expected_api_base_url):
        details.append("audit api base URL did not match install validation API")
    if not result.run_id:
        details.append("audit run id was not recorded")
    if not result.write_succeeded:
        details.append("audit write did not succeed")
    if not result.read_back_succeeded:
        details.append("audit read-back did not succeed")
    if not result.tenant_scope_enforced:
        details.append("audit tenant scope was not enforced")
    if not result.sensitive_metadata_redacted:
        details.append("audit sensitive metadata was not redacted")
    return "; ".join(details)


def audit_write_event_stream_consistency_failure_details(
    config: InstallValidationRunConfig,
    result: AuditWriteVerificationResult,
) -> str:
    if not config.event_stream_verification_path:
        return ""
    try:
        event_result = EventStreamVerificationResult.model_validate_json(
            Path(config.event_stream_verification_path).read_text()
        )
    except Exception:
        return ""
    if not result.run_id or not event_result.run_id:
        return ""
    if result.run_id == event_result.run_id:
        return ""
    return "audit run id did not match event stream verification run id"


def check_trace_collector_verification_result(
    config: InstallValidationRunConfig,
) -> InstallValidationCheck:
    if not config.trace_collector_verification_path:
        if release_acceptance_evidence_required(config):
            return failed_check(
                InstallValidationCheckName.TRACE_COLLECTOR,
                "observability",
                "trace collector validation requires --trace-collector-verification",
                (
                    "run trace collector verification against the customer "
                    "collector endpoint and provide a redacted JSON result "
                    "before accepting the install"
                ),
            )
        return skipped_check(
            InstallValidationCheckName.TRACE_COLLECTOR,
            "observability",
            "trace collector validation requires --trace-collector-verification",
        )
    metadata: dict[str, str | int | float | bool] = {
        "result_path": config.trace_collector_verification_path,
    }
    try:
        result = TraceCollectorVerificationResult.model_validate_json(
            Path(config.trace_collector_verification_path).read_text()
        )
    except Exception:
        return failed_check(
            InstallValidationCheckName.TRACE_COLLECTOR,
            "observability",
            "trace collector verification result could not be read or matched to the schema",
            (
                "rerun python -m taroai.observability.verification and provide "
                "a redacted JSON result"
            ),
        )

    metadata.update(result.model_dump(exclude_none=True))
    redact_report_url_metadata(metadata, "endpoint_url")
    details = trace_collector_verification_failure_details(result)
    if not details:
        return InstallValidationCheck(
            name=InstallValidationCheckName.TRACE_COLLECTOR,
            status="passed",
            dependency="observability",
            message="trace collector verification result confirms OTLP HTTP export",
            metadata=metadata,
        )
    return InstallValidationCheck(
        name=InstallValidationCheckName.TRACE_COLLECTOR,
        status="failed",
        dependency="observability",
        message=f"trace collector verification result is not ready: {details}",
        remediation=(
            "fix OTLP HTTP collector reachability, credentials, service metadata, "
            "or redaction before accepting the install"
        ),
        metadata=metadata,
    )


def trace_collector_verification_failure_details(
    result: TraceCollectorVerificationResult,
) -> str:
    details: list[str] = []
    if result.span_count < 1:
        details.append("trace collector did not receive a span")
    if result.resource_span_count < 1:
        details.append("trace collector did not receive resource spans")
    if result.scope_span_count < 1:
        details.append("trace collector did not receive scope spans")
    if result.secret_value_exposed:
        details.append("verification output exposed a secret value")
    return "; ".join(details)


def check_support_bundle_redaction_evidence_result(
    config: InstallValidationRunConfig,
) -> InstallValidationCheck:
    if not config.support_bundle_redaction_evidence_path:
        if customer_operated_evidence_required(config):
            return failed_check(
                InstallValidationCheckName.SUPPORT_BUNDLE_REDACTION,
                "support_bundle",
                (
                    "support bundle redaction validation requires "
                    "--support-bundle-redaction-evidence"
                ),
                (
                    "run support bundle redaction inside the customer boundary "
                    "and provide the redaction evidence JSON before accepting "
                    "the install"
                ),
            )
        return skipped_check(
            InstallValidationCheckName.SUPPORT_BUNDLE_REDACTION,
            "support_bundle",
            (
                "support bundle redaction validation requires "
                "--support-bundle-redaction-evidence"
            ),
        )
    metadata: dict[str, str | int | float | bool] = {
        "result_path": config.support_bundle_redaction_evidence_path,
    }
    try:
        result = SupportBundleRedactionReport.model_validate_json(
            Path(config.support_bundle_redaction_evidence_path).read_text()
        )
    except Exception:
        return InstallValidationCheck(
            name=InstallValidationCheckName.SUPPORT_BUNDLE_REDACTION,
            status="failed",
            dependency="support_bundle",
            message="support bundle redaction evidence could not be read or matched to the schema",
            remediation=(
                "rerun scripts/redact-support-bundle.sh inside the customer "
                "boundary and provide the redaction evidence JSON"
            ),
            metadata=metadata,
        )

    metadata.update(support_bundle_redaction_metadata(result))
    details = support_bundle_redaction_failure_details(result)
    if not details:
        return InstallValidationCheck(
            name=InstallValidationCheckName.SUPPORT_BUNDLE_REDACTION,
            status="passed",
            dependency="support_bundle",
            message="support bundle redaction evidence confirms sanitized bundle output",
            metadata=metadata,
        )
    return InstallValidationCheck(
        name=InstallValidationCheckName.SUPPORT_BUNDLE_REDACTION,
        status="failed",
        dependency="support_bundle",
        message=f"support bundle redaction evidence is not ready: {details}",
        remediation=(
            "rerun support bundle redaction and confirm the sanitized archive plus "
            "evidence report are generated before customer handoff"
        ),
        metadata=metadata,
    )


def support_bundle_redaction_metadata(
    result: SupportBundleRedactionReport,
) -> dict[str, str | int | float | bool]:
    metadata: dict[str, str | int | float | bool] = {
        "file_count": result.file_count,
        "text_entry_count": result.text_entry_count,
        "binary_entry_count": result.binary_entry_count,
        "redacted_entry_count": result.redacted_entry_count,
        "finding_count": sum(result.finding_count_by_category.values()),
    }
    for category, count in sorted(result.finding_count_by_category.items()):
        metadata[f"redaction_{category}_count"] = count
    return metadata


def support_bundle_redaction_failure_details(
    result: SupportBundleRedactionReport,
) -> str:
    details: list[str] = []
    if not result.valid:
        details.append("redaction report is not valid")
    if result.errors:
        details.append("redaction report contains errors")
    return "; ".join(details)


def check_restore_drill_verification_result(
    config: InstallValidationRunConfig,
) -> InstallValidationCheck:
    if not config.restore_drill_verification_path:
        if release_acceptance_evidence_required(config):
            return failed_check(
                InstallValidationCheckName.BACKUP_RESTORE_DRILL,
                "backup_restore",
                "backup restore drill validation requires --restore-drill-verification",
                (
                    "run the customer-approved restore drill validation harness "
                    "and provide the redacted restore drill result before "
                    "accepting the install"
                ),
            )
        return skipped_check(
            InstallValidationCheckName.BACKUP_RESTORE_DRILL,
            "backup_restore",
            "backup restore drill validation requires --restore-drill-verification",
        )
    metadata: dict[str, str | int | float | bool] = {
        "result_path": config.restore_drill_verification_path,
    }
    try:
        result = RestoreDrillVerificationResult.model_validate_json(
            Path(config.restore_drill_verification_path).read_text()
        )
    except Exception:
        return failed_check(
            InstallValidationCheckName.BACKUP_RESTORE_DRILL,
            "backup_restore",
            "restore drill verification result could not be read or matched to the schema",
            (
                "rerun the approved restore drill validation harness and provide "
                "a redacted JSON result"
            ),
        )

    metadata.update(result.model_dump(exclude_none=True))
    details = restore_drill_verification_failure_details(result)
    if not details:
        return InstallValidationCheck(
            name=InstallValidationCheckName.BACKUP_RESTORE_DRILL,
            status="passed",
            dependency="backup_restore",
            message="restore drill verification result confirms backup recovery path",
            metadata=metadata,
        )
    return InstallValidationCheck(
        name=InstallValidationCheckName.BACKUP_RESTORE_DRILL,
        status="failed",
        dependency="backup_restore",
        message=f"restore drill verification result is not ready: {details}",
        remediation=(
            "fix backup capture, restore ordering, restored dependency validation, "
            "or post-restore checks before accepting the install"
        ),
        metadata=metadata,
    )


def restore_drill_verification_failure_details(
    result: RestoreDrillVerificationResult,
) -> str:
    details: list[str] = []
    if not result.backup_manifest_generated:
        details.append("backup manifest was not generated")
    if not result.restore_order_executed:
        details.append("restore order was not executed")
    if not result.database_restore_verified:
        details.append("database restore was not verified")
    if not result.object_storage_restore_verified:
        details.append("object storage restore was not verified")
    if not result.redis_restore_or_rebuild_verified:
        details.append("Redis restore or rebuild was not verified")
    if not result.config_restore_verified:
        details.append("configuration restore was not verified")
    if not result.post_restore_validation_passed:
        details.append("post-restore validation did not pass")
    return "; ".join(details)


def check_api_health(
    config: InstallValidationRunConfig,
    client: InstallValidationHttpClient,
) -> tuple[InstallValidationCheck, dict[str, Any]]:
    try:
        health = client.get(join_url(config.api_base_url, "/healthz"))
        readiness = client.get(join_url(config.api_base_url, "/readyz"))
        readiness_body = readiness.json_body() if status_ok(readiness.status_code) else {}
    except Exception:
        return (
            failed_check(
                InstallValidationCheckName.API_HEALTH,
                "api",
                "API health/readiness request failed",
                "check API service DNS, ingress, network policy, and service logs",
            ),
            {},
        )

    if not status_ok(health.status_code) or not status_ok(readiness.status_code):
        return (
            failed_check(
                InstallValidationCheckName.API_HEALTH,
                "api",
                f"API health={health.status_code} readiness={readiness.status_code}",
                "check API deployment health and readiness dependencies",
            ),
            readiness_body,
        )
    return (
        passed_check(
            InstallValidationCheckName.API_HEALTH,
            "api",
            "API /healthz and /readyz returned successful responses",
        ),
        readiness_body,
    )


def check_model_gateway_readiness(
    config: InstallValidationRunConfig,
    readiness_body: dict[str, Any],
) -> InstallValidationCheck:
    check = readiness_body.get("checks", {}).get("model_gateway", {})
    if check.get("configured") is not True:
        missing = check.get("missing") or []
        missing_text = ", ".join(str(item) for item in missing) or "readiness not configured"
        return failed_check(
            InstallValidationCheckName.MODEL_GATEWAY_HEALTH,
            "model_gateway",
            f"model_gateway readiness missing: {missing_text}",
            "configure TAROAI_MODEL_GATEWAY_MODEL and a usable provider credential",
        )
    if not config.model_gateway_verification_path:
        if model_gateway_evidence_required(config, check):
            return failed_check(
                InstallValidationCheckName.MODEL_GATEWAY_HEALTH,
                "model_gateway",
                (
                    "OpenAI-compatible Model Gateway live validation requires "
                    "--model-gateway-verification"
                ),
                (
                    "run scripts/verify-model-gateway.sh against the installed "
                    "OpenAI-compatible provider and provide the redacted JSON "
                    "result before accepting the install"
                ),
            )
        return skipped_check(
            InstallValidationCheckName.MODEL_GATEWAY_HEALTH,
            "model_gateway",
            "OpenAI-compatible Model Gateway live validation requires --model-gateway-verification",
        )
    metadata: dict[str, str | int | float | bool] = {
        "result_path": config.model_gateway_verification_path,
    }
    try:
        result = OpenAICompatibleModelGatewayVerificationResult.model_validate_json(
            Path(config.model_gateway_verification_path).read_text()
        )
    except Exception:
        return failed_check(
            InstallValidationCheckName.MODEL_GATEWAY_HEALTH,
            "model_gateway",
            "model gateway verification result could not be read or matched to the schema",
            (
                "rerun scripts/verify-model-gateway.sh or "
                "python -m taroai.model_gateway.verification and provide "
                "a redacted JSON result"
            ),
        )

    metadata.update(model_gateway_verification_metadata(result))
    details = join_failure_details(
        model_gateway_readiness_evidence_details(check, result),
        model_gateway_verification_failure_details(result),
    )
    if not details:
        return InstallValidationCheck(
            name=InstallValidationCheckName.MODEL_GATEWAY_HEALTH,
            status="passed",
            dependency="model_gateway",
            message="model gateway verification result confirms OpenAI-compatible planning",
            metadata=metadata,
        )
    return InstallValidationCheck(
        name=InstallValidationCheckName.MODEL_GATEWAY_HEALTH,
        status="failed",
        dependency="model_gateway",
        message=f"model gateway verification result is not ready: {details}",
        remediation=(
            "fix model gateway provider credentials, endpoint, model, response "
            "format, or policy before accepting the install"
        ),
        metadata=metadata,
    )


def model_gateway_evidence_required(
    config: InstallValidationRunConfig,
    readiness: Any,
) -> bool:
    mode = config.deployment_mode.strip().lower()
    if mode not in RUNTIME_EXECUTION_EVIDENCE_MODES:
        return False
    if not isinstance(readiness, dict):
        return False
    return readiness.get("configured") is True


def model_gateway_verification_metadata(
    result: OpenAICompatibleModelGatewayVerificationResult,
) -> dict[str, str | int | float | bool]:
    metadata: dict[str, str | int | float | bool] = {
        "verified": result.verified,
        "base_url": redacted_url_for_report(result.base_url),
        "model": result.model,
        "response_id": result.response_id,
        "planned_step_count": result.planned_step_count,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "total_tokens": result.total_tokens,
    }
    if result.provider_id is not None:
        metadata["provider_id"] = result.provider_id
    return metadata


def model_gateway_readiness_evidence_details(
    readiness: Any,
    result: OpenAICompatibleModelGatewayVerificationResult,
) -> str:
    if not isinstance(readiness, dict):
        return ""
    details: list[str] = []
    gateway_type = str(readiness.get("gateway_type") or "").strip()
    if gateway_type == "openai_compatible":
        expected_base_url = readiness_string(readiness.get("base_url"))
        if expected_base_url and normalized_report_url(
            result.base_url
        ) != normalized_report_url(expected_base_url):
            details.append(
                "model gateway verification base_url did not match API readiness"
            )
        expected_model = readiness_string(readiness.get("model"))
        if expected_model and result.model.strip() != expected_model:
            details.append("model gateway verification model did not match API readiness")
    if gateway_type == "provider_registry":
        configured_provider_ids = readiness_string_list(
            readiness.get("configured_provider_ids")
        ) or readiness_string_list(readiness.get("provider_ids"))
        if configured_provider_ids and result.provider_id not in configured_provider_ids:
            details.append(
                "model gateway verification provider_id did not match API readiness"
            )
    return "; ".join(details)


def readiness_string(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


def readiness_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        item.strip()
        for item in value
        if isinstance(item, str) and item.strip()
    ]


def normalized_report_url(value: str) -> str:
    return redacted_url_for_report(value).rstrip("/")


def model_gateway_verification_failure_details(
    result: OpenAICompatibleModelGatewayVerificationResult,
) -> str:
    details: list[str] = []
    if not result.verified:
        details.append("model gateway verification did not pass")
    if not result.response_id.strip():
        details.append("model gateway response id was empty")
    if result.planned_step_count <= 0:
        details.append("model gateway returned no planned steps")
    if not result.planned_tool_names:
        details.append("model gateway returned no planned tool names")
    elif MODEL_GATEWAY_EXPECTED_PLANNING_TOOL not in result.planned_tool_names:
        details.append(
            "model gateway did not return expected planning tool "
            f"{MODEL_GATEWAY_EXPECTED_PLANNING_TOOL}"
        )
    return "; ".join(details)


def check_sandbox_readiness(
    config: InstallValidationRunConfig,
    readiness_body: dict[str, Any],
) -> InstallValidationCheck:
    readiness = readiness_body.get("checks", {}).get("sandbox", {})
    if readiness.get("configured") is not True:
        missing = readiness.get("missing") or []
        missing_text = (
            ", ".join(str(item) for item in missing)
            or "readiness not configured"
        )
        return failed_check(
            InstallValidationCheckName.SANDBOX_HEALTH,
            "sandbox_provider",
            f"sandbox_provider readiness missing: {missing_text}",
            (
                "configure an enterprise sandbox provider, "
                "TAROAI_SANDBOX_CONTROLLER_BASE_URL, and "
                "TAROAI_SANDBOX_CONTROLLER_API_KEY"
            ),
        )
    if (
        readiness.get("controller_required") is True
        and readiness.get("capabilities_checked") is not True
    ):
        return failed_check(
            InstallValidationCheckName.SANDBOX_HEALTH,
            "sandbox_provider",
            "sandbox controller readiness missing: sandbox_controller_capabilities",
            (
                "confirm the API can authenticate to the sandbox controller and "
                "read its /capabilities response before accepting the install"
            ),
        )
    readiness_capability_details = sandbox_readiness_capability_failure_details(
        readiness
    )
    if readiness_capability_details:
        return failed_check(
            InstallValidationCheckName.SANDBOX_HEALTH,
            "sandbox_provider",
            f"sandbox controller readiness insufficient: {readiness_capability_details}",
            (
                "fix the sandbox controller /capabilities response so API "
                "readiness declares runtime isolation, image policy, TTL, "
                "destroy support, resource controls, and capacity limits"
            ),
        )
    default_key_check = default_operator_token_check(
        config=config,
        name=InstallValidationCheckName.SANDBOX_HEALTH,
        dependency="sandbox_provider",
        field_name="sandbox_controller_api_key",
        env_var_name="TAROAI_SANDBOX_CONTROLLER_API_KEY",
        value=config.sandbox_controller_api_key,
    )
    if default_key_check is not None:
        return default_key_check
    if not config.sandbox_verification_path:
        if sandbox_lifecycle_evidence_required(config, readiness):
            return failed_check(
                InstallValidationCheckName.SANDBOX_HEALTH,
                "sandbox_provider",
                "sandbox lifecycle validation requires --sandbox-verification",
                (
                    "run the customer sandbox lifecycle validation harness "
                    "against the installed provider and provide the redacted "
                    "JSON result before accepting the install"
                ),
            )
        return skipped_check(
            InstallValidationCheckName.SANDBOX_HEALTH,
            "sandbox_provider",
            "sandbox lifecycle validation requires --sandbox-verification",
        )
    metadata: dict[str, str | int | float | bool] = {
        "result_path": config.sandbox_verification_path,
    }
    try:
        result = SandboxLifecycleVerificationResult.model_validate_json(
            Path(config.sandbox_verification_path).read_text()
        )
    except Exception:
        return failed_check(
            InstallValidationCheckName.SANDBOX_HEALTH,
            "sandbox_provider",
            "sandbox lifecycle verification result could not be read or matched to the schema",
            (
                "rerun the customer sandbox lifecycle validation harness and "
                "provide a redacted JSON result"
            ),
        )

    metadata.update(result.model_dump(exclude_none=True))
    details = join_failure_details(
        sandbox_enterprise_provider_evidence_details(config, readiness, result),
        sandbox_lifecycle_verification_failure_details(
            result,
            auth_challenge_required=bool(config.sandbox_controller_api_key.strip()),
        ),
    )
    provider = sandbox_provider_for_validation(readiness, result)
    if provider in {"k8s", "kubernetes"}:
        details = join_failure_details(
            details,
            kubernetes_sandbox_provider_evidence_details(config, metadata),
        )
    if not details:
        return InstallValidationCheck(
            name=InstallValidationCheckName.SANDBOX_HEALTH,
            status="passed",
            dependency="sandbox_provider",
            message=(
                "sandbox lifecycle verification result confirms session create, "
                "command, destroy, and controller isolation capabilities"
            ),
            metadata=metadata,
        )
    return InstallValidationCheck(
        name=InstallValidationCheckName.SANDBOX_HEALTH,
        status="failed",
        dependency="sandbox_provider",
        message=f"sandbox lifecycle verification result is not ready: {details}",
        remediation=(
            "fix sandbox controller lifecycle, command execution, session cleanup, "
            "declared isolation capabilities, or redacted verification output "
            "before accepting the install"
        ),
        metadata=metadata,
    )


def sandbox_provider_for_validation(
    readiness: dict[str, Any],
    result: SandboxLifecycleVerificationResult,
) -> str:
    provider = readiness.get("provider")
    if isinstance(provider, str) and provider.strip():
        return provider.strip().lower()
    return result.provider.strip().lower()


def sandbox_readiness_capability_failure_details(readiness: Any) -> str:
    if not isinstance(readiness, dict):
        return ""
    if readiness.get("controller_required") is not True:
        return ""
    if readiness.get("capabilities_checked") is not True:
        return ""
    details: list[str] = []
    for field_name, label in SANDBOX_READINESS_REQUIRED_CAPABILITY_FLAGS.items():
        if readiness.get(field_name) is not True:
            details.append(label)
    allowed_image_count = readiness.get("allowed_image_count")
    if not isinstance(allowed_image_count, int) or allowed_image_count <= 0:
        details.append("sandbox_allowed_image_count")
    for field_name, label in SANDBOX_READINESS_REQUIRED_CAPACITY_FIELDS.items():
        value = readiness.get(field_name)
        if not isinstance(value, int) or value <= 0:
            details.append(label)
    return ", ".join(details)


def browser_readiness_capability_failure_details(readiness: Any) -> str:
    if not isinstance(readiness, dict):
        return ""
    if readiness.get("controller_required") is not True:
        return ""
    if readiness.get("capabilities_checked") is not True:
        return ""
    details: list[str] = []
    for field_name, label in BROWSER_READINESS_REQUIRED_CAPABILITY_FLAGS.items():
        if readiness.get(field_name) is not True:
            details.append(label)
    for field_name, label in BROWSER_READINESS_REQUIRED_CAPACITY_FIELDS.items():
        value = readiness.get(field_name)
        if not isinstance(value, int) or value <= 0:
            details.append(label)
    return ", ".join(details)


def sandbox_lifecycle_evidence_required(
    config: InstallValidationRunConfig,
    readiness: Any,
) -> bool:
    mode = config.deployment_mode.strip().lower()
    if mode not in RUNTIME_EXECUTION_EVIDENCE_MODES:
        return False
    if not isinstance(readiness, dict):
        return False
    return readiness.get("configured") is True


def sandbox_enterprise_provider_evidence_details(
    config: InstallValidationRunConfig,
    readiness: dict[str, Any],
    result: SandboxLifecycleVerificationResult,
) -> str:
    mode = config.deployment_mode.strip().lower()
    if mode not in CUSTOMER_OPERATED_INSTALL_VALIDATION_MODES:
        return ""
    result_provider = canonical_sandbox_provider(result.provider)
    providers = [result_provider]
    readiness_provider = readiness.get("provider")
    if isinstance(readiness_provider, str) and readiness_provider.strip():
        ready_provider = canonical_sandbox_provider(readiness_provider)
        providers.append(ready_provider)
        if ready_provider != result_provider:
            return (
                "sandbox verification provider did not match API readiness provider: "
                f"readiness={ready_provider}, evidence={result_provider}"
            )
    if mode == "air_gapped" and "e2b" in providers:
        return (
            "air_gapped deployments require sandbox verification evidence from k8s "
            "or kubernetes; received e2b"
        )
    invalid_providers = sorted(
        {provider for provider in providers if provider not in ENTERPRISE_SANDBOX_EVIDENCE_PROVIDERS}
    )
    if not invalid_providers:
        return ""
    received = ", ".join(invalid_providers)
    return (
        f"{mode} deployments require sandbox verification evidence from k8s or e2b; "
        f"received {received}"
    )


def canonical_sandbox_provider(provider: str) -> str:
    normalized = provider.strip().lower()
    if normalized == "k8s":
        return "kubernetes"
    return normalized


def join_failure_details(*details: str) -> str:
    return "; ".join(detail for detail in details if detail)


def customer_operated_evidence_required(config: InstallValidationRunConfig) -> bool:
    return (
        config.deployment_mode.strip().lower()
        in CUSTOMER_OPERATED_INSTALL_VALIDATION_MODES
    )


def release_acceptance_evidence_required(config: InstallValidationRunConfig) -> bool:
    return (
        config.deployment_mode.strip().lower()
        in RELEASE_ACCEPTANCE_EVIDENCE_MODES
    )


def runtime_closed_loop_evidence_required(config: InstallValidationRunConfig) -> bool:
    return release_acceptance_evidence_required(config)


def runtime_closed_loop_required_gates(
    config: InstallValidationRunConfig,
) -> list[str]:
    gates = [
        "demo_ready",
        "workspace_execution_ready",
        "skill_reuse_ready",
        "browser_controller_governance_ready",
    ]
    if customer_operated_evidence_required(config):
        gates.append("sandbox_governance_ready")
    return gates


def runtime_closed_loop_metadata(
    report: DemoReadinessGateReport,
) -> dict[str, str | int | float | bool]:
    return {
        "demo_ready": report.demo_ready,
        "local_smoke_ready": report.local_smoke_ready,
        "strict_model_ready": report.strict_model_ready,
        "workspace_execution_ready": report.workspace_execution_ready,
        "skill_reuse_ready": report.skill_reuse_ready,
        "browser_controller_governance_ready": (
            report.browser_controller_governance_ready
        ),
        "sandbox_governance_ready": report.sandbox_governance_ready,
        "sandbox_runtime_isolation_declared": (
            report.sandbox_runtime_isolation_declared
        ),
        "sandbox_image_policy_enforced_declared": (
            report.sandbox_image_policy_enforced_declared
        ),
        "sandbox_allowed_image_count": report.sandbox_allowed_image_count,
        "required_gates": ", ".join(report.required_gates),
        "failed_required_gates": ", ".join(report.failed_required_gates),
        "error_count": len(report.errors),
    }


def runtime_closed_loop_gate_passed(
    report: DemoReadinessGateReport,
    gate: str,
) -> bool:
    if report.gate_results.get(gate) is not True:
        return False
    if gate == "demo_ready":
        return report.demo_ready
    if gate == "workspace_execution_ready":
        return report.workspace_execution_ready
    if gate == "skill_reuse_ready":
        return report.skill_reuse_ready
    if gate == "browser_controller_governance_ready":
        return report.browser_controller_governance_ready
    if gate == "sandbox_governance_ready":
        return report.sandbox_governance_ready
    return False


def runtime_closed_loop_supporting_failure_details(
    report: DemoReadinessGateReport,
    required_gates: list[str],
) -> list[str]:
    details: list[str] = []
    if "demo_ready" in required_gates:
        if not report.local_smoke_ready:
            details.append("local_smoke_ready=false")
        if not report.strict_model_ready:
            details.append("strict_model_ready=false")
    if "sandbox_governance_ready" in required_gates:
        if not report.sandbox_runtime_isolation_declared:
            details.append("sandbox_runtime_isolation_declared=false")
        if not report.sandbox_image_policy_enforced_declared:
            details.append("sandbox_image_policy_enforced_declared=false")
        if report.sandbox_allowed_image_count <= 0:
            details.append("sandbox_allowed_image_count=0")
    return details


def runtime_closed_loop_referenced_result_path(
    report: DemoReadinessGateReport,
    evidence_path: str,
) -> Path:
    result_path = Path(report.result_path)
    if result_path.is_absolute():
        return result_path
    cwd_result_path = result_path.resolve(strict=False)
    if cwd_result_path.is_file():
        return cwd_result_path
    return Path(evidence_path).resolve(strict=False).parent / result_path


def runtime_closed_loop_source_result_failure_details(
    report: DemoReadinessGateReport,
    evidence_path: str,
    config: InstallValidationRunConfig,
) -> list[str]:
    source_path = runtime_closed_loop_referenced_result_path(report, evidence_path)
    if not source_path.is_file():
        return ["referenced local cloud PoC result is missing"]
    source_result: LocalCloudPocVerificationResult | None = None
    try:
        source_result = LocalCloudPocVerificationResult.model_validate_json(
            source_path.read_text()
        )
    except Exception:
        source_result = None
    details: list[str] = []
    if source_result is not None:
        details.extend(
            runtime_closed_loop_source_context_failure_details(source_result, config)
        )
    regenerated_report = run_demo_readiness_gate(
        DemoReadinessGateConfig(
            result_path=source_path,
            require_workspace_execution=(
                "workspace_execution_ready" in report.required_gates
            ),
            require_skill_reuse="skill_reuse_ready" in report.required_gates,
            require_browser_controller_governance=(
                "browser_controller_governance_ready" in report.required_gates
            ),
            require_sandbox_governance=(
                "sandbox_governance_ready" in report.required_gates
            ),
        )
    )
    if not runtime_closed_loop_reports_match_source(report, regenerated_report):
        details.append("runtime closed-loop evidence does not match source result")
    return details


def runtime_closed_loop_source_context_failure_details(
    source_result: LocalCloudPocVerificationResult,
    config: InstallValidationRunConfig,
) -> list[str]:
    details: list[str] = []
    if not source_result.run_id:
        details.append("runtime closed-loop source run id was not recorded")
    if not source_result.api_base_url:
        details.append("runtime closed-loop source API base URL was not recorded")
    elif normalized_report_url(source_result.api_base_url) != normalized_report_url(
        config.api_base_url
    ):
        details.append(
            "runtime closed-loop source API base URL did not match install validation API"
        )
    if not source_result.browser_base_url:
        details.append("runtime closed-loop source browser base URL was not recorded")
    elif normalized_report_url(source_result.browser_base_url) != normalized_report_url(
        config.browser_base_url
    ):
        details.append(
            "runtime closed-loop source browser base URL did not match install validation browser"
        )
    if config.web_base_url is not None:
        if not source_result.web_base_url:
            details.append("runtime closed-loop source web base URL was not recorded")
        elif normalized_report_url(source_result.web_base_url) != normalized_report_url(
            config.web_base_url
        ):
            details.append(
                "runtime closed-loop source web base URL did not match install validation web"
            )
    details.extend(
        runtime_closed_loop_source_event_failure_details(source_result, config)
    )
    details.extend(
        runtime_closed_loop_source_audit_failure_details(source_result, config)
    )
    return details


def runtime_closed_loop_source_event_failure_details(
    source_result: LocalCloudPocVerificationResult,
    config: InstallValidationRunConfig,
) -> list[str]:
    if not config.event_stream_verification_path:
        return []
    try:
        event_result = EventStreamVerificationResult.model_validate_json(
            Path(config.event_stream_verification_path).read_text()
        )
    except Exception:
        return []
    if not source_result.run_id or not event_result.run_id:
        return []
    if source_result.run_id == event_result.run_id:
        return []
    return [
        "runtime closed-loop source run id did not match event stream verification run id"
    ]


def runtime_closed_loop_source_audit_failure_details(
    source_result: LocalCloudPocVerificationResult,
    config: InstallValidationRunConfig,
) -> list[str]:
    if not config.audit_write_verification_path:
        return []
    try:
        audit_result = AuditWriteVerificationResult.model_validate_json(
            Path(config.audit_write_verification_path).read_text()
        )
    except Exception:
        return []
    if not source_result.run_id or not audit_result.run_id:
        return []
    if source_result.run_id == audit_result.run_id:
        return []
    return [
        "runtime closed-loop source run id did not match audit verification run id"
    ]


def runtime_closed_loop_reports_match_source(
    report: DemoReadinessGateReport,
    source_report: DemoReadinessGateReport,
) -> bool:
    fields = [
        "status",
        "demo_ready",
        "local_smoke_ready",
        "strict_model_ready",
        "workspace_execution_ready",
        "skill_reuse_ready",
        "browser_controller_governance_ready",
        "sandbox_governance_ready",
        "sandbox_runtime_isolation_declared",
        "sandbox_image_policy_enforced_declared",
        "sandbox_allowed_image_count",
        "required_gates",
        "failed_required_gates",
        "gate_results",
        "errors",
    ]
    return all(
        getattr(report, field) == getattr(source_report, field)
        for field in fields
    )


def check_runtime_closed_loop_evidence_result(
    config: InstallValidationRunConfig,
) -> InstallValidationCheck:
    metadata: dict[str, str | int | float | bool] = {}
    if not config.runtime_closed_loop_evidence_path:
        if runtime_closed_loop_evidence_required(config):
            return failed_check(
                InstallValidationCheckName.RUNTIME_CLOSED_LOOP,
                "runtime_closed_loop",
                (
                    "runtime closed-loop validation requires "
                    "--runtime-closed-loop-evidence"
                ),
                (
                    "run scripts/verify-compose-strict-e2e.sh or "
                    "scripts/verify-local-cloud-demo-ready.sh with workspace and "
                    "browser-governance gates, then provide its report JSON"
                ),
            )
        return skipped_check(
            InstallValidationCheckName.RUNTIME_CLOSED_LOOP,
            "runtime_closed_loop",
            "runtime closed-loop validation requires --runtime-closed-loop-evidence",
        )

    metadata["evidence_path"] = config.runtime_closed_loop_evidence_path
    try:
        report = DemoReadinessGateReport.model_validate_json(
            Path(config.runtime_closed_loop_evidence_path).read_text()
        )
    except Exception:
        return InstallValidationCheck(
            name=InstallValidationCheckName.RUNTIME_CLOSED_LOOP,
            status="failed",
            dependency="runtime_closed_loop",
            message=(
                "runtime closed-loop evidence could not be read or did not match "
                "schema"
            ),
            remediation=(
                "rerun scripts/verify-local-cloud-demo-ready.sh and provide the "
                "generated demo gate report JSON"
            ),
            metadata=metadata,
        )

    metadata.update(runtime_closed_loop_metadata(report))
    source_result_path = runtime_closed_loop_referenced_result_path(
        report,
        config.runtime_closed_loop_evidence_path,
    )
    metadata["source_result_path"] = str(source_result_path)
    required_gates = runtime_closed_loop_required_gates(config)
    missing_required_gates = [
        gate for gate in required_gates if gate not in report.required_gates
    ]
    failed_required_gates = [
        gate
        for gate in required_gates
        if not runtime_closed_loop_gate_passed(report, gate)
    ]
    supporting_failure_details = runtime_closed_loop_supporting_failure_details(
        report,
        required_gates,
    )
    source_result_failure_details = (
        runtime_closed_loop_source_result_failure_details(
            report,
            config.runtime_closed_loop_evidence_path,
            config,
        )
    )
    if (
        report.status != "passed"
        or report.errors
        or report.failed_required_gates
        or missing_required_gates
        or failed_required_gates
        or supporting_failure_details
        or source_result_failure_details
    ):
        details = []
        if report.status != "passed":
            details.append("status was not passed")
        if report.errors:
            details.append("evidence errors were present")
        if report.failed_required_gates:
            details.append(
                "failed gates: " + ", ".join(report.failed_required_gates)
            )
        if missing_required_gates:
            details.append(
                "missing required gates: " + ", ".join(missing_required_gates)
            )
        if failed_required_gates:
            details.append(
                "required gates not passed: " + ", ".join(failed_required_gates)
            )
        details.extend(supporting_failure_details)
        details.extend(source_result_failure_details)
        return InstallValidationCheck(
            name=InstallValidationCheckName.RUNTIME_CLOSED_LOOP,
            status="failed",
            dependency="runtime_closed_loop",
            message=(
                "runtime closed-loop evidence is not release-ready: "
                + "; ".join(details)
            ),
            remediation=(
                "rerun the strict local cloud PoC verifier and demo gate with the "
                "workspace execution, skill reuse, browser-controller governance, "
                "and required sandbox governance flags for this deployment mode"
            ),
            metadata=metadata,
        )

    return InstallValidationCheck(
        name=InstallValidationCheckName.RUNTIME_CLOSED_LOOP,
        status="passed",
        dependency="runtime_closed_loop",
        message="runtime closed-loop demo gate validated",
        metadata=metadata,
    )


def kubernetes_sandbox_provider_evidence_details(
    config: InstallValidationRunConfig,
    metadata: dict[str, str | int | float | bool],
) -> str:
    if not config.kubernetes_sandbox_verification_path:
        return (
            "kubernetes sandbox provider verification requires "
            "--kubernetes-sandbox-verification"
        )
    metadata["kubernetes_result_path"] = config.kubernetes_sandbox_verification_path
    try:
        result = KubernetesSandboxVerificationResult.model_validate_json(
            Path(config.kubernetes_sandbox_verification_path).read_text()
        )
    except Exception:
        return (
            "kubernetes sandbox provider verification result could not be read "
            "or matched to the schema"
        )

    metadata.update(kubernetes_sandbox_provider_metadata(result))
    return kubernetes_sandbox_provider_failure_details(result)


def kubernetes_sandbox_provider_metadata(
    result: KubernetesSandboxVerificationResult,
) -> dict[str, str | int | float | bool]:
    metadata: dict[str, str | int | float | bool] = {
        "kubernetes_sandbox_verified": (
            kubernetes_sandbox_provider_failure_details(result) == ""
        ),
        "kubernetes_provider": result.provider,
        "kubernetes_namespace": result.namespace,
        "kubernetes_session_id": result.session_id,
        "kubernetes_pod_name": result.pod_name,
        "kubernetes_network_policy_name": result.network_policy_name,
        "kubernetes_session_network_policy_name": result.network_policy_name,
        "kubernetes_session_network_policy_default_deny": (
            result.network_policy_default_deny
        ),
        "kubernetes_session_network_policy_types": ",".join(
            result.network_policy_types
        ),
        "kubernetes_session_network_policy_selector_session_id": (
            result.network_policy_session_selector.get(
                "taroai.sandbox_session_id",
                "",
            )
        ),
        "kubernetes_session_service_account_name": result.service_account_name,
        "kubernetes_exit_code": result.exit_code,
        "kubernetes_destroyed": result.destroyed,
        "kubernetes_memory_limit": result.memory_limit,
        "kubernetes_cpu_limit": result.cpu_limit,
        "kubernetes_ephemeral_storage_limit": result.ephemeral_storage_limit,
        "kubernetes_workspace_volume_size_limit": result.workspace_volume_size_limit,
        "kubernetes_tmp_volume_size_limit": result.tmp_volume_size_limit,
        "kubernetes_pod_active_deadline_seconds": result.pod_active_deadline_seconds,
        "kubernetes_host_network": result.host_network,
        "kubernetes_host_pid": result.host_pid,
        "kubernetes_host_ipc": result.host_ipc,
        "kubernetes_pod_run_as_non_root": result.pod_run_as_non_root,
        "kubernetes_seccomp_profile_type": result.seccomp_profile_type,
        "kubernetes_privileged": result.privileged,
        "kubernetes_allow_privilege_escalation": result.allow_privilege_escalation,
        "kubernetes_read_only_root_filesystem": result.read_only_root_filesystem,
        "kubernetes_dropped_capabilities": ",".join(result.dropped_capabilities),
        "kubernetes_automount_service_account_token": (
            result.automount_service_account_token
        ),
        "kubernetes_service_links_enabled": result.service_links_enabled,
        "kubernetes_termination_grace_period_seconds": (
            result.termination_grace_period_seconds
        ),
        "kubernetes_runtime_class_required": result.runtime_class_required,
        "kubernetes_image": result.image,
        "kubernetes_allowed_image_count": len(result.allowed_images),
        "kubernetes_image_digest_pinned": sandbox_runtime_image_digest_pinned(
            result.image
        ),
        "kubernetes_image_has_registry": sandbox_runtime_image_has_registry(
            result.image
        ),
        "kubernetes_image_has_non_latest_tag": (
            sandbox_runtime_image_has_non_latest_tag(result.image)
        ),
        "kubernetes_publishable_artifact_path_count": len(
            kubernetes_publishable_artifact_paths(result)
        ),
        "kubernetes_downloaded_artifact_content_length": len(
            result.downloaded_content
        ),
    }
    if result.runtime_class_name:
        metadata["kubernetes_runtime_class_name"] = result.runtime_class_name
    if result.runtime_policy is not None:
        metadata.update(
            {
                "kubernetes_runtime_policy_verified": result.runtime_policy.verified,
                "kubernetes_runtime_policy_namespace": result.runtime_policy.namespace,
                "kubernetes_resource_quota_name": result.runtime_policy.resource_quota_name,
                "kubernetes_resource_quota_pods": (
                    result.runtime_policy.resource_quota_hard.get("pods", "")
                ),
                "kubernetes_limit_range_name": result.runtime_policy.limit_range_name,
                "kubernetes_limit_range_default_memory": (
                    result.runtime_policy.limit_range_default.get("memory", "")
                ),
                "kubernetes_limit_range_default_request_memory": (
                    result.runtime_policy.limit_range_default_request.get("memory", "")
                ),
                "kubernetes_limit_range_max_memory": (
                    result.runtime_policy.limit_range_max.get("memory", "")
                ),
                "kubernetes_network_policy_name": result.runtime_policy.network_policy_name,
                "kubernetes_network_policy_default_deny": (
                    result.runtime_policy.network_policy_default_deny
                ),
                "kubernetes_controller_service_account_name": (
                    result.runtime_policy.controller_service_account_name
                ),
                "kubernetes_controller_service_account_exists": (
                    result.runtime_policy.controller_service_account_exists
                ),
                "kubernetes_runner_service_account_name": (
                    result.runtime_policy.runner_service_account_name
                ),
                "kubernetes_runner_service_account_token_automount_disabled": (
                    result.runtime_policy.runner_service_account_token_automount_disabled
                ),
                "kubernetes_controller_role_name": (
                    result.runtime_policy.controller_role_name
                ),
                "kubernetes_controller_role_binding_name": (
                    result.runtime_policy.controller_role_binding_name
                ),
                "kubernetes_controller_role_least_privilege": (
                    result.runtime_policy.controller_role_least_privilege
                ),
                "kubernetes_controller_role_binding_valid": (
                    result.runtime_policy.controller_role_binding_valid
                ),
            }
        )
    return metadata


def kubernetes_sandbox_provider_failure_details(
    result: KubernetesSandboxVerificationResult,
) -> str:
    details: list[str] = []
    if result.provider.strip().lower() not in {"k8s", "kubernetes"}:
        details.append("kubernetes sandbox provider verification used the wrong provider")
    if not result.pod_name.strip():
        details.append("kubernetes sandbox provider verification did not record a Pod")
    if not result.network_policy_name.strip():
        details.append(
            "kubernetes sandbox provider verification did not record a NetworkPolicy"
        )
    if result.exit_code != 0:
        details.append("kubernetes sandbox provider command did not exit successfully")
    if not result.destroyed:
        details.append("kubernetes sandbox provider session was not destroyed")
    if not result.snapshot_uri.strip():
        details.append("kubernetes sandbox provider did not record a snapshot URI")
    if not result.runtime_class_required:
        details.append("kubernetes sandbox runtime class was not required")
    if not result.runtime_class_name.strip():
        details.append("kubernetes sandbox runtime class name was empty")
    if result.runtime_policy is None:
        details.append("kubernetes sandbox runtime policy was not verified")
    elif not result.runtime_policy.verified:
        details.append("kubernetes sandbox runtime policy verification did not pass")
    else:
        if result.namespace.strip() != result.runtime_policy.namespace.strip():
            details.append(
                "kubernetes sandbox session namespace did not match verified "
                "runtime policy namespace"
            )
        if not result.runtime_policy.network_policy_default_deny:
            details.append(
                "kubernetes sandbox runtime NetworkPolicy does not default-deny traffic"
            )
        if not result.runtime_policy.controller_service_account_exists:
            details.append(
                "kubernetes sandbox controller ServiceAccount was not verified"
            )
        if not result.runtime_policy.runner_service_account_token_automount_disabled:
            details.append(
                "kubernetes sandbox runner ServiceAccount token automount was not disabled"
            )
        if not result.runtime_policy.controller_role_least_privilege:
            details.append("kubernetes sandbox controller Role was not least-privilege")
        if not result.runtime_policy.controller_role_binding_valid:
            details.append("kubernetes sandbox controller RoleBinding was not valid")
        session_service_account_name = result.service_account_name.strip()
        runner_service_account_name = (
            result.runtime_policy.runner_service_account_name.strip()
        )
        controller_service_account_name = (
            result.runtime_policy.controller_service_account_name.strip()
        )
        if session_service_account_name != runner_service_account_name:
            details.append(
                "kubernetes sandbox session ServiceAccount did not match "
                "verified runner ServiceAccount"
            )
        if (
            controller_service_account_name
            and session_service_account_name == controller_service_account_name
        ):
            details.append(
                "kubernetes sandbox session ServiceAccount used the controller "
                "ServiceAccount"
            )
    details.extend(kubernetes_pod_hardening_failure_details(result))
    details.extend(kubernetes_session_network_policy_failure_details(result))
    details.extend(kubernetes_artifact_evidence_failure_details(result))
    details.extend(kubernetes_image_policy_failure_details(result))
    return "; ".join(details)


def kubernetes_session_network_policy_failure_details(
    result: KubernetesSandboxVerificationResult,
) -> list[str]:
    details: list[str] = []
    if not result.network_policy_default_deny:
        details.append(
            "kubernetes sandbox session NetworkPolicy does not default-deny "
            "sandbox traffic"
        )
    if sorted(result.network_policy_types) != ["Egress", "Ingress"]:
        details.append(
            "kubernetes sandbox session NetworkPolicy did not cover Ingress and Egress"
        )
    session_selector = result.network_policy_session_selector.get(
        "taroai.sandbox_session_id",
        "",
    )
    if session_selector != result.session_id:
        details.append(
            "kubernetes sandbox session NetworkPolicy selector did not match "
            "the verified sandbox session"
        )
    return details


def kubernetes_pod_hardening_failure_details(
    result: KubernetesSandboxVerificationResult,
) -> list[str]:
    details: list[str] = []
    if not result.workspace_volume_size_limit.strip():
        details.append("kubernetes sandbox workspace volume size limit was empty")
    elif result.workspace_volume_size_limit != result.ephemeral_storage_limit:
        details.append(
            "kubernetes sandbox workspace volume size limit did not match "
            "ephemeral storage limit"
        )
    if not result.tmp_volume_size_limit.strip():
        details.append("kubernetes sandbox tmp volume size limit was empty")
    elif result.tmp_volume_size_limit != result.ephemeral_storage_limit:
        details.append(
            "kubernetes sandbox tmp volume size limit did not match "
            "ephemeral storage limit"
        )
    if result.pod_active_deadline_seconds <= 0:
        details.append("kubernetes sandbox pod active deadline was not enforced")
    if result.host_network:
        details.append("kubernetes sandbox host network access was enabled")
    if result.host_pid:
        details.append("kubernetes sandbox host PID access was enabled")
    if result.host_ipc:
        details.append("kubernetes sandbox host IPC access was enabled")
    if not result.pod_run_as_non_root:
        details.append("kubernetes sandbox pod did not require non-root execution")
    if result.seccomp_profile_type != "RuntimeDefault":
        details.append(
            "kubernetes sandbox seccomp profile was not RuntimeDefault"
        )
    if result.privileged:
        details.append("kubernetes sandbox container privileged mode was enabled")
    if result.allow_privilege_escalation:
        details.append("kubernetes sandbox container privilege escalation was allowed")
    if not result.read_only_root_filesystem:
        details.append("kubernetes sandbox root filesystem was writable")
    if "ALL" not in result.dropped_capabilities:
        details.append("kubernetes sandbox did not drop all Linux capabilities")
    if result.automount_service_account_token:
        details.append("kubernetes sandbox service account token automount was enabled")
    if result.service_links_enabled:
        details.append("kubernetes sandbox service links were enabled")
    if result.termination_grace_period_seconds < 0:
        details.append("kubernetes sandbox termination grace period was negative")
    elif result.termination_grace_period_seconds > 5:
        details.append("kubernetes sandbox termination grace period was too long")
    if result.run_as_user <= 0:
        details.append("kubernetes sandbox runAsUser was not non-root")
    if result.run_as_group <= 0:
        details.append("kubernetes sandbox runAsGroup was not non-root")
    return details


def kubernetes_artifact_evidence_failure_details(
    result: KubernetesSandboxVerificationResult,
) -> list[str]:
    details: list[str] = []
    if not kubernetes_publishable_artifact_paths(result):
        details.append(
            "kubernetes sandbox provider did not record a publishable artifact path"
        )
    if not result.downloaded_content:
        details.append("kubernetes sandbox downloaded artifact content was empty")
    return details


def kubernetes_publishable_artifact_paths(
    result: KubernetesSandboxVerificationResult,
) -> list[str]:
    return [
        path
        for path in result.file_paths
        if path.startswith("/workspace/artifacts/") and not path.endswith("/")
    ]


def kubernetes_image_policy_failure_details(
    result: KubernetesSandboxVerificationResult,
) -> list[str]:
    return sandbox_runtime_image_policy_failure_details(
        image=result.image,
        allowed_images=result.allowed_images,
        context="kubernetes sandbox",
    )


def sandbox_lifecycle_verification_failure_details(
    result: SandboxLifecycleVerificationResult,
    auth_challenge_required: bool = False,
) -> str:
    details: list[str] = []
    if not result.session_created:
        details.append("sandbox session was not created")
    if not result.command_executed:
        details.append("sandbox command was not executed")
    if not result.session_destroyed:
        details.append("sandbox session was not destroyed")
    if not result.session_destroy_confirmed:
        details.append("sandbox session destroy was not confirmed")
    if not result.post_destroy_command_blocked:
        details.append("sandbox command was not blocked after session destroy")
    if not result.command_scope_enforced:
        details.append("sandbox command scope was not enforced")
    if not result.file_scope_enforced:
        details.append("sandbox file scope was not enforced")
    if not result.file_read_scope_enforced:
        details.append("sandbox file read scope was not enforced")
    if not result.snapshot_scope_enforced:
        details.append("sandbox snapshot scope was not enforced")
    if not result.session_listed:
        details.append("sandbox session was not listed for concurrency checks")
    if not result.tenant_session_scope_enforced:
        details.append("sandbox session list did not enforce tenant scope")
    if not result.output_redacted:
        details.append("sandbox verification output was not redacted")
    if not result.artifact_path:
        details.append("sandbox artifact path was not recorded")
    elif not result.artifact_path.startswith("/workspace/artifacts/"):
        details.append("sandbox artifact path was not publishable")
    if not result.artifact_listed:
        details.append("sandbox artifact was not listed")
    if not result.artifact_downloaded:
        details.append("sandbox artifact was not downloaded")
    if result.downloaded_artifact_content_length <= 0:
        details.append("sandbox downloaded artifact content was empty")
    if not result.capabilities_checked:
        details.append("sandbox controller capabilities were not checked")
    if not result.network_isolation_declared:
        details.append("sandbox controller did not declare network isolation")
    if not result.filesystem_isolation_declared:
        details.append("sandbox controller did not declare filesystem isolation")
    if not result.resource_limits_declared:
        details.append("sandbox controller did not declare resource limits")
    if not result.destroy_supported_declared:
        details.append("sandbox controller did not declare destroy support")
    if not result.session_ttl_enforced_declared:
        details.append("sandbox controller did not declare session TTL enforcement")
    if not result.runtime_isolation_declared:
        details.append("sandbox controller did not declare runtime isolation")
    if not result.image_policy_enforced_declared:
        details.append("sandbox controller did not declare image policy enforcement")
    if result.allowed_image_count <= 0:
        details.append("sandbox controller did not declare allowed runtime images")
    if not result.max_session_ttl_seconds_declared:
        details.append("sandbox controller did not declare max session TTL")
    if not result.max_sessions_declared:
        details.append("sandbox controller did not declare global session capacity")
    if not result.max_sessions_per_tenant_declared:
        details.append("sandbox controller did not declare tenant session capacity")
    if not result.max_sessions_per_run_declared:
        details.append("sandbox controller did not declare run session capacity")
    if auth_challenge_required:
        if not result.auth_tenant_session_list_challenge_enforced:
            details.append(
                "sandbox controller tenant session-list auth challenge was not enforced"
            )
        if not result.auth_global_session_list_challenge_enforced:
            details.append(
                "sandbox controller global session-list auth challenge was not enforced"
            )
        if not result.auth_capabilities_challenge_enforced:
            details.append(
                "sandbox controller capabilities auth challenge was not enforced"
            )
        if not result.auth_challenge_enforced:
            details.append("sandbox controller auth challenge was not enforced")
    return "; ".join(details)


def check_browser_controller_health(
    config: InstallValidationRunConfig,
    client: InstallValidationHttpClient,
    readiness_body: dict[str, Any],
) -> InstallValidationCheck:
    readiness = readiness_body.get("checks", {}).get("browser")
    if not isinstance(readiness, dict):
        if release_acceptance_evidence_required(config):
            return failed_check(
                InstallValidationCheckName.BROWSER_CONTROLLER_HEALTH,
                "browser_controller",
                "browser readiness missing from /readyz",
                (
                    "upgrade the API readiness endpoint so /readyz.checks.browser "
                    "reports provider, configured state, controller endpoint, "
                    "and controller auth before accepting the install"
                ),
            )
    else:
        if readiness.get("provider") == "disabled":
            return skipped_check(
                InstallValidationCheckName.BROWSER_CONTROLLER_HEALTH,
                "browser_controller",
                "browser provider is disabled",
            )
        if readiness.get("configured") is not True:
            missing = readiness.get("missing") or []
            missing_text = (
                ", ".join(str(item) for item in missing)
                or "readiness not configured"
            )
            return failed_check(
                InstallValidationCheckName.BROWSER_CONTROLLER_HEALTH,
                "browser_controller",
                f"browser_controller readiness missing: {missing_text}",
                (
                    "configure TAROAI_BROWSER_PROVIDER, "
                    "TAROAI_BROWSER_CONTROLLER_BASE_URL, and "
                    "TAROAI_BROWSER_CONTROLLER_API_KEY"
                ),
            )
    headers = {}
    default_key_check = default_operator_token_check(
        config=config,
        name=InstallValidationCheckName.BROWSER_CONTROLLER_HEALTH,
        dependency="browser_controller",
        field_name="browser_controller_api_key",
        env_var_name="TAROAI_BROWSER_CONTROLLER_API_KEY",
        value=config.browser_controller_api_key,
    )
    if default_key_check is not None:
        return default_key_check
    if (
        isinstance(readiness, dict)
        and readiness.get("controller_required") is True
        and readiness.get("capabilities_checked") is not True
    ):
        return failed_check(
            InstallValidationCheckName.BROWSER_CONTROLLER_HEALTH,
            "browser_controller",
            "browser-controller readiness missing: browser_controller_capabilities",
            (
                "confirm the API can authenticate to the browser controller "
                "and read its /capabilities response before accepting the install"
            ),
        )
    browser_readiness_capability_details = (
        browser_readiness_capability_failure_details(readiness)
    )
    if browser_readiness_capability_details:
        return failed_check(
            InstallValidationCheckName.BROWSER_CONTROLLER_HEALTH,
            "browser_controller",
            (
                "browser-controller readiness insufficient: "
                f"{browser_readiness_capability_details}"
            ),
            (
                "fix the browser controller /capabilities response so API "
                "readiness declares auth, TTL, and capacity controls"
            ),
        )
    if config.browser_controller_api_key.strip():
        headers["Authorization"] = f"Bearer {config.browser_controller_api_key.strip()}"
    try:
        response = client.get(join_url(config.browser_base_url, "/healthz"), headers=headers)
    except Exception:
        return failed_check(
            InstallValidationCheckName.BROWSER_CONTROLLER_HEALTH,
            "browser_controller",
            "browser-controller health request failed",
            "check browser-controller service DNS, API key, network policy, and logs",
        )
    if not status_ok(response.status_code):
        return failed_check(
            InstallValidationCheckName.BROWSER_CONTROLLER_HEALTH,
            "browser_controller",
            f"browser-controller /healthz returned HTTP {response.status_code}",
            "check browser-controller deployment health, API key, and network policy",
        )

    if not config.browser_controller_verification_path:
        if browser_lifecycle_evidence_required(config, readiness):
            return failed_check(
                InstallValidationCheckName.BROWSER_CONTROLLER_HEALTH,
                "browser_controller",
                (
                    "browser-controller lifecycle validation requires "
                    "--browser-controller-verification"
                ),
                (
                    "run scripts/verify-browser-controller.sh against the installed "
                    "browser controller and provide the redacted JSON result before "
                    "accepting the install"
                ),
            )
        return skipped_check(
            InstallValidationCheckName.BROWSER_CONTROLLER_HEALTH,
            "browser_controller",
            (
                "browser-controller lifecycle validation requires "
                "--browser-controller-verification"
            ),
        )

    metadata: dict[str, str | int | float | bool] = {
        "result_path": config.browser_controller_verification_path,
    }
    try:
        result = BrowserControllerVerificationResult.model_validate_json(
            Path(config.browser_controller_verification_path).read_text()
        )
    except Exception:
        return failed_check(
            InstallValidationCheckName.BROWSER_CONTROLLER_HEALTH,
            "browser_controller",
            (
                "browser-controller lifecycle verification result could not be "
                "read or matched to the schema"
            ),
            (
                "rerun the customer browser-controller lifecycle validation "
                "harness and provide a redacted JSON result"
            ),
        )

    metadata.update(result.model_dump(exclude_none=True))
    redact_report_url_metadata(metadata, "screenshot_uri")
    details = join_failure_details(
        browser_controller_provider_evidence_details(readiness, result),
        browser_controller_verification_failure_details(
            result,
            auth_challenge_required=bool(config.browser_controller_api_key.strip()),
        ),
    )
    if not details:
        return InstallValidationCheck(
            name=InstallValidationCheckName.BROWSER_CONTROLLER_HEALTH,
            status="passed",
            dependency="browser_controller",
            message=(
                "browser-controller lifecycle verification result confirms "
                "declared TTL and capacity capabilities, session open, "
                "duplicate rejection, scoped session read/delete and action "
                "enforcement, tenant-scoped listing, action, capture/extract, "
                "and delete"
            ),
            metadata=metadata,
        )
    return InstallValidationCheck(
        name=InstallValidationCheckName.BROWSER_CONTROLLER_HEALTH,
        status="failed",
        dependency="browser_controller",
        message=(
            "browser-controller lifecycle verification result is not ready: "
            f"{details}"
        ),
        remediation=(
            "fix browser-controller lifecycle, action execution, capture/extract "
            "verification, session cleanup, or redacted verification output "
            "before accepting the install"
        ),
        metadata=metadata,
    )


def browser_lifecycle_evidence_required(
    config: InstallValidationRunConfig,
    readiness: Any,
) -> bool:
    if not release_acceptance_evidence_required(config):
        return False
    if not isinstance(readiness, dict):
        return False
    if readiness.get("provider") == "disabled":
        return False
    return readiness.get("configured") is True


def browser_controller_provider_evidence_details(
    readiness: Any,
    result: BrowserControllerVerificationResult,
) -> str:
    if not isinstance(readiness, dict):
        return ""
    readiness_provider = readiness.get("provider")
    if not isinstance(readiness_provider, str) or not readiness_provider.strip():
        return ""
    ready_provider = readiness_provider.strip().lower()
    result_provider = result.provider.strip().lower()
    if ready_provider == result_provider:
        return ""
    return (
        "browser-controller verification provider did not match API readiness "
        f"provider: readiness={ready_provider}, evidence={result_provider}"
    )


def browser_controller_verification_failure_details(
    result: BrowserControllerVerificationResult,
    auth_challenge_required: bool = False,
) -> str:
    details: list[str] = []
    if not result.session_opened:
        details.append("browser session was not opened")
    if not result.action_executed:
        details.append("browser action was not executed")
    if not result.session_deleted:
        details.append("browser session was not deleted")
    if not result.session_delete_confirmed:
        details.append("browser session deletion was not confirmed")
    if not result.duplicate_session_rejected:
        details.append("duplicate browser session was not rejected")
    if not result.action_scope_enforced:
        details.append("browser action scope was not enforced")
    if not result.session_read_scope_enforced:
        details.append("browser session read scope was not enforced")
    if not result.session_delete_scope_enforced:
        details.append("browser session delete scope was not enforced")
    if not result.session_listed:
        details.append("browser session was not listed for concurrency checks")
    if not result.tenant_session_scope_enforced:
        details.append("browser session list did not enforce tenant scope")
    if not result.capabilities_checked:
        details.append("browser controller capabilities were not checked")
    if not result.session_ttl_enforced_declared:
        details.append("browser controller did not declare session TTL enforcement")
    if not result.max_session_ttl_seconds_declared:
        details.append("browser controller did not declare maximum session TTL")
    if not result.max_sessions_declared:
        details.append("browser controller did not declare global session capacity")
    if not result.max_sessions_per_tenant_declared:
        details.append("browser controller did not declare tenant session capacity")
    if not result.max_sessions_per_run_declared:
        details.append("browser controller did not declare run session capacity")
    if not result.screenshot_or_extract_verified:
        details.append("browser screenshot or extract was not verified")
    if not result.screenshot_uri and result.extract_text_length <= 0:
        details.append("browser screenshot URI was not recorded")
    if result.screenshot_content_length <= 0 and result.extract_text_length <= 0:
        details.append("browser screenshot content was empty")
    if auth_challenge_required:
        if not result.auth_tenant_session_list_challenge_enforced:
            details.append(
                "browser controller tenant session-list auth challenge was not enforced"
            )
        if not result.auth_global_session_list_challenge_enforced:
            details.append(
                "browser controller global session-list auth challenge was not enforced"
            )
        if not result.auth_capabilities_challenge_enforced:
            details.append(
                "browser controller capabilities auth challenge was not enforced"
            )
        if not result.auth_challenge_enforced:
            details.append("browser controller auth challenge was not enforced")
    if not result.output_redacted:
        details.append("browser verification output was not redacted")
    return "; ".join(details)


def check_web_workspace_health(
    config: InstallValidationRunConfig,
    client: InstallValidationHttpClient,
) -> InstallValidationCheck:
    if not config.web_base_url:
        return skipped_check(
            InstallValidationCheckName.WEB_WORKSPACE_HEALTH,
            "web_workspace",
            "Web Workspace health validation requires --web-base-url",
        )

    metadata: dict[str, str | int | float | bool] = {
        "web_base_url": redacted_url_for_report(config.web_base_url),
    }
    try:
        html_response = client.get(join_url(config.web_base_url, "/"))
        script_response = client.get(join_url(config.web_base_url, "/assets/main.js"))
    except Exception:
        return failed_check(
            InstallValidationCheckName.WEB_WORKSPACE_HEALTH,
            "web_workspace",
            "Web Workspace health request failed",
            "check Web service DNS, ingress, network policy, and static asset service logs",
        )

    metadata.update(
        {
            "html_status_code": html_response.status_code,
            "script_status_code": script_response.status_code,
            "html_bytes": len(html_response.body.encode("utf-8")),
            "script_bytes": len(script_response.body.encode("utf-8")),
        }
    )
    details = web_workspace_failure_details(html_response, script_response)
    if not details:
        return InstallValidationCheck(
            name=InstallValidationCheckName.WEB_WORKSPACE_HEALTH,
            status="passed",
            dependency="web_workspace",
            message="Web Workspace HTML and frontend contract are reachable",
            metadata=metadata,
        )
    return InstallValidationCheck(
        name=InstallValidationCheckName.WEB_WORKSPACE_HEALTH,
        status="failed",
        dependency="web_workspace",
        message=f"Web Workspace verification is not ready: {details}",
        remediation=(
            "fix the Web service route, package static assets, or restore the "
            "Workspace chat, readiness, Bearer-auth, browser, and artifact contract"
        ),
        metadata=metadata,
    )


def web_workspace_failure_details(
    html_response: InstallValidationHttpResponse,
    script_response: InstallValidationHttpResponse,
) -> str:
    details: list[str] = []
    if not status_ok(html_response.status_code):
        details.append(f"workspace HTML returned HTTP {html_response.status_code}")
    if not status_ok(script_response.status_code):
        details.append(f"workspace script returned HTTP {script_response.status_code}")

    html_requirements = {
        "title": "Taroai Workspace",
        "chat column": 'data-testid="chat-column"',
        "conversation log": 'data-testid="conversation-log"',
        "CREAO-compatible composer hint": (
            "Press Enter to send, Shift+Enter for a new line."
        ),
        "composer input": 'id="composer-input"',
        "send button": 'id="send-button"',
        "login email input": 'id="login-email"',
        "login password input": 'id="login-password"',
        "login button": 'id="login-button"',
        "logout button": 'id="logout-button"',
        "auth status": "data-auth-status",
        "readiness status": "data-readiness-status",
        "readiness model": "data-readiness-model",
        "readiness sandbox": "data-readiness-sandbox",
        "browser storage object": "data-browser-storage-object",
        "artifact list": "data-artifact-list",
        "workspace script": "./assets/main.js",
    }
    details.extend(
        f"web workspace HTML missing {name}"
        for name, fragment in html_requirements.items()
        if fragment not in html_response.body
    )

    script_requirements = {
        "login endpoint": '"/api/auth/login"',
        "Bearer token storage": "taroai.accessToken",
        "Authorization header": "Authorization",
        "Bearer header prefix": '"Bearer "',
        "session token storage": "sessionStorage.setItem",
        "session token removal": "sessionStorage.removeItem",
        "readiness endpoint": '"/readyz"',
        "model readiness": "model_gateway",
        "sandbox readiness": "sandbox",
        "missing readiness fields": "missing.join",
        "storage download": "/api/storage/objects/",
    }
    details.extend(
        f"web workspace script missing {name}"
        for name, fragment in script_requirements.items()
        if fragment not in script_response.body
    )
    return "; ".join(details)


def readiness_check(
    name: InstallValidationCheckName,
    dependency: str,
    readiness_body: dict[str, Any],
    readiness_key: str,
    success_message: str,
    remediation: str,
) -> InstallValidationCheck:
    check = readiness_body.get("checks", {}).get(readiness_key, {})
    if check.get("configured") is True:
        return passed_check(name, dependency, success_message)
    missing = check.get("missing") or []
    missing_text = ", ".join(str(item) for item in missing) or "readiness not configured"
    return failed_check(
        name,
        dependency,
        f"{dependency} readiness missing: {missing_text}",
        remediation,
    )


def default_operator_token_check(
    config: InstallValidationRunConfig,
    name: InstallValidationCheckName,
    dependency: str,
    field_name: str,
    env_var_name: str,
    value: str,
) -> InstallValidationCheck | None:
    mode = config.deployment_mode.strip().lower()
    if mode not in CUSTOMER_OPERATED_INSTALL_VALIDATION_MODES:
        return None
    if value.strip() not in DEFAULT_OPERATOR_TOKEN_VALUES:
        return None
    return failed_check(
        name,
        dependency,
        f"{field_name} uses a default value",
        (
            f"configure {env_var_name} with a generated deployment-specific value "
            "from the approved secret manager before accepting the install"
        ),
    )


def passed_check(
    name: InstallValidationCheckName,
    dependency: str,
    message: str,
) -> InstallValidationCheck:
    return InstallValidationCheck(
        name=name,
        status="passed",
        dependency=dependency,
        message=message,
    )


def failed_check(
    name: InstallValidationCheckName,
    dependency: str,
    message: str,
    remediation: str,
) -> InstallValidationCheck:
    return InstallValidationCheck(
        name=name,
        status="failed",
        dependency=dependency,
        message=message,
        remediation=remediation,
    )


def skipped_check(
    name: InstallValidationCheckName,
    dependency: str,
    message: str,
) -> InstallValidationCheck:
    return InstallValidationCheck(
        name=name,
        status="skipped",
        dependency=dependency,
        message=message,
    )


def join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def status_ok(status_code: int) -> bool:
    return 200 <= status_code < 300


def validate_http_url(value: str, field_name: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field_name} must be an absolute HTTP URL")


def redacted_url_for_report(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return value

    hostname = parsed.hostname
    if not hostname:
        netloc = parsed.netloc.rsplit("@", 1)[-1]
    else:
        host = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
        try:
            port = parsed.port
        except ValueError:
            port = None
        netloc = host if port is None else f"{host}:{port}"
    return parsed._replace(netloc=netloc, params="", query="", fragment="").geturl()


def redact_report_url_metadata(
    metadata: dict[str, str | int | float | bool],
    *keys: str,
) -> None:
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, str):
            metadata[key] = redacted_url_for_report(value)


def parse_args(argv: list[str] | None = None) -> InstallValidationRunConfig:
    parser = argparse.ArgumentParser(
        description="Run Taroai private install validation HTTP checks."
    )
    parser.add_argument("--deployment-id", default=os.environ.get("TAROAI_DEPLOYMENT_ID", "local"))
    parser.add_argument("--mode", default=os.environ.get("TAROAI_DEPLOYMENT_MODE", "private"))
    parser.add_argument("--api-base-url", default=os.environ.get("TAROAI_API_BASE_URL", "http://localhost:8000"))
    parser.add_argument(
        "--browser-base-url",
        default=os.environ.get("TAROAI_BROWSER_CONTROLLER_BASE_URL", "http://localhost:8001"),
    )
    parser.add_argument(
        "--sandbox-controller-api-key",
        default=os.environ.get("TAROAI_SANDBOX_CONTROLLER_API_KEY", ""),
    )
    parser.add_argument(
        "--browser-controller-api-key",
        default=os.environ.get("TAROAI_BROWSER_CONTROLLER_API_KEY", ""),
    )
    parser.add_argument("--web-base-url", default=os.environ.get("TAROAI_WEB_BASE_URL"))
    parser.add_argument("--release-package", default=os.environ.get("TAROAI_RELEASE_PACKAGE_PATH"))
    parser.add_argument(
        "--release-transfer-evidence",
        default=os.environ.get("TAROAI_RELEASE_TRANSFER_EVIDENCE_PATH"),
    )
    parser.add_argument(
        "--expected-release-package-sha256",
        default=os.environ.get("TAROAI_RELEASE_PACKAGE_SHA256"),
    )
    parser.add_argument(
        "--release-package-signature",
        default=os.environ.get("TAROAI_RELEASE_PACKAGE_SIGNATURE_PATH"),
    )
    parser.add_argument(
        "--release-package-trusted-public-key",
        action="append",
        default=[],
    )
    parser.add_argument("--migration-plan", default=os.environ.get("TAROAI_MIGRATION_PLAN_PATH"))
    parser.add_argument(
        "--object-storage-verification",
        default=os.environ.get("TAROAI_OBJECT_STORAGE_VERIFICATION_PATH"),
    )
    parser.add_argument(
        "--redis-queue-verification",
        default=os.environ.get("TAROAI_REDIS_QUEUE_VERIFICATION_PATH"),
    )
    parser.add_argument(
        "--secret-manager-verification",
        default=os.environ.get("TAROAI_SECRET_MANAGER_VERIFICATION_PATH"),
    )
    parser.add_argument(
        "--model-gateway-verification",
        default=os.environ.get("TAROAI_MODEL_GATEWAY_VERIFICATION_PATH"),
    )
    parser.add_argument(
        "--sandbox-verification",
        default=os.environ.get("TAROAI_SANDBOX_VERIFICATION_PATH"),
    )
    parser.add_argument(
        "--kubernetes-sandbox-verification",
        default=os.environ.get("TAROAI_KUBERNETES_SANDBOX_VERIFICATION_PATH"),
    )
    parser.add_argument(
        "--browser-controller-verification",
        default=os.environ.get("TAROAI_BROWSER_CONTROLLER_VERIFICATION_PATH"),
    )
    parser.add_argument(
        "--event-stream-verification",
        default=os.environ.get("TAROAI_EVENT_STREAM_VERIFICATION_PATH"),
    )
    parser.add_argument(
        "--audit-write-verification",
        default=os.environ.get("TAROAI_AUDIT_WRITE_VERIFICATION_PATH"),
    )
    parser.add_argument(
        "--trace-collector-verification",
        default=os.environ.get("TAROAI_TRACE_COLLECTOR_VERIFICATION_PATH"),
    )
    parser.add_argument(
        "--support-bundle-redaction-evidence",
        default=os.environ.get("TAROAI_SUPPORT_BUNDLE_REDACTION_EVIDENCE_PATH"),
    )
    parser.add_argument(
        "--restore-drill-verification",
        default=os.environ.get("TAROAI_RESTORE_DRILL_VERIFICATION_PATH"),
    )
    parser.add_argument(
        "--runtime-closed-loop-evidence",
        default=os.environ.get("TAROAI_RUNTIME_CLOSED_LOOP_EVIDENCE_PATH"),
    )
    parser.add_argument(
        "--output",
        default=os.environ.get("TAROAI_INSTALL_VALIDATION_OUTPUT"),
    )
    parser.add_argument("--timeout-seconds", type=int, default=30)
    parsed = parser.parse_args(argv)
    return InstallValidationRunConfig(
        deployment_id=parsed.deployment_id,
        deployment_mode=parsed.mode,
        api_base_url=parsed.api_base_url,
        browser_base_url=parsed.browser_base_url,
        sandbox_controller_api_key=parsed.sandbox_controller_api_key,
        browser_controller_api_key=parsed.browser_controller_api_key,
        web_base_url=parsed.web_base_url,
        release_package_path=parsed.release_package,
        release_transfer_evidence_path=parsed.release_transfer_evidence,
        expected_release_package_checksum_sha256=parsed.expected_release_package_sha256,
        release_package_signature_path=parsed.release_package_signature,
        release_package_trusted_public_keys=parse_trusted_public_keys(
            parsed.release_package_trusted_public_key
        ),
        migration_plan_path=parsed.migration_plan,
        object_storage_verification_path=parsed.object_storage_verification,
        redis_queue_verification_path=parsed.redis_queue_verification,
        secret_manager_verification_path=parsed.secret_manager_verification,
        model_gateway_verification_path=parsed.model_gateway_verification,
        sandbox_verification_path=parsed.sandbox_verification,
        kubernetes_sandbox_verification_path=parsed.kubernetes_sandbox_verification,
        browser_controller_verification_path=parsed.browser_controller_verification,
        event_stream_verification_path=parsed.event_stream_verification,
        audit_write_verification_path=parsed.audit_write_verification,
        trace_collector_verification_path=parsed.trace_collector_verification,
        support_bundle_redaction_evidence_path=parsed.support_bundle_redaction_evidence,
        restore_drill_verification_path=parsed.restore_drill_verification,
        runtime_closed_loop_evidence_path=parsed.runtime_closed_loop_evidence,
        output_path=parsed.output,
        timeout_seconds=parsed.timeout_seconds,
    )


def main(argv: list[str] | None = None) -> int:
    config = parse_args(argv)
    report = run_install_validation(config)
    output = report.model_dump_json(indent=2)
    if config.output_path:
        write_install_validation_output(Path(config.output_path), output)
    else:
        print(output)
    return 0 if report.is_ready else 1


def write_install_validation_output(output_path: Path, output: str) -> None:
    atomic_write_text(output_path, output + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
