import json
from pathlib import Path

from taroai.deployment.local_cloud_poc_demo_gate import (
    DemoReadinessGateConfig,
    DemoReadinessGateReport,
    demo_readiness_gate_report_json,
    parse_args,
    run_demo_readiness_gate,
    write_demo_readiness_gate_report,
)


def strict_api_ready_payload() -> dict:
    return {
        "api_health_ok": True,
        "browser_health_ok": True,
        "web_ok": True,
        "tenant_id": "tenant_acme",
        "owner_user_id": "user_owner",
        "tenant_ready": True,
        "model_gateway_configured": True,
        "sandbox_configured": True,
        "sandbox_provider": "local_process",
        "run_id": "run_1",
        "execute_status_code": 200,
        "run_status": "succeeded",
        "artifact_count": 1,
        "artifact_names": ["report.md"],
        "model_artifact_required_name_found": True,
        "model_artifact_storage_object_count": 1,
        "model_artifact_total_download_bytes": 72,
        "model_artifact_storage_object_id": "storage_report_1",
        "model_artifact_download_bytes": 72,
        "model_artifact_required_text_found": True,
        "model_sandbox_command_event_seen": True,
        "model_artifact_promoted_event_seen": True,
        "model_run_event_payload_safe": True,
        "model_sandbox_command_exit_code": 0,
        "model_sandbox_command_output_uri": (
            "s3://taroai-artifacts/tenant_acme/workspace_acme/runs/run_1/"
            "sandbox-command-outputs/model_sandbox-output.json"
        ),
        "model_sandbox_command_output_storage_object_id": (
            "storage_model_sandbox_output_1"
        ),
        "model_artifact_event_matches_storage_object": True,
        "model_runtime_state_status": "succeeded",
        "model_runtime_sandbox_session_id": "runtime_sandbox_1",
        "model_runtime_completed_step_count": 1,
        "model_runtime_promoted_artifact_path_count": 1,
        "model_runtime_required_artifact_path_found": True,
        "model_trace_span_count": 3,
        "model_trace_event_count": 3,
        "model_trace_billing_meter_count": 1,
        "model_trace_audit_event_count": 1,
        "model_trace_runtime_tool_call_seen": True,
        "model_trace_billing_tool_call_seen": True,
        "model_trace_audit_tool_executed_seen": True,
        "model_trace_payload_safe": True,
        "sandbox_session_id": "sandbox_1",
        "sandbox_exit_code": 0,
        "sandbox_output_uri": (
            "s3://taroai-artifacts/tenant_acme/workspace_acme/runs/run_1/"
            "sandbox-command-outputs/sandbox_1-output.json"
        ),
        "sandbox_output_storage_object_id": "storage_sandbox_output_1",
        "sandbox_output_download_bytes": 40,
        "sandbox_session_destroyed": True,
        "sandbox_destroy_status_confirmed": True,
        "sandbox_post_destroy_command_blocked": True,
        "browser_screenshot_storage_object_id": "storage_browser_1",
        "browser_screenshot_download_bytes": 128,
        "browser_session_id": "browser_verify_1",
        "browser_session_listed": True,
        "browser_tenant_session_scope_enforced": True,
        "browser_session_read_scope_enforced": True,
        "browser_session_delete_scope_enforced": True,
        "browser_extract_text": "Browser smoke OK",
    }


