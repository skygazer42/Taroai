from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from taroai.deployment import (
    InstallValidationCheck,
    InstallValidationCheckName,
    InstallValidationReport,
    InstallValidationStatus,
)


def successful_checks() -> list[dict]:
    observed_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        {
            "name": name.value,
            "status": "passed",
            "dependency": name.value,
            "message": f"{name.value} validated",
            "observed_at": observed_at,
            "duration_ms": 10,
        }
        for name in InstallValidationCheckName
    ]


def test_install_validation_report_accepts_complete_success_report():
    report = InstallValidationReport(
        deployment_id="private-acme",
        deployment_mode="private",
        checked_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        checks=successful_checks(),
    )

    assert report.status == InstallValidationStatus.PASSED
    assert report.is_ready is True
    assert report.failure_summary() == []
    assert {check.name for check in report.checks} == set(InstallValidationCheckName)


def test_install_validation_report_requires_every_operator_check():
    checks = [
        check
        for check in successful_checks()
        if check["name"] != InstallValidationCheckName.AUDIT_WRITE.value
    ]

    with pytest.raises(ValidationError) as error:
        InstallValidationReport(
            deployment_id="private-acme",
            deployment_mode="private",
            checked_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            checks=checks,
        )

    assert "install validation report missing checks: ['audit_write']" in str(error.value)


def test_install_validation_report_requires_browser_controller_health_check():
    checks = [
        check
        for check in successful_checks()
        if check["name"] != InstallValidationCheckName.BROWSER_CONTROLLER_HEALTH.value
    ]

    with pytest.raises(ValidationError) as error:
        InstallValidationReport(
            deployment_id="private-acme",
            deployment_mode="private",
            checked_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            checks=checks,
        )

    assert "install validation report missing checks: ['browser_controller_health']" in str(
        error.value
    )


def test_install_validation_report_requires_web_workspace_health_check():
    checks = [
        check
        for check in successful_checks()
        if check["name"] != InstallValidationCheckName.WEB_WORKSPACE_HEALTH.value
    ]

    with pytest.raises(ValidationError) as error:
        InstallValidationReport(
            deployment_id="private-acme",
            deployment_mode="private",
            checked_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            checks=checks,
        )

    assert "install validation report missing checks: ['web_workspace_health']" in str(
        error.value
    )


def test_install_validation_check_requires_remediation_for_failed_dependency():
    with pytest.raises(ValidationError) as error:
        InstallValidationCheck(
            name="object_storage_read_write",
            status="failed",
            dependency="object_storage",
            message="upload denied",
            remediation="",
        )

    assert "failed validation checks require remediation" in str(error.value)


def test_install_validation_report_summarizes_failed_dependencies():
    checks = successful_checks()
    object_storage_index = next(
        index
        for index, check in enumerate(checks)
        if check["name"] == InstallValidationCheckName.OBJECT_STORAGE_READ_WRITE.value
    )
    checks[object_storage_index] = {
        "name": "object_storage_read_write",
        "status": "failed",
        "dependency": "object_storage",
        "message": "upload denied",
        "remediation": "check S3 credentials and bucket policy",
    }
    report = InstallValidationReport(
        deployment_id="private-acme",
        deployment_mode="private",
        checked_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        checks=checks,
    )

    assert report.status == InstallValidationStatus.FAILED
    assert report.is_ready is False
    assert report.failure_summary() == [
        "object_storage_read_write: object_storage - upload denied "
        "(remediation: check S3 credentials and bucket policy)"
    ]


