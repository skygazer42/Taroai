import argparse
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from taroai.deployment.local_cloud_poc_verification import (
    LocalCloudPocVerificationResult,
)
from taroai.support.redaction import atomic_write_text, redact_text_entry


class DemoReadinessGateConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result_path: Path
    require_workspace_execution: bool = False
    require_skill_reuse: bool = False
    require_browser_controller_governance: bool = False
    require_sandbox_governance: bool = False
    output_path: Path | None = None


class DemoReadinessGateReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["passed", "failed"]
    result_path: str
    demo_ready: bool = False
    local_smoke_ready: bool = False
    strict_model_ready: bool = False
    workspace_execution_ready: bool = False
    skill_reuse_ready: bool = False
    browser_controller_governance_ready: bool = False
    sandbox_governance_ready: bool = False
    sandbox_runtime_isolation_declared: bool = False
    sandbox_image_policy_enforced_declared: bool = False
    sandbox_allowed_image_count: int = 0
    required_gates: list[str] = Field(default_factory=list)
    failed_required_gates: list[str] = Field(default_factory=list)
    gate_results: dict[str, bool] = Field(default_factory=dict)
    summary: str = ""
    errors: list[str] = Field(default_factory=list)


def run_demo_readiness_gate(
    config: DemoReadinessGateConfig,
) -> DemoReadinessGateReport:
    try:
        result = LocalCloudPocVerificationResult.model_validate_json(
            config.result_path.read_text(encoding="utf-8")
        )
    except Exception as error:
        return DemoReadinessGateReport(
            status="failed",
            result_path=str(config.result_path),
            required_gates=required_gate_names(config),
            failed_required_gates=failed_required_gate_names(
                required_gate_names(config),
                unavailable_gate_results(),
            ),
            gate_results=unavailable_gate_results(),
            summary="local cloud PoC result could not be read",
            errors=[safe_error_message(f"invalid result JSON: {error}")],
        )

    errors = demo_readiness_errors(
        result,
        require_workspace_execution=config.require_workspace_execution,
        require_skill_reuse=config.require_skill_reuse,
        require_browser_controller_governance=(
            config.require_browser_controller_governance
        ),
        require_sandbox_governance=config.require_sandbox_governance,
    )
    status: Literal["passed", "failed"] = "failed" if errors else "passed"
    gate_results = gate_result_map(result)
    return DemoReadinessGateReport(
        status=status,
        result_path=str(config.result_path),
        demo_ready=result.demo_ready,
        local_smoke_ready=result.local_smoke_ready,
        strict_model_ready=result.strict_model_ready,
        workspace_execution_ready=result.workspace_execution_ready,
        skill_reuse_ready=result.skill_reuse_ready,
        browser_controller_governance_ready=browser_controller_governance_ready(
            result
        ),
        sandbox_governance_ready=sandbox_governance_ready(result),
        sandbox_runtime_isolation_declared=(
            result.sandbox_runtime_isolation_declared
        ),
        sandbox_image_policy_enforced_declared=(
            result.sandbox_image_policy_enforced_declared
        ),
        sandbox_allowed_image_count=result.sandbox_allowed_image_count,
        required_gates=required_gate_names(config),
        failed_required_gates=failed_required_gate_names(
            required_gate_names(config),
            gate_results,
        ),
        gate_results=gate_results,
        summary=result.demo_readiness_summary,
        errors=errors,
    )


def demo_readiness_errors(
    result: LocalCloudPocVerificationResult,
    *,
    require_workspace_execution: bool,
    require_skill_reuse: bool,
    require_browser_controller_governance: bool,
    require_sandbox_governance: bool,
) -> list[str]:
    errors: list[str] = []
    if not result.demo_ready:
        errors.append(f"demo_ready=false ({result.demo_readiness_summary})")
    if require_workspace_execution and not result.workspace_execution_ready:
        errors.append("workspace_execution_ready=false")
    if require_skill_reuse and not result.skill_reuse_ready:
        errors.append("skill_reuse_ready=false")
    if (
        require_browser_controller_governance
        and not browser_controller_governance_ready(result)
    ):
        errors.append("browser_controller_governance_ready=false")
    if require_sandbox_governance and not sandbox_governance_ready(result):
        errors.append("sandbox_governance_ready=false")
        if not result.sandbox_runtime_isolation_declared:
            errors.append("sandbox_runtime_isolation_declared=false")
        if not result.sandbox_image_policy_enforced_declared:
            errors.append("sandbox_image_policy_enforced_declared=false")
        if result.sandbox_allowed_image_count <= 0:
            errors.append("sandbox_allowed_image_count=0")
        if not result.sandbox_post_destroy_command_blocked:
            errors.append("sandbox_post_destroy_command_blocked=false")
    return errors


def required_gate_names(config: DemoReadinessGateConfig) -> list[str]:
    names = ["demo_ready"]
    if config.require_workspace_execution:
        names.append("workspace_execution_ready")
    if config.require_skill_reuse:
        names.append("skill_reuse_ready")
    if config.require_browser_controller_governance:
        names.append("browser_controller_governance_ready")
    if config.require_sandbox_governance:
        names.append("sandbox_governance_ready")
    return names