def write_result_payload(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def test_demo_gate_accepts_strict_api_ready_evidence(tmp_path: Path):
    result_path = tmp_path / "local-cloud-poc-result.json"
    write_result_payload(result_path, strict_api_ready_payload())

    report = run_demo_readiness_gate(
        DemoReadinessGateConfig(result_path=result_path)
    )

    assert report.status == "passed"
    assert report.demo_ready is True
    assert report.local_smoke_ready is True
    assert report.strict_model_ready is True
    assert report.workspace_execution_ready is False
    assert report.skill_reuse_ready is False
    assert report.required_gates == ["demo_ready"]
    assert report.failed_required_gates == []
    assert report.gate_results["demo_ready"] is True
    assert report.gate_results["strict_model_ready"] is True
    assert report.gate_results["workspace_execution_ready"] is False
    assert report.gate_results["sandbox_governance_ready"] is False
    assert report.summary == "strict API demo ready"
    assert report.errors == []


def test_demo_gate_requires_browser_screenshot_artifact_for_local_smoke(
    tmp_path: Path,
):
    result_path = tmp_path / "local-cloud-poc-result.json"
    payload = strict_api_ready_payload() | {
        "browser_screenshot_storage_object_id": None,
        "browser_screenshot_download_bytes": 0,
    }
    write_result_payload(result_path, payload)

    report = run_demo_readiness_gate(
        DemoReadinessGateConfig(result_path=result_path)
    )

    assert report.status == "failed"
    assert report.local_smoke_ready is False
    assert report.demo_ready is False
    assert report.failed_required_gates == ["demo_ready"]


def test_demo_gate_rejects_strict_model_evidence_when_execute_api_failed(
    tmp_path: Path,
):
    result_path = tmp_path / "local-cloud-poc-result.json"
    payload = strict_api_ready_payload() | {
        "execute_status_code": 503,
        "execute_code": "model_gateway_unavailable",
    }
    write_result_payload(result_path, payload)

    report = run_demo_readiness_gate(
        DemoReadinessGateConfig(result_path=result_path)
    )

    assert report.status == "failed"
    assert report.strict_model_ready is False
    assert report.demo_ready is False
    assert report.failed_required_gates == ["demo_ready"]


def test_demo_gate_can_require_workspace_execution_evidence(tmp_path: Path):
    result_path = tmp_path / "local-cloud-poc-result.json"
    write_result_payload(result_path, strict_api_ready_payload())

    report = run_demo_readiness_gate(
        DemoReadinessGateConfig(
            result_path=result_path,
            require_workspace_execution=True,
        )
    )

    assert report.status == "failed"
    assert report.demo_ready is True
    assert "workspace_execution_ready=false" in report.errors


def test_demo_gate_can_require_browser_controller_governance_evidence(
    tmp_path: Path,
):
    result_path = tmp_path / "local-cloud-poc-result.json"
    write_result_payload(result_path, strict_api_ready_payload())

    report = run_demo_readiness_gate(
        DemoReadinessGateConfig(
            result_path=result_path,
            require_browser_controller_governance=True,
        )
    )

    assert report.status == "failed"
    assert report.demo_ready is True
    assert report.browser_controller_governance_ready is False
    assert "browser_controller_governance_ready=false" in report.errors


def test_demo_gate_requires_browser_session_lifecycle_scope_for_governance(
    tmp_path: Path,
):
    result_path = tmp_path / "local-cloud-poc-result.json"
    payload = strict_api_ready_payload() | {
        "browser_controller_auth_enforced": True,
        "browser_controller_auth_tenant_session_list_challenge_enforced": True,
        "browser_controller_auth_global_session_list_challenge_enforced": True,
        "browser_controller_auth_capabilities_challenge_enforced": True,
        "browser_controller_capabilities_checked": True,
        "browser_controller_auth_required": True,
        "browser_controller_session_ttl_enforced": True,
        "browser_controller_max_session_ttl_seconds": 1800,
        "browser_controller_max_sessions": 50,
        "browser_controller_max_sessions_per_tenant": 20,
        "browser_controller_max_sessions_per_run": 3,
        "browser_session_read_scope_enforced": False,
        "browser_session_delete_scope_enforced": False,
    }
    write_result_payload(result_path, payload)

    report = run_demo_readiness_gate(
        DemoReadinessGateConfig(
            result_path=result_path,
            require_browser_controller_governance=True,
        )
    )

    assert report.status == "failed"
    assert report.browser_controller_governance_ready is False
    assert report.failed_required_gates == [
        "demo_ready",
        "browser_controller_governance_ready",
    ]


def test_demo_gate_can_require_sandbox_governance_evidence(tmp_path: Path):
    result_path = tmp_path / "local-cloud-poc-result.json"
    write_result_payload(result_path, strict_api_ready_payload())

    report = run_demo_readiness_gate(
        DemoReadinessGateConfig(
            result_path=result_path,
            require_sandbox_governance=True,
        )
    )

    assert report.status == "failed"
    assert report.demo_ready is True
    assert report.sandbox_governance_ready is False
    assert report.required_gates == [
        "demo_ready",
        "sandbox_governance_ready",
    ]
    assert report.failed_required_gates == ["sandbox_governance_ready"]
    assert report.gate_results["demo_ready"] is True
    assert report.gate_results["sandbox_governance_ready"] is False
    assert "sandbox_governance_ready=false" in report.errors


def test_demo_gate_requires_runtime_and_image_policy_for_sandbox_governance(
    tmp_path: Path,
):
    result_path = tmp_path / "local-cloud-poc-result.json"
    payload = strict_api_ready_payload() | {
        "sandbox_capabilities_checked": True,
        "sandbox_network_isolation_declared": True,
        "sandbox_filesystem_isolation_declared": True,
        "sandbox_resource_limits_declared": True,
        "sandbox_destroy_supported_declared": True,
        "sandbox_session_ttl_enforced_declared": True,
        "sandbox_max_session_ttl_seconds": 1800,
        "sandbox_max_sessions": 50,
        "sandbox_max_sessions_per_tenant": 20,
        "sandbox_max_sessions_per_run": 3,
        "sandbox_runtime_isolation_declared": False,
        "sandbox_image_policy_enforced_declared": False,
        "sandbox_allowed_image_count": 0,
    }
    write_result_payload(result_path, payload)

    report = run_demo_readiness_gate(
        DemoReadinessGateConfig(
            result_path=result_path,
            require_sandbox_governance=True,
        )
    )

    assert report.status == "failed"
    assert report.gate_results["sandbox_governance_ready"] is False
    assert "sandbox_runtime_isolation_declared=false" in report.errors
    assert "sandbox_image_policy_enforced_declared=false" in report.errors


def test_demo_gate_requires_sandbox_post_destroy_execution_block_for_governance(
    tmp_path: Path,
):
    result_path = tmp_path / "local-cloud-poc-result.json"
    payload = strict_api_ready_payload() | {
        "sandbox_capabilities_checked": True,
        "sandbox_network_isolation_declared": True,
        "sandbox_filesystem_isolation_declared": True,
        "sandbox_resource_limits_declared": True,
        "sandbox_destroy_supported_declared": True,
        "sandbox_session_ttl_enforced_declared": True,
        "sandbox_runtime_isolation_declared": True,
        "sandbox_image_policy_enforced_declared": True,
        "sandbox_allowed_image_count": 1,
        "sandbox_max_session_ttl_seconds": 1800,
        "sandbox_max_sessions": 50,
        "sandbox_max_sessions_per_tenant": 20,
        "sandbox_max_sessions_per_run": 3,
        "sandbox_post_destroy_command_blocked": False,
    }
    write_result_payload(result_path, payload)

    report = run_demo_readiness_gate(
        DemoReadinessGateConfig(
            result_path=result_path,
            require_sandbox_governance=True,
        )
    )

    assert report.status == "failed"
    assert report.sandbox_governance_ready is False
    assert report.failed_required_gates == [
        "demo_ready",
        "sandbox_governance_ready",
    ]
    assert "sandbox_post_destroy_command_blocked=false" in report.errors


def test_demo_gate_redacts_invalid_result_json_errors(tmp_path: Path):
    result_path = tmp_path / "local-cloud-poc-result.json"
    secret_value = "sk-sensitive-token-1234567890"
    write_result_payload(
        result_path,
        {
            "api_key": secret_value,
            "tenant_id": "tenant_acme",
        },
    )

    report = run_demo_readiness_gate(
        DemoReadinessGateConfig(result_path=result_path)
    )
    joined_errors = "\n".join(report.errors)

    assert report.status == "failed"
    assert secret_value not in joined_errors
    assert "[REDACTED:" in joined_errors
    assert report.gate_results["demo_ready"] is False
    assert report.failed_required_gates == ["demo_ready"]


def test_demo_gate_report_writer_redacts_secret_shaped_values(tmp_path: Path):
    output_path = tmp_path / "evidence" / "demo-gate-result.json"
    secret_value = "sk-sensitive-token-1234567890"
    report = DemoReadinessGateReport(
        status="failed",
        result_path=str(tmp_path / "local-cloud-poc-result.json"),
        required_gates=["demo_ready"],
        gate_results={"demo_ready": False},
        errors=[f"upstream validation included {secret_value}"],
    )

    write_demo_readiness_gate_report(output_path, report)
    output = output_path.read_text(encoding="utf-8")

    assert secret_value not in output
    assert "[REDACTED:" in output


def test_demo_gate_report_json_redacts_secret_shaped_values():
    secret_value = "sk-sensitive-token-1234567890"
    report = DemoReadinessGateReport(
        status="failed",
        result_path="dist/local-cloud-poc-result.json",
        required_gates=["demo_ready"],
        gate_results={"demo_ready": False},
        errors=[f"upstream validation included {secret_value}"],
    )

    output = demo_readiness_gate_report_json(report)

    assert secret_value not in output
    assert "[REDACTED:" in output


def test_demo_gate_cli_parses_result_path_and_requirements(tmp_path: Path):
    result_path = tmp_path / "local-cloud-poc-result.json"
    output_path = tmp_path / "demo-gate-result.json"

    config = parse_args(
        [
            str(result_path),
            "--require-workspace-execution",
            "--require-skill-reuse",
            "--require-browser-controller-governance",
            "--require-sandbox-governance",
            "--output",
            str(output_path),
        ]
    )

    assert config.result_path == result_path
    assert config.require_workspace_execution is True
    assert config.require_skill_reuse is True
    assert config.require_browser_controller_governance is True
    assert config.require_sandbox_governance is True
    assert config.output_path == output_path