def test_private_install_validation_runbook_is_committed():
    runbook = Path("docs/operations/private-install-validation.md")

    text = runbook.read_text()

    assert "database migration" in text
    assert "Redis connectivity" in text
    assert "release package integrity" in text
    assert "--release-transfer-evidence" in text
    assert "--expected-release-package-sha256" in text
    assert "--release-package-signature" in text
    assert "--release-package-trusted-public-key" in text
    assert "--migration-plan" in text
    assert "--object-storage-verification" in text
    assert "--redis-queue-verification" in text
    assert "--secret-manager-verification" in text
    assert "--model-gateway-verification" in text
    assert "--sandbox-verification" in text
    assert "--kubernetes-sandbox-verification" in text
    assert "--browser-controller-verification" in text
    assert "--event-stream-verification" in text
    assert "--audit-write-verification" in text
    assert "--trace-collector-verification" in text
    assert "--support-bundle-redaction-evidence" in text
    assert "--restore-drill-verification" in text
    assert "taroai.model_gateway.verification" in text
    assert "taroai.secrets.verification" in text
    assert "taroai.deployment.api_verification" in text
    assert "taroai.observability.verification" in text
    assert "taroai.sandbox.lifecycle_verification" in text
    assert "taroai.sandbox.controller_service" in text
    assert "taroai.sandbox.browser_verification" in text
    assert "taroai.deployment.restore_drill_verification" in text
    assert "object storage read/write" in text
    assert "secret manager read" in text
    assert "sandbox lifecycle" in text
    assert "artifact round-trip" in text
    assert "screenshot content length" in text
    assert "GET /capabilities" in text
    assert "network_isolation=true" in text
    assert "filesystem_isolation=true" in text
    assert "resource_limits=true" in text
    assert "session_ttl_enforced=true" in text
    assert "max_session_ttl_seconds" in text
    assert "max_sessions" in text
    assert "max_sessions_per_tenant" in text
    assert "max_sessions_per_run" in text
    assert "before `/sessions` is called" in text
    assert "GET /sessions?tenant_id=..." in text
    assert "TAROAI_SANDBOX_CONTROLLER_SESSION_TTL_SECONDS" in text
    assert "default-deny `NetworkPolicy`" in text
    assert "live per-session NetworkPolicy selector/types/" in text
    assert "does not target the verified sandbox session" in text
    assert "Expired tracked sessions are rejected" in text
    assert "confirms the Pod is no longer active" in text
    assert "confirms each deleted Pod is no longer active" in text
    assert "each per-session NetworkPolicy is gone" in text
    assert "block post-destroy command execution" in text
    assert "post_destroy_command_blocked" in text
    assert "file-read" in text
    assert "file_read_scope_enforced" in text
    assert "--kubernetes-sandbox-verification" in text
    assert "runtime_class_required=true" in text
    assert "non-empty\n`runtime_class_name`" in text
    assert "approved sandbox runtime image" in text
    assert "covered by configured allowed image patterns" in text
    assert "digest-pinned image" in text
    assert "must not use `:latest`" in text
    assert "emptyDir.sizeLimit" in text
    assert "activeDeadlineSeconds" in text
    assert "hostNetwork=false" in text
    assert "hostPID=false" in text
    assert "hostIPC=false" in text
    assert "runAsNonRoot=true" in text
    assert "seccompProfile.type=RuntimeDefault" in text
    assert "privileged=false" in text
    assert "allowPrivilegeEscalation=false" in text
    assert "readOnlyRootFilesystem=true" in text
    assert 'capabilities.drop=["ALL"]' in text
    assert "automountServiceAccountToken=false" in text
    assert "enableServiceLinks=false" in text
    assert "terminationGracePeriodSeconds" in text
    assert "publishable artifact path" in text
    assert "downloaded artifact content" in text
    assert "sandbox concurrency and\nlicense enforcement" in text
    assert "status=destroyed" in text
    assert "--denied-tenant-id" in text
    assert "denied tenant's\nsession list" in text
    assert "browser-controller lifecycle" in text
    assert "confirm the deleted session is no longer readable" in text
    assert (
        "follow-up `GET /sessions/{session_id}?tenant_id=...&workspace_id=...&run_id=...`"
        in text
    )
    assert "session-read, session-delete, and action probes" in text
    assert "Browser-controller responses must echo the requested tenant" in text
    assert "GET /sessions?tenant_id=..." in text
    assert "active browser capacity" in text
    assert "/readyz.checks.browser" in text
    assert "Web Workspace health" in text
    assert "web workspace HTML" in text
    assert "login controls" in text
    assert "composer controls" in text
    assert "--web-base-url" in text
    assert "event stream" in text
    assert "audit write" in text
    assert "run_id" in text
    assert "first_event_sequence" in text
    assert "trace collector" in text
    assert "backup restore drill" in text
    assert "OpenAI-compatible Model Gateway" in text
    assert "scripts/validate-install.sh" in text
    assert "scripts/build-migration-plan.sh" in text
    assert "scripts/verify-object-storage.sh" in text
    assert "scripts/verify-redis-queue.sh" in text
    assert "scripts/verify-secret-manager.sh" in text
    assert "scripts/verify-event-stream.sh" in text
    assert "scripts/verify-audit-write.sh" in text
    assert "scripts/verify-model-gateway.sh" in text
    assert "scripts/verify-sandbox-lifecycle.sh" in text
    assert "scripts/verify-browser-controller.sh" in text
    assert "scripts/verify-trace-collector.sh" in text
    assert "scripts/verify-restore-drill.sh" in text


def test_private_packaging_plan_tracks_executable_install_validation_script():
    plan = Path("docs/plans/2026-07-01-24-private-deployment-packaging.md")

    text = plan.read_text()

    assert "Modify: `scripts/validate-install.sh`" in text
    assert "Future: `scripts/validate-install.sh`" not in text
    assert "planned `scripts/validate-install.sh` command" not in text
    assert "executable validation script and live dependency probing remain planned" not in text
    assert "scripts/validate-install.sh" in text
    assert "live API health/readiness probing" in text
    assert "Web health" in text
    assert "web workspace HTML" in text
    assert "login controls" in text
    assert "composer controls" in text
    assert "browser-controller health" in text
    assert "/readyz.checks.browser" in text
    assert "TAROAI_BROWSER_CONTROLLER_API_KEY" in text
    assert "TAROAI_SANDBOX_CONTROLLER_API_KEY" in text
    assert "local_process" in text
    assert "customer-operated" in text