def failed_required_gate_names(
    required_gates: list[str],
    gate_results: dict[str, bool],
) -> list[str]:
    return [name for name in required_gates if not gate_results.get(name, False)]


def gate_result_map(
    result: LocalCloudPocVerificationResult,
) -> dict[str, bool]:
    return {
        "demo_ready": result.demo_ready,
        "local_smoke_ready": result.local_smoke_ready,
        "strict_model_ready": result.strict_model_ready,
        "workspace_execution_ready": result.workspace_execution_ready,
        "skill_reuse_ready": result.skill_reuse_ready,
        "browser_controller_governance_ready": (
            browser_controller_governance_ready(result)
        ),
        "sandbox_governance_ready": sandbox_governance_ready(result),
    }


def unavailable_gate_results() -> dict[str, bool]:
    return {
        "demo_ready": False,
        "local_smoke_ready": False,
        "strict_model_ready": False,
        "workspace_execution_ready": False,
        "skill_reuse_ready": False,
        "browser_controller_governance_ready": False,
        "sandbox_governance_ready": False,
    }


def safe_error_message(message: str) -> str:
    redacted, _ = redact_text_entry("local-cloud-poc-demo-gate-error", message)
    return redacted


def browser_controller_governance_ready(
    result: LocalCloudPocVerificationResult,
) -> bool:
    return all(
        [
            result.browser_controller_auth_enforced,
            result.browser_controller_auth_tenant_session_list_challenge_enforced,
            result.browser_controller_auth_global_session_list_challenge_enforced,
            result.browser_controller_auth_capabilities_challenge_enforced,
            result.browser_controller_capabilities_checked,
            result.browser_controller_auth_required,
            result.browser_controller_session_ttl_enforced,
            result.browser_controller_max_session_ttl_seconds > 0,
            result.browser_controller_max_sessions > 0,
            result.browser_controller_max_sessions_per_tenant > 0,
            result.browser_controller_max_sessions_per_run > 0,
            result.browser_session_read_scope_enforced,
            result.browser_session_delete_scope_enforced,
        ]
    )


def sandbox_governance_ready(
    result: LocalCloudPocVerificationResult,
) -> bool:
    return all(
        [
            result.sandbox_capabilities_checked,
            result.sandbox_network_isolation_declared,
            result.sandbox_filesystem_isolation_declared,
            result.sandbox_resource_limits_declared,
            result.sandbox_destroy_supported_declared,
            result.sandbox_session_ttl_enforced_declared,
            result.sandbox_runtime_isolation_declared,
            result.sandbox_image_policy_enforced_declared,
            result.sandbox_allowed_image_count > 0,
            result.sandbox_max_session_ttl_seconds > 0,
            result.sandbox_max_sessions > 0,
            result.sandbox_max_sessions_per_tenant > 0,
            result.sandbox_max_sessions_per_run > 0,
            result.sandbox_session_destroyed,
            result.sandbox_destroy_status_confirmed,
            result.sandbox_post_destroy_command_blocked,
        ]
    )


def write_demo_readiness_gate_report(
    output_path: Path,
    report: DemoReadinessGateReport,
) -> None:
    atomic_write_text(
        output_path,
        demo_readiness_gate_report_json(report, indent=2) + "\n",
        encoding="utf-8",
    )


def demo_readiness_gate_report_json(
    report: DemoReadinessGateReport,
    *,
    indent: int | None = None,
    sort_keys: bool = False,
) -> str:
    report_json = json.dumps(
        report.model_dump(mode="json"),
        indent=indent,
        sort_keys=sort_keys,
    )
    redacted_report, _ = redact_text_entry(
        "local-cloud-poc-demo-gate-report",
        report_json,
    )
    return redacted_report


def parse_args(argv: list[str] | None = None) -> DemoReadinessGateConfig:
    parser = argparse.ArgumentParser(
        description="Validate local cloud PoC verifier result readiness rollups."
    )
    parser.add_argument("result_path")
    parser.add_argument("--require-workspace-execution", action="store_true")
    parser.add_argument("--require-skill-reuse", action="store_true")
    parser.add_argument(
        "--require-browser-controller-governance",
        action="store_true",
    )
    parser.add_argument("--require-sandbox-governance", action="store_true")
    parser.add_argument("--output", default=None)
    parsed = parser.parse_args(argv)
    return DemoReadinessGateConfig(
        result_path=Path(parsed.result_path),
        require_workspace_execution=parsed.require_workspace_execution,
        require_skill_reuse=parsed.require_skill_reuse,
        require_browser_controller_governance=(
            parsed.require_browser_controller_governance
        ),
        require_sandbox_governance=parsed.require_sandbox_governance,
        output_path=Path(parsed.output) if parsed.output else None,
    )


def main(argv: list[str] | None = None) -> int:
    config = parse_args(argv)
    report = run_demo_readiness_gate(config)
    if config.output_path is not None:
        write_demo_readiness_gate_report(config.output_path, report)
    else:
        print(demo_readiness_gate_report_json(report, sort_keys=True))
    return 0 if report.status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
