import argparse
import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlencode, urlparse
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from taroai.support.redaction import atomic_write_text, redact_text_entry


DEFAULT_RUN_MESSAGE = (
    "Use sandbox.command to create a short Markdown hello report at "
    "/workspace/artifacts/report.md. The report should include a heading "
    "and one sentence including the exact phrase "
    "'local cloud PoC execution path'."
)
RUN_CLEANUP_FAILURE_EVENT_TYPES = {
    "browser.session.destroy_failed",
    "sandbox.session.destroy_failed",
}
DEFAULT_DRAFT_SKILL_MANIFEST = {
    "id": "sales.erp_invoice_matching",
    "version": "1.0.0",
    "name": "ERP Invoice Matching",
    "description": "Match ERP invoices against renewal account data.",
    "type": "workflow_skill",
    "owner": "solutions/sales",
    "input_schema": {
        "type": "object",
        "required": ["account_id"],
        "properties": {"account_id": {"type": "string"}},
    },
    "output_schema": {
        "type": "object",
        "required": ["matches"],
        "properties": {"matches": {"type": "array", "items": {"type": "object"}}},
    },
    "required_scopes": ["erp.invoice.read"],
    "risk_level": "medium",
    "runtime": {"sandbox": "workflow", "timeout_seconds": 120},
    "billing_meters": ["tool_call_count"],
}


class LocalCloudPocVerificationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_base_url: str = Field(default="http://localhost:8000", min_length=1)
    browser_base_url: str = Field(default="http://localhost:8001", min_length=1)
    browser_controller_api_key: str = Field(default="", repr=False)
    web_base_url: str | None = Field(default="http://localhost:3000")
    bootstrap_token: str = Field(default="", min_length=1)
    tenant_slug: str = Field(default="acme", min_length=1)
    owner_email: str = Field(default="owner@example.com", min_length=3)
    owner_display_name: str = Field(default="Owner", min_length=1)
    owner_password: str = Field(default="correct horse battery staple", min_length=8)
    run_message: str = Field(
        default=DEFAULT_RUN_MESSAGE,
        min_length=1,
    )
    sandbox_command: str = Field(default="python --version", min_length=1)
    browser_session_id: str = Field(
        default_factory=lambda: f"browser_verify_{uuid4().hex[:12]}",
        min_length=1,
    )
    browser_denied_tenant_id: str = Field(
        default="tenant_browser_verify_denied",
        min_length=1,
    )
    browser_smoke_text: str = Field(default="Browser smoke OK", min_length=1)
    browser_workspace_url: str | None = None
    browser_workspace_api_base_url: str | None = None
    browser_workspace_auth_poll_interval_seconds: float = Field(default=0.25, ge=0)
    browser_workspace_submit_message: str | None = None
    browser_workspace_submit_expected_text: str = Field(
        default="model gateway model is not configured",
        min_length=1,
    )
    browser_workspace_submit_poll_interval_seconds: float = Field(default=0.25, ge=0)
    browser_workspace_submit_poll_attempts: int = Field(default=30, ge=1)
    browser_workspace_missing_skill_name: str = Field(
        default="ERP invoice reconciliation",
        min_length=1,
    )
    browser_workspace_missing_skill_comment: str = Field(
        default="Need this repeated workflow in a reusable solution pack.",
        min_length=1,
    )
    browser_workspace_solution_pack_id: str = Field(
        default="sales.renewal_ops",
        min_length=1,
    )
    browser_workspace_missing_skill_feedback_count: int = Field(default=3, ge=1)
    browser_workspace_draft_skill_name: str = Field(
        default="ERP Invoice Matching",
        min_length=1,
    )
    browser_workspace_draft_summary: str = Field(
        default="Add governed invoice matching skill draft.",
        min_length=1,
    )
    browser_workspace_draft_pack_version: str = Field(default="1.0.1", min_length=1)
    browser_workspace_draft_skill_manifest_json: str = Field(
        default_factory=lambda: json.dumps(
            [DEFAULT_DRAFT_SKILL_MANIFEST],
            indent=2,
        ),
        min_length=1,
    )
    timeout_seconds: int = Field(default=30, ge=1)
    run_status_poll_attempts: int = Field(default=5, ge=1)
    run_status_poll_interval_seconds: float = Field(default=1.0, ge=0)
    require_model_execution: bool = False
    model_artifact_required_name: str = Field(default="report.md", min_length=1)
    model_artifact_required_text: str = Field(
        default="local cloud PoC execution path",
        min_length=1,
    )
    output_path: str | None = None

    @model_validator(mode="after")
    def validate_urls(self) -> "LocalCloudPocVerificationConfig":
        for field_name in ["api_base_url", "browser_base_url"]:
            validate_http_url(getattr(self, field_name), field_name)
        if self.web_base_url is not None:
            validate_http_url(self.web_base_url, "web_base_url")
        if self.browser_workspace_url is not None:
            validate_http_url(self.browser_workspace_url, "browser_workspace_url")
        if self.browser_workspace_api_base_url is not None:
            validate_http_url(
                self.browser_workspace_api_base_url,
                "browser_workspace_api_base_url",
            )
        browser_workspace_errors = []
        if (
            self.browser_workspace_api_base_url is not None
            and self.browser_workspace_url is None
        ):
            browser_workspace_errors.append(
                "browser_workspace_url is required when browser_workspace_api_base_url is configured"
            )
        if (
            self.require_model_execution
            and self.browser_workspace_url is not None
            and self.browser_workspace_api_base_url is None
        ):
            browser_workspace_errors.append(
                "browser_workspace_api_base_url is required when strict model execution uses browser workspace"
            )
        if self.browser_workspace_submit_message is not None:
            if self.browser_workspace_url is None:
                browser_workspace_errors.append(
                    "browser_workspace_url is required when browser workspace submit is enabled"
                )
            if self.browser_workspace_api_base_url is None:
                browser_workspace_errors.append(
                    "browser_workspace_api_base_url is required when browser workspace submit is enabled"
                )
        if (
            self.require_model_execution
            and self.browser_workspace_url is not None
            and self.browser_workspace_submit_expected_text == "succeeded"
            and self.browser_workspace_submit_message is None
        ):
            browser_workspace_errors.append(
                "browser_workspace_submit_message is required when strict model execution uses browser workspace"
            )
        if browser_workspace_errors:
            raise ValueError("; ".join(browser_workspace_errors))
        return self


class LocalCloudPocHttpResponse(BaseModel):
    status_code: int = Field(ge=100)
    body: str = ""
    body_bytes: bytes = b""

    @model_validator(mode="after")
    def populate_body_bytes(self) -> "LocalCloudPocHttpResponse":
        if not self.body_bytes and self.body:
            self.body_bytes = self.body.encode("utf-8")
        return self

    def json_body(self) -> dict[str, Any]:
        if not self.body:
            return {}
        parsed = json.loads(self.body)
        if not isinstance(parsed, dict):
            raise RuntimeError("local cloud PoC verifier expected a JSON object")
        return parsed

    def json_value(self) -> Any:
        if not self.body:
            return None
        return json.loads(self.body)


class LocalCloudPocVerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_base_url: str = ""
    browser_base_url: str = ""
    web_base_url: str | None = None
    local_smoke_ready: bool = False
    strict_model_ready: bool = False
    workspace_execution_ready: bool = False
    skill_reuse_ready: bool = False
    demo_ready: bool = False
    demo_readiness_summary: str = "not evaluated"
    api_health_ok: bool
    browser_health_ok: bool
    browser_controller_auth_enforced: bool = False
    browser_controller_auth_tenant_session_list_challenge_enforced: bool = False
    browser_controller_auth_global_session_list_challenge_enforced: bool = False
    browser_controller_auth_capabilities_challenge_enforced: bool = False
    browser_controller_capabilities_checked: bool = False
    browser_controller_auth_required: bool = False
    browser_controller_session_ttl_enforced: bool = False
    browser_controller_max_session_ttl_seconds: int = 0
    browser_controller_max_sessions: int = 0
    browser_controller_max_sessions_per_tenant: int = 0
    browser_controller_max_sessions_per_run: int = 0
    browser_controller_navigation_allowlist_enforced: bool = False
    browser_controller_navigation_allowed_host_count: int = 0
    web_ok: bool
    tenant_id: str
    owner_user_id: str
    tenant_ready: bool
    model_gateway_configured: bool
    model_gateway_missing: list[str] = Field(default_factory=list)
    sandbox_configured: bool
    sandbox_provider: str
    sandbox_missing: list[str] = Field(default_factory=list)
    sandbox_capabilities_checked: bool = False
    sandbox_network_isolation_declared: bool = False
    sandbox_filesystem_isolation_declared: bool = False
    sandbox_resource_limits_declared: bool = False
    sandbox_destroy_supported_declared: bool = False
    sandbox_session_ttl_enforced_declared: bool = False
    sandbox_runtime_isolation_declared: bool = False
    sandbox_image_policy_enforced_declared: bool = False
    sandbox_allowed_image_count: int = 0
    sandbox_max_session_ttl_seconds: int = 0
    sandbox_max_sessions: int = 0
    sandbox_max_sessions_per_tenant: int = 0
    sandbox_max_sessions_per_run: int = 0
    run_id: str
    execute_status_code: int
    execute_code: str | None = None
    run_status: str | None = None
    artifact_count: int = 0
    artifact_names: list[str] = Field(default_factory=list)
    model_artifact_required_name_found: bool = False
    model_artifact_storage_object_count: int = 0
    model_artifact_total_download_bytes: int = 0
    model_artifact_storage_object_id: str | None = None
    model_artifact_download_bytes: int = 0
    model_artifact_required_text_found: bool = False
    model_run_event_types: list[str] = Field(default_factory=list)
    model_sandbox_command_event_seen: bool = False
    model_artifact_promoted_event_seen: bool = False
    model_run_event_payload_safe: bool = False
    model_sandbox_command_exit_code: int | None = None
    model_sandbox_command_output_uri: str | None = None
    model_sandbox_command_output_storage_object_id: str | None = None
    model_browser_action_storage_object_id: str | None = None
    model_artifact_promoted_storage_object_id: str | None = None
    model_artifact_event_matches_storage_object: bool = False
    model_runtime_state_status: str | None = None
    model_runtime_sandbox_session_id: str | None = None
    model_runtime_browser_session_id: str | None = None
    model_runtime_completed_step_count: int = 0
    model_runtime_promoted_artifact_path_count: int = 0
    model_runtime_required_artifact_path_found: bool = False
    model_trace_span_count: int = 0
    model_trace_event_count: int = 0
    model_trace_billing_meter_count: int = 0
    model_trace_audit_event_count: int = 0
    model_trace_runtime_tool_call_seen: bool = False
    model_trace_billing_tool_call_seen: bool = False
    model_trace_audit_tool_executed_seen: bool = False
    model_trace_payload_safe: bool = False
    sandbox_session_id: str
    sandbox_exit_code: int
    sandbox_output_uri: str
    sandbox_output_storage_object_id: str | None = None
    sandbox_output_download_bytes: int = 0
    sandbox_session_destroyed: bool = False
    sandbox_destroy_status_confirmed: bool = False
    sandbox_post_destroy_command_blocked: bool = False
    browser_screenshot_uri: str | None = None
    browser_screenshot_storage_object_id: str | None = None
    browser_screenshot_download_bytes: int = 0
    browser_session_id: str
    browser_session_listed: bool = False
    browser_tenant_session_scope_enforced: bool = False
    browser_session_read_scope_enforced: bool = False
    browser_session_delete_scope_enforced: bool = False
    browser_extract_text: str
    browser_workspace_text: str | None = None
    browser_workspace_bootstrap_status: str | None = None
    browser_workspace_bootstrap_tenant_id: str | None = None
    browser_workspace_bootstrap_user_id: str | None = None
    browser_workspace_bootstrap_workspace_id: str | None = None
    browser_workspace_bootstrap_token_cleared: bool = False
    browser_workspace_auth_status: str | None = None
    browser_workspace_readiness_status: str | None = None
    browser_workspace_readiness_model: str | None = None
    browser_workspace_readiness_sandbox: str | None = None
    browser_workspace_submit_text: str | None = None
    browser_workspace_execution_model_route: str | None = None
    browser_workspace_evidence_summary: str | None = None
    browser_workspace_delivery_summary: str | None = None
    browser_workspace_delivery_chain_status: str | None = None
    browser_workspace_delivery_chain_run_id: str | None = None
    browser_workspace_delivery_chain_sandbox_session_id: str | None = None
    browser_workspace_delivery_chain_artifact_storage_object_id: str | None = None
    browser_workspace_delivery_chain_terminal_storage_object_id: str | None = None
    browser_workspace_delivery_chain_browser_storage_object_id: str | None = None
    browser_workspace_event_integrity_status: str | None = None
    browser_workspace_event_integrity_count: str | None = None
    browser_workspace_event_integrity_sequence: str | None = None
    browser_workspace_event_integrity_closure: str | None = None
    browser_workspace_trace_status_text: str | None = None
    browser_workspace_trace_span_count_text: str | None = None
    browser_workspace_trace_event_count_text: str | None = None
    browser_workspace_trace_billing_count_text: str | None = None
    browser_workspace_trace_audit_count_text: str | None = None
    browser_workspace_trace_error_text: str | None = None
    browser_workspace_browser_storage_object_id: str | None = None
    browser_workspace_browser_preview_storage_object_id: str | None = None
    browser_workspace_artifact_preview_text: str | None = None
    browser_workspace_artifact_preview_storage_object_id: str | None = None
    browser_workspace_artifact_download_storage_object_id: str | None = None
    browser_workspace_artifact_download_status: str | None = None
    browser_workspace_artifact_downloaded_storage_object_id: str | None = None
    browser_workspace_terminal_text: str | None = None
    browser_workspace_terminal_output_storage_object_id: str | None = None
    browser_workspace_feedback_status: str | None = None
    browser_workspace_feedback_api_seen: bool = False
    browser_workspace_feedback_rating: int | None = None
    browser_workspace_missing_skill_feedback_status: str | None = None
    browser_workspace_missing_skill_feedback_api_count: int = 0
    browser_workspace_candidate_status: str | None = None
    browser_workspace_eval_candidate_api_count: int = 0
    browser_workspace_eval_candidate_review_api_count: int = 0
    browser_workspace_pack_candidate_status: str | None = None
    browser_workspace_pack_candidate_api_count: int = 0
    browser_workspace_pack_candidate_review_api_count: int = 0
    browser_workspace_draft_status: str | None = None
    browser_workspace_draft_api_status: str | None = None
    browser_workspace_draft_api_applied: bool = False
    browser_workspace_solution_pack_install_status: str | None = None
    browser_workspace_solution_pack_install_api_seen: bool = False
    browser_workspace_solution_pack_install_skill_count: int = 0
    browser_workspace_skill_invoke_status: str | None = None
    browser_workspace_skill_run_status: str | None = None
    browser_workspace_skill_run_id: str | None = None
    browser_workspace_skill_run_api_status: str | None = None
    browser_workspace_skill_run_artifact_count: int = 0
    browser_workspace_skill_run_artifact_download_bytes: int = 0
    browser_workspace_skill_run_required_text_found: bool = False
    browser_workspace_skill_invocation_event_seen: bool = False
    browser_workspace_skill_invocation_event_matches_skill: bool = False
    browser_workspace_skill_run_sandbox_command_event_seen: bool = False
    browser_workspace_skill_run_artifact_promoted_event_seen: bool = False
    browser_workspace_skill_run_event_payload_safe: bool = False
    browser_workspace_skill_runtime_state_status: str | None = None
    browser_workspace_skill_runtime_sandbox_session_id: str | None = None
    browser_workspace_skill_runtime_required_artifact_path_found: bool = False
    browser_workspace_skill_trace_span_count: int = 0
    browser_workspace_skill_trace_event_count: int = 0
    browser_workspace_skill_trace_billing_meter_count: int = 0
    browser_workspace_skill_trace_audit_event_count: int = 0
    browser_workspace_skill_trace_runtime_tool_call_seen: bool = False
    browser_workspace_skill_trace_billing_tool_call_seen: bool = False
    browser_workspace_skill_trace_audit_tool_executed_seen: bool = False
    browser_workspace_skill_trace_payload_safe: bool = False
    browser_workspace_skill_trace_status_text: str | None = None
    browser_workspace_skill_trace_span_count_text: str | None = None
    browser_workspace_skill_trace_event_count_text: str | None = None
    browser_workspace_skill_trace_billing_count_text: str | None = None
    browser_workspace_skill_trace_audit_count_text: str | None = None
    browser_workspace_skill_trace_error_text: str | None = None
    browser_workspace_skill_run_history_status: str | None = None
    browser_workspace_skill_run_history_text: str | None = None
    browser_workspace_skill_history_selection_trace_status: str | None = None
    browser_workspace_skill_history_selection_delivery_summary: str | None = None
    browser_workspace_skill_history_selection_delivery_chain_status: str | None = None
    browser_workspace_skill_history_selection_delivery_chain_run_id: str | None = None
    browser_workspace_skill_history_selection_delivery_chain_sandbox_session: str | None = None
    browser_workspace_skill_history_selection_delivery_chain_artifact_storage: str | None = None
    browser_workspace_skill_history_selection_delivery_chain_terminal_storage: str | None = None
    browser_workspace_skill_history_selection_event_integrity_status: str | None = None
    browser_workspace_skill_history_selection_event_integrity_count: str | None = None
    browser_workspace_skill_history_selection_event_integrity_sequence: str | None = None
    browser_workspace_skill_history_selection_event_integrity_closure: str | None = None
    browser_workspace_skill_history_selection_terminal_text: str | None = None
    browser_workspace_skill_history_selection_terminal_output_storage_object_id: str | None = None
    browser_workspace_skill_history_selection_artifact_preview_text: str | None = None
    browser_workspace_skill_history_selection_previewed_storage_object_id: str | None = None
    browser_workspace_skill_history_selection_runtime_state_status: str | None = None
    browser_workspace_skill_history_selection_runtime_sandbox_session: str | None = None
    browser_workspace_skill_history_selection_runtime_artifact_count: str | None = None
    browser_workspace_skill_history_selection_execution_summary: str | None = None
    browser_workspace_skill_history_selection_execution_model_route: str | None = None
    browser_workspace_skill_history_selection_execution_sandbox: str | None = None
    browser_workspace_skill_history_selection_execution_artifact: str | None = None
    browser_workspace_skill_history_selection_download_storage_object_id: str | None = None
    browser_workspace_skill_history_selection_download_status: str | None = None
    browser_workspace_skill_history_selection_downloaded_storage_object_id: str | None = None
    browser_workspace_skill_history_selection_feedback_status: str | None = None
    browser_workspace_skill_history_selection_feedback_api_seen: bool = False
    browser_workspace_skill_history_selection_feedback_rating: int | None = None
    browser_workspace_skill_evidence_summary: str | None = None
    browser_workspace_skill_delivery_summary: str | None = None
    browser_workspace_skill_artifact_preview_text: str | None = None
    solution_pack_reuse_version: str | None = None
    solution_pack_reuse_skill_id: str | None = None
    solution_pack_reuse_version_count: int = 0
    solution_pack_reuse_marketplace_visible: bool = False
    solution_pack_reuse_workspace_installed: bool = False
    solution_pack_reuse_invocation_ready: bool = False
    solution_pack_reuse_missing_required_scopes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def populate_demo_readiness(self) -> "LocalCloudPocVerificationResult":
        self.local_smoke_ready = self._local_smoke_ready()
        self.strict_model_ready = self._strict_model_ready()
        self.workspace_execution_ready = self._workspace_execution_ready()
        self.skill_reuse_ready = self._skill_reuse_ready()

        workspace_required = self.browser_workspace_submit_text is not None
        skill_required = (
            self.browser_workspace_draft_status is not None
            or self.browser_workspace_skill_run_id is not None
        )
        self.demo_ready = (
            self.local_smoke_ready
            and self.strict_model_ready
            and (not workspace_required or self.workspace_execution_ready)
            and (not skill_required or self.skill_reuse_ready)
        )
        self.demo_readiness_summary = self._demo_readiness_summary(
            workspace_required=workspace_required,
            skill_required=skill_required,
        )
        return self

    def _local_smoke_ready(self) -> bool:
        return all(
            [
                self.api_health_ok,
                self.browser_health_ok,
                self.web_ok,
                self.tenant_ready,
                self.sandbox_configured,
                self.sandbox_exit_code == 0,
                bool(self.sandbox_output_uri),
                bool(self.sandbox_output_storage_object_id),
                self.sandbox_output_download_bytes > 0,
                self.sandbox_session_destroyed,
                self.sandbox_destroy_status_confirmed,
                self.sandbox_post_destroy_command_blocked,
                bool(self.browser_screenshot_storage_object_id),
                self.browser_screenshot_download_bytes > 0,
                self.browser_session_listed,
                self.browser_tenant_session_scope_enforced,
                self.browser_session_read_scope_enforced,
                self.browser_session_delete_scope_enforced,
                bool(self.browser_extract_text),
            ]
        )

    def _strict_model_ready(self) -> bool:
        return all(
            [
                self.model_gateway_configured,
                self.execute_status_code == 200,
                self.execute_code is None,
                self.run_status == "succeeded",
                self.model_artifact_required_name_found,
                self.model_artifact_storage_object_count > 0,
                self.model_artifact_total_download_bytes > 0,
                bool(self.model_artifact_storage_object_id),
                self.model_artifact_download_bytes > 0,
                self.model_artifact_required_text_found,
                self.model_sandbox_command_event_seen,
                self.model_artifact_promoted_event_seen,
                self.model_run_event_payload_safe,
                self.model_sandbox_command_exit_code == 0,
                bool(self.model_sandbox_command_output_uri),
                bool(self.model_sandbox_command_output_storage_object_id),
                self.model_artifact_event_matches_storage_object,
                self.model_runtime_state_status == "succeeded",
                bool(self.model_runtime_sandbox_session_id),
                self.model_runtime_completed_step_count > 0,
                self.model_runtime_promoted_artifact_path_count > 0,
                self.model_runtime_required_artifact_path_found,
                self.model_trace_span_count > 0,
                self.model_trace_event_count > 0,
                self.model_trace_billing_meter_count > 0,
                self.model_trace_audit_event_count > 0,
                self.model_trace_runtime_tool_call_seen,
                self.model_trace_billing_tool_call_seen,
                self.model_trace_audit_tool_executed_seen,
                self.model_trace_payload_safe,
            ]
        )

    def _workspace_execution_ready(self) -> bool:
        return all(
            [
                self.browser_workspace_submit_text is not None,
                self.browser_workspace_evidence_summary == "Artifact delivery proven",
                self.browser_workspace_delivery_summary is not None,
                self.browser_workspace_delivery_chain_status
                == "Delivery chain complete",
                bool(self.browser_workspace_delivery_chain_run_id),
                bool(self.browser_workspace_delivery_chain_sandbox_session_id),
                bool(
                    self.browser_workspace_delivery_chain_artifact_storage_object_id
                ),
                bool(
                    self.browser_workspace_delivery_chain_terminal_storage_object_id
                ),
                self.browser_workspace_event_integrity_status
                == "Event stream verified",
                self.browser_workspace_trace_status_text == "Loaded",
                self.browser_workspace_trace_error_text == "No error",
                bool(self.browser_workspace_artifact_preview_text),
                bool(self.browser_workspace_artifact_preview_storage_object_id),
                bool(self.browser_workspace_artifact_download_storage_object_id),
                self.browser_workspace_artifact_download_status is not None,
                bool(self.browser_workspace_artifact_downloaded_storage_object_id),
                bool(self.browser_workspace_terminal_text),
                bool(self.browser_workspace_terminal_output_storage_object_id),
                self.browser_workspace_feedback_api_seen,
            ]
        )

    def _skill_reuse_ready(self) -> bool:
        return all(
            [
                self.browser_workspace_solution_pack_install_api_seen,
                self.browser_workspace_solution_pack_install_skill_count > 0,
                self.browser_workspace_skill_run_api_status == "succeeded",
                self.browser_workspace_skill_run_artifact_count > 0,
                self.browser_workspace_skill_run_artifact_download_bytes > 0,
                self.browser_workspace_skill_run_required_text_found,
                self.browser_workspace_skill_invocation_event_seen,
                self.browser_workspace_skill_invocation_event_matches_skill,
                self.browser_workspace_skill_run_sandbox_command_event_seen,
                self.browser_workspace_skill_run_artifact_promoted_event_seen,
                self.browser_workspace_skill_run_event_payload_safe,
                self.browser_workspace_skill_runtime_state_status == "succeeded",
                bool(self.browser_workspace_skill_runtime_sandbox_session_id),
                self.browser_workspace_skill_runtime_required_artifact_path_found,
                self.browser_workspace_skill_trace_runtime_tool_call_seen,
                self.browser_workspace_skill_trace_billing_tool_call_seen,
                self.browser_workspace_skill_trace_audit_tool_executed_seen,
                self.browser_workspace_skill_trace_payload_safe,
                self.browser_workspace_skill_history_selection_trace_status
                == "Loaded",
                self.browser_workspace_skill_history_selection_delivery_chain_status
                == "Delivery chain complete",
                self.browser_workspace_skill_history_selection_runtime_state_status
                == "succeeded",
                self.browser_workspace_skill_history_selection_download_status
                is not None,
                self.browser_workspace_skill_history_selection_feedback_api_seen,
                self.solution_pack_reuse_marketplace_visible,
                self.solution_pack_reuse_workspace_installed,
                self.solution_pack_reuse_invocation_ready,
                not self.solution_pack_reuse_missing_required_scopes,
            ]
        )

    def _demo_readiness_summary(
        self,
        *,
        workspace_required: bool,
        skill_required: bool,
    ) -> str:
        if self.demo_ready and workspace_required:
            if skill_required:
                return "strict workspace demo ready"
            return "strict workspace execution ready"
        if self.demo_ready:
            return "strict API demo ready"
        if self.local_smoke_ready and not self.model_gateway_configured:
            return "local smoke ready; model gateway missing"
        if self.local_smoke_ready:
            return "local smoke ready; strict execution incomplete"
        return "local smoke incomplete"


class LocalCloudPocSolutionPackReuseVerification(BaseModel):
    version: str | None = None
    skill_id: str | None = None
    version_count: int = 0
    marketplace_visible: bool = False
    workspace_installed: bool = False
    invocation_ready: bool = False
    missing_required_scopes: list[str] = Field(default_factory=list)


class LocalCloudPocBrowserWorkspaceVerification(BaseModel):
    text: str | None = None
    bootstrap_status: str | None = None
    bootstrap_tenant_id: str | None = None
    bootstrap_user_id: str | None = None
    bootstrap_workspace_id: str | None = None
    bootstrap_token_cleared: bool = False
    auth_status: str | None = None
    readiness_status: str | None = None
    readiness_model: str | None = None
    readiness_sandbox: str | None = None
    submit_text: str | None = None
    execution_model_route: str | None = None
    evidence_summary: str | None = None
    delivery_summary: str | None = None
    delivery_chain_status: str | None = None
    delivery_chain_run_id: str | None = None
    delivery_chain_sandbox_session_id: str | None = None
    delivery_chain_artifact_storage_object_id: str | None = None
    delivery_chain_terminal_storage_object_id: str | None = None
    delivery_chain_browser_storage_object_id: str | None = None
    event_integrity_status: str | None = None
    event_integrity_count: str | None = None
    event_integrity_sequence: str | None = None
    event_integrity_closure: str | None = None
    trace_status_text: str | None = None
    trace_span_count_text: str | None = None
    trace_event_count_text: str | None = None
    trace_billing_count_text: str | None = None
    trace_audit_count_text: str | None = None
    trace_error_text: str | None = None
    browser_storage_object_id: str | None = None
    browser_preview_storage_object_id: str | None = None
    artifact_preview_text: str | None = None
    artifact_preview_storage_object_id: str | None = None
    artifact_download_storage_object_id: str | None = None
    artifact_download_status: str | None = None
    artifact_downloaded_storage_object_id: str | None = None
    terminal_text: str | None = None
    terminal_output_storage_object_id: str | None = None
    feedback_status: str | None = None
    feedback_api_seen: bool = False
    feedback_rating: int | None = None
    missing_skill_feedback_status: str | None = None
    missing_skill_feedback_api_count: int = 0
    candidate_status: str | None = None
    eval_candidate_api_count: int = 0
    eval_candidate_review_api_count: int = 0
    pack_candidate_status: str | None = None
    pack_candidate_api_count: int = 0
    pack_candidate_review_api_count: int = 0
    draft_status: str | None = None
    draft_api_status: str | None = None
    draft_api_applied: bool = False
    solution_pack_install_status: str | None = None
    solution_pack_install_api_seen: bool = False
    solution_pack_install_skill_count: int = 0
    skill_invoke_status: str | None = None
    skill_run_status: str | None = None
    skill_run_id: str | None = None
    skill_run_api_status: str | None = None
    skill_run_artifact_count: int = 0
    skill_run_artifact_download_bytes: int = 0
    skill_run_required_text_found: bool = False
    skill_invocation_event_seen: bool = False
    skill_invocation_event_matches_skill: bool = False
    skill_run_sandbox_command_event_seen: bool = False
    skill_run_artifact_promoted_event_seen: bool = False
    skill_run_event_payload_safe: bool = False
    skill_runtime_state_status: str | None = None
    skill_runtime_sandbox_session_id: str | None = None
    skill_runtime_required_artifact_path_found: bool = False
    skill_trace_span_count: int = 0
    skill_trace_event_count: int = 0
    skill_trace_billing_meter_count: int = 0
    skill_trace_audit_event_count: int = 0
    skill_trace_runtime_tool_call_seen: bool = False
    skill_trace_billing_tool_call_seen: bool = False
    skill_trace_audit_tool_executed_seen: bool = False
    skill_trace_payload_safe: bool = False
    skill_trace_status_text: str | None = None
    skill_trace_span_count_text: str | None = None
    skill_trace_event_count_text: str | None = None
    skill_trace_billing_count_text: str | None = None
    skill_trace_audit_count_text: str | None = None
    skill_trace_error_text: str | None = None
    skill_run_history_status: str | None = None
    skill_run_history_text: str | None = None
    skill_history_selection_trace_status: str | None = None
    skill_history_selection_delivery_summary: str | None = None
    skill_history_selection_delivery_chain_status: str | None = None
    skill_history_selection_delivery_chain_run_id: str | None = None
    skill_history_selection_delivery_chain_sandbox_session: str | None = None
    skill_history_selection_delivery_chain_artifact_storage: str | None = None
    skill_history_selection_delivery_chain_terminal_storage: str | None = None
    skill_history_selection_event_integrity_status: str | None = None
    skill_history_selection_event_integrity_count: str | None = None
    skill_history_selection_event_integrity_sequence: str | None = None
    skill_history_selection_event_integrity_closure: str | None = None
    skill_history_selection_terminal_text: str | None = None
    skill_history_selection_terminal_output_storage_object_id: str | None = None
    skill_history_selection_artifact_preview_text: str | None = None
    skill_history_selection_previewed_storage_object_id: str | None = None
    skill_history_selection_runtime_state_status: str | None = None
    skill_history_selection_runtime_sandbox_session: str | None = None
    skill_history_selection_runtime_artifact_count: str | None = None
    skill_history_selection_execution_summary: str | None = None
    skill_history_selection_execution_model_route: str | None = None
    skill_history_selection_execution_sandbox: str | None = None
    skill_history_selection_execution_artifact: str | None = None
    skill_history_selection_download_storage_object_id: str | None = None
    skill_history_selection_download_status: str | None = None
    skill_history_selection_downloaded_storage_object_id: str | None = None
    skill_history_selection_feedback_status: str | None = None
    skill_history_selection_feedback_api_seen: bool = False
    skill_history_selection_feedback_rating: int | None = None
    skill_evidence_summary: str | None = None
    skill_delivery_summary: str | None = None
    skill_artifact_preview_text: str | None = None


class LocalCloudPocHttpClient:
    def __init__(self, timeout_seconds: int = 30):
        self.timeout_seconds = timeout_seconds
        self.opener = build_opener(ProxyHandler({}))

    def request(
        self,
        method: str,
        url: str,
        payload: dict | None = None,
        headers: dict | None = None,
    ) -> LocalCloudPocHttpResponse:
        data = None
        request_headers = dict(headers or {})
        if payload is not None:
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request = Request(url, data=data, headers=request_headers, method=method)
        try:
            with self.opener.open(request, timeout=self.timeout_seconds) as response:
                body_bytes = response.read()
                return LocalCloudPocHttpResponse(
                    status_code=response.status,
                    body=body_bytes.decode("utf-8", errors="replace"),
                    body_bytes=body_bytes,
                )
        except HTTPError as error:
            body_bytes = error.read()
            return LocalCloudPocHttpResponse(
                status_code=error.code,
                body=body_bytes.decode("utf-8", errors="replace"),
                body_bytes=body_bytes,
            )
        except (TimeoutError, URLError) as error:
            raise RuntimeError(f"local cloud PoC verifier request failed: {error}") from error


def parse_args(argv: list[str] | None = None) -> LocalCloudPocVerificationConfig:
    parser = argparse.ArgumentParser(
        description="Verify local cloud PoC API, web, browser, auth, and sandbox flow."
    )
    parser.add_argument("--api-base-url", default="http://localhost:8000")
    parser.add_argument("--browser-base-url", default="http://localhost:8001")
    parser.add_argument(
        "--browser-controller-api-key",
        default=os.environ.get("TAROAI_BROWSER_CONTROLLER_API_KEY", ""),
    )
    parser.add_argument("--web-base-url", default="http://localhost:3000")
    parser.add_argument(
        "--bootstrap-token",
        default=os.environ.get("TAROAI_TENANT_BOOTSTRAP_TOKEN", ""),
    )
    parser.add_argument("--tenant-slug", default="acme")
    parser.add_argument("--owner-email", default="owner@example.com")
    parser.add_argument("--owner-display-name", default="Owner")
    parser.add_argument("--owner-password", default="correct horse battery staple")
    parser.add_argument("--run-message", default=DEFAULT_RUN_MESSAGE)
    parser.add_argument("--sandbox-command", default="python --version")
    parser.add_argument("--browser-denied-tenant-id", default="tenant_browser_verify_denied")
    parser.add_argument("--browser-smoke-text", default="Browser smoke OK")
    parser.add_argument("--browser-workspace-url", default=None)
    parser.add_argument("--browser-workspace-api-base-url", default=None)
    parser.add_argument(
        "--browser-workspace-auth-poll-interval-seconds",
        type=float,
        default=0.25,
    )
    parser.add_argument("--browser-workspace-submit-message", default=None)
    parser.add_argument(
        "--browser-workspace-submit-expected-text",
        default=None,
    )
    parser.add_argument(
        "--browser-workspace-submit-poll-interval-seconds",
        type=float,
        default=0.25,
    )
    parser.add_argument(
        "--browser-workspace-submit-poll-attempts",
        type=int,
        default=30,
    )
    parser.add_argument(
        "--browser-workspace-missing-skill-name",
        default="ERP invoice reconciliation",
    )
    parser.add_argument(
        "--browser-workspace-missing-skill-comment",
        default="Need this repeated workflow in a reusable solution pack.",
    )
    parser.add_argument(
        "--browser-workspace-solution-pack-id",
        default="sales.renewal_ops",
    )
    parser.add_argument(
        "--browser-workspace-missing-skill-feedback-count",
        type=int,
        default=3,
    )
    parser.add_argument(
        "--browser-workspace-draft-skill-name",
        default="ERP Invoice Matching",
    )
    parser.add_argument(
        "--browser-workspace-draft-summary",
        default="Add governed invoice matching skill draft.",
    )
    parser.add_argument(
        "--browser-workspace-draft-pack-version",
        default="1.0.1",
    )
    parser.add_argument(
        "--browser-workspace-draft-skill-manifest-json",
        default=json.dumps([DEFAULT_DRAFT_SKILL_MANIFEST], indent=2),
    )
    parser.add_argument("--timeout-seconds", type=int, default=30)
    parser.add_argument("--run-status-poll-attempts", type=int, default=5)
    parser.add_argument("--run-status-poll-interval-seconds", type=float, default=1.0)
    parser.add_argument("--require-model-execution", action="store_true")
    parser.add_argument("--model-artifact-required-name", default="report.md")
    parser.add_argument(
        "--model-artifact-required-text",
        default="local cloud PoC execution path",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Write redacted verification JSON to this file instead of stdout.",
    )
    parsed = parser.parse_args(argv)
    browser_workspace_submit_expected_text = (
        parsed.browser_workspace_submit_expected_text
    )
    if browser_workspace_submit_expected_text is None:
        if parsed.require_model_execution:
            browser_workspace_submit_expected_text = "succeeded"
        else:
            browser_workspace_submit_expected_text = (
                "model gateway model is not configured"
            )
    return LocalCloudPocVerificationConfig(
        api_base_url=parsed.api_base_url,
        browser_base_url=parsed.browser_base_url,
        browser_controller_api_key=parsed.browser_controller_api_key,
        web_base_url=parsed.web_base_url,
        bootstrap_token=parsed.bootstrap_token,
        tenant_slug=parsed.tenant_slug,
        owner_email=parsed.owner_email,
        owner_display_name=parsed.owner_display_name,
        owner_password=parsed.owner_password,
        run_message=parsed.run_message,
        sandbox_command=parsed.sandbox_command,
        browser_denied_tenant_id=parsed.browser_denied_tenant_id,
        browser_smoke_text=parsed.browser_smoke_text,
        browser_workspace_url=parsed.browser_workspace_url,
        browser_workspace_api_base_url=parsed.browser_workspace_api_base_url,
        browser_workspace_auth_poll_interval_seconds=(
            parsed.browser_workspace_auth_poll_interval_seconds
        ),
        browser_workspace_submit_message=parsed.browser_workspace_submit_message,
        browser_workspace_submit_expected_text=(
            browser_workspace_submit_expected_text
        ),
        browser_workspace_submit_poll_interval_seconds=(
            parsed.browser_workspace_submit_poll_interval_seconds
        ),
        browser_workspace_submit_poll_attempts=(
            parsed.browser_workspace_submit_poll_attempts
        ),
        browser_workspace_missing_skill_name=(
            parsed.browser_workspace_missing_skill_name
        ),
        browser_workspace_missing_skill_comment=(
            parsed.browser_workspace_missing_skill_comment
        ),
        browser_workspace_solution_pack_id=parsed.browser_workspace_solution_pack_id,
        browser_workspace_missing_skill_feedback_count=(
            parsed.browser_workspace_missing_skill_feedback_count
        ),
        browser_workspace_draft_skill_name=(
            parsed.browser_workspace_draft_skill_name
        ),
        browser_workspace_draft_summary=parsed.browser_workspace_draft_summary,
        browser_workspace_draft_pack_version=(
            parsed.browser_workspace_draft_pack_version
        ),
        browser_workspace_draft_skill_manifest_json=(
            parsed.browser_workspace_draft_skill_manifest_json
        ),
        timeout_seconds=parsed.timeout_seconds,
        run_status_poll_attempts=parsed.run_status_poll_attempts,
        run_status_poll_interval_seconds=parsed.run_status_poll_interval_seconds,
        require_model_execution=parsed.require_model_execution,
        model_artifact_required_name=parsed.model_artifact_required_name,
        model_artifact_required_text=parsed.model_artifact_required_text,
        output_path=parsed.output,
    )


def validate_http_url(url: str, field_name: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field_name} must be an HTTP URL")


def redacted_url_for_result(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return value
    hostname = parsed.hostname
    if not hostname:
        netloc = parsed.netloc.rsplit("@", 1)[-1]
    else:
        host = (
            f"[{hostname}]"
            if ":" in hostname and not hostname.startswith("[")
            else hostname
        )
        try:
            port = parsed.port
        except ValueError:
            port = None
        netloc = host if port is None else f"{host}:{port}"
    return parsed._replace(
        netloc=netloc,
        params="",
        query="",
        fragment="",
    ).geturl()


def browser_controller_headers(
    config: LocalCloudPocVerificationConfig,
) -> dict[str, str]:
    api_key = config.browser_controller_api_key.strip()
    if not api_key:
        return {}
    return {"Authorization": f"Bearer {api_key}"}


def verify_browser_controller_auth_challenge(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
) -> bool:
    return all(inspect_browser_controller_auth_challenge(client, config).values())


def inspect_browser_controller_auth_challenge(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
) -> dict[str, bool]:
    if not config.browser_controller_api_key.strip():
        return {
            "browser_controller_auth_tenant_session_list_challenge_enforced": False,
            "browser_controller_auth_global_session_list_challenge_enforced": False,
            "browser_controller_auth_capabilities_challenge_enforced": False,
        }
    session_response = request_json(
        client,
        "GET",
        config.browser_base_url,
        "/sessions?tenant_id=taroai_auth_probe",
        headers={},
    )
    if session_response.status_code not in {401, 403}:
        raise RuntimeError(
            "browser controller did not reject unauthenticated requests: "
            f"HTTP {session_response.status_code}"
        )
    tenant_session_list_rejected = True
    global_session_response = request_json(
        client,
        "GET",
        config.browser_base_url,
        "/sessions",
        headers={},
    )
    if global_session_response.status_code not in {401, 403}:
        raise RuntimeError(
            "browser controller did not reject unauthenticated global session list requests: "
            f"HTTP {global_session_response.status_code}"
        )
    global_session_list_rejected = True
    capabilities_response = request_json(
        client,
        "GET",
        config.browser_base_url,
        "/capabilities",
        headers={},
    )
    if capabilities_response.status_code not in {401, 403}:
        raise RuntimeError(
            "browser controller did not reject unauthenticated capabilities requests: "
            f"HTTP {capabilities_response.status_code}"
        )
    return {
        "browser_controller_auth_tenant_session_list_challenge_enforced": (
            tenant_session_list_rejected
        ),
        "browser_controller_auth_global_session_list_challenge_enforced": (
            global_session_list_rejected
        ),
        "browser_controller_auth_capabilities_challenge_enforced": True,
    }


def verify_browser_controller_capabilities(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
) -> dict[str, int | bool]:
    response = request_json(
        client,
        "GET",
        config.browser_base_url,
        "/capabilities",
        headers=browser_controller_headers(config),
    )
    assert_status(
        response,
        {200},
        "browser controller capabilities check failed",
    )
    body = response.json_body()
    provider = str(body.get("provider") or "")
    auth_required = bool(body.get("auth_required"))
    session_ttl_enforced = bool(body.get("session_ttl_enforced"))
    max_session_ttl_seconds = int(body.get("max_session_ttl_seconds") or 0)
    max_sessions = int(body.get("max_sessions") or 0)
    max_sessions_per_tenant = int(body.get("max_sessions_per_tenant") or 0)
    max_sessions_per_run = int(body.get("max_sessions_per_run") or 0)
    navigation_allowlist_enforced = bool(
        body.get("navigation_allowlist_enforced")
    )
    navigation_allowed_host_count = int(
        body.get("navigation_allowed_host_count") or 0
    )
    missing = []
    if not provider:
        missing.append("provider")
    if config.browser_controller_api_key.strip() and not auth_required:
        missing.append("auth_required")
    if not session_ttl_enforced:
        missing.append("session_ttl_enforced")
    if max_session_ttl_seconds <= 0:
        missing.append("max_session_ttl_seconds")
    if max_sessions <= 0:
        missing.append("max_sessions")
    if max_sessions_per_tenant <= 0:
        missing.append("max_sessions_per_tenant")
    if max_sessions_per_run <= 0:
        missing.append("max_sessions_per_run")
    if missing:
        raise RuntimeError(
            "browser controller capabilities are not ready: "
            + ", ".join(missing)
        )
    return {
        "browser_controller_capabilities_checked": True,
        "browser_controller_auth_required": auth_required,
        "browser_controller_session_ttl_enforced": session_ttl_enforced,
        "browser_controller_max_session_ttl_seconds": max_session_ttl_seconds,
        "browser_controller_max_sessions": max_sessions,
        "browser_controller_max_sessions_per_tenant": max_sessions_per_tenant,
        "browser_controller_max_sessions_per_run": max_sessions_per_run,
        "browser_controller_navigation_allowlist_enforced": (
            navigation_allowlist_enforced
        ),
        "browser_controller_navigation_allowed_host_count": (
            navigation_allowed_host_count
        ),
    }


def sandbox_readiness_capabilities(
    sandbox_readiness: dict[str, Any],
) -> dict[str, int | bool]:
    return {
        "sandbox_capabilities_checked": bool(
            sandbox_readiness.get("capabilities_checked")
        ),
        "sandbox_network_isolation_declared": bool(
            sandbox_readiness.get("network_isolation_declared")
        ),
        "sandbox_filesystem_isolation_declared": bool(
            sandbox_readiness.get("filesystem_isolation_declared")
        ),
        "sandbox_resource_limits_declared": bool(
            sandbox_readiness.get("resource_limits_declared")
        ),
        "sandbox_destroy_supported_declared": bool(
            sandbox_readiness.get("destroy_supported_declared")
        ),
        "sandbox_session_ttl_enforced_declared": bool(
            sandbox_readiness.get("session_ttl_enforced_declared")
        ),
        "sandbox_runtime_isolation_declared": bool(
            sandbox_readiness.get("runtime_isolation_declared")
        ),
        "sandbox_image_policy_enforced_declared": bool(
            sandbox_readiness.get("image_policy_enforced_declared")
        ),
        "sandbox_allowed_image_count": int(
            sandbox_readiness.get("allowed_image_count") or 0
        ),
        "sandbox_max_session_ttl_seconds": int(
            sandbox_readiness.get("max_session_ttl_seconds") or 0
        ),
        "sandbox_max_sessions": int(sandbox_readiness.get("max_sessions") or 0),
        "sandbox_max_sessions_per_tenant": int(
            sandbox_readiness.get("max_sessions_per_tenant") or 0
        ),
        "sandbox_max_sessions_per_run": int(
            sandbox_readiness.get("max_sessions_per_run") or 0
        ),
    }


def verify_local_cloud_poc(
    config: LocalCloudPocVerificationConfig,
    client: LocalCloudPocHttpClient | None = None,
) -> LocalCloudPocVerificationResult:
    http_client = client or LocalCloudPocHttpClient(timeout_seconds=config.timeout_seconds)
    api_health = request_json(http_client, "GET", config.api_base_url, "/healthz")
    assert_status(api_health, {200}, "API health check failed")
    ready = request_json(http_client, "GET", config.api_base_url, "/readyz")
    assert_status(ready, {200}, "API readiness check failed")
    readiness_body = ready.json_body()
    model_gateway = readiness_body.get("checks", {}).get("model_gateway", {})
    model_gateway_configured = bool(model_gateway.get("configured"))
    model_gateway_missing = list(model_gateway.get("missing") or [])
    sandbox_readiness = readiness_body.get("checks", {}).get("sandbox")
    if not isinstance(sandbox_readiness, dict):
        sandbox_readiness = {}
    sandbox_configured = bool(sandbox_readiness.get("configured"))
    sandbox_provider = str(sandbox_readiness.get("provider") or "unknown")
    sandbox_missing = list(sandbox_readiness.get("missing") or [])
    sandbox_capabilities = sandbox_readiness_capabilities(sandbox_readiness)
    if not sandbox_configured:
        if not sandbox_missing:
            sandbox_missing.append("checks.sandbox")
        missing = ", ".join(str(item) for item in sandbox_missing)
        raise RuntimeError(
            "sandbox is not configured for local cloud PoC execution: "
            f"missing {missing}"
        )
    if config.require_model_execution and not model_gateway_configured:
        missing = ", ".join(str(item) for item in model_gateway_missing) or "unknown"
        raise RuntimeError(
            "model gateway is not configured for strict model execution: "
            f"missing {missing}"
        )

    browser_health = request_json(
        http_client,
        "GET",
        config.browser_base_url,
        "/healthz",
        headers=browser_controller_headers(config),
    )
    assert_status(browser_health, {200}, "browser controller health check failed")
    browser_controller_auth_evidence = inspect_browser_controller_auth_challenge(
        http_client,
        config,
    )
    browser_controller_auth_enforced = all(browser_controller_auth_evidence.values())
    browser_controller_capabilities = verify_browser_controller_capabilities(
        http_client,
        config,
    )
    web_ok = verify_web(http_client, config)
    bootstrap = bootstrap_tenant(http_client, config)
    access_token = login(http_client, config, bootstrap["tenant_id"])
    auth_headers = {"Authorization": f"Bearer {access_token}"}
    tenant_readiness = request_json(
        http_client,
        "GET",
        config.api_base_url,
        "/api/tenants/current/readiness",
        headers=auth_headers,
    )
    assert_status(tenant_readiness, {200}, "tenant readiness check failed")
    tenant_ready = bool(tenant_readiness.json_body().get("ready"))
    if not tenant_ready:
        raise RuntimeError("tenant readiness check did not pass")
    if config.require_model_execution and config.browser_workspace_url is not None:
        ensure_solution_pack_for_draft(http_client, config, auth_headers)

    run = create_run(http_client, config, bootstrap["starter_workspace_id"], auth_headers)
    execute = execute_run(
        http_client,
        config,
        run["run_id"],
        auth_headers,
        model_gateway_configured,
    )
    model_execution = verify_model_execution(
        http_client,
        config,
        run["run_id"],
        auth_headers,
        model_gateway_configured,
    )
    sandbox = verify_sandbox(
        http_client,
        config,
        bootstrap["starter_workspace_id"],
        run["run_id"],
        auth_headers,
    )
    browser_text = ""
    browser_session_scope = {
        "browser_session_listed": False,
        "browser_tenant_session_scope_enforced": False,
        "browser_session_read_scope_enforced": False,
        "browser_session_delete_scope_enforced": False,
    }
    browser_workspace = LocalCloudPocBrowserWorkspaceVerification()
    solution_pack_reuse = LocalCloudPocSolutionPackReuseVerification()
    browser_session_opened = False
    try:
        open_browser_session(
            http_client,
            config,
            bootstrap["tenant_id"],
            bootstrap["starter_workspace_id"],
            run["run_id"],
        )
        browser_session_opened = True
        browser_session_scope = verify_browser_session_scope(
            http_client,
            config,
            bootstrap["tenant_id"],
            bootstrap["starter_workspace_id"],
            run["run_id"],
        )
        browser_text = verify_browser(
            http_client,
            config,
            bootstrap["tenant_id"],
            bootstrap["starter_workspace_id"],
            run["run_id"],
        )
        browser_workspace = verify_browser_workspace(
            http_client,
            config,
            bootstrap["tenant_id"],
            bootstrap["owner_user_id"],
            bootstrap["starter_workspace_id"],
            run["run_id"],
            auth_headers,
            expected_run_id=run["run_id"],
            expected_sandbox_session_id=model_execution[
                "model_runtime_sandbox_session_id"
            ],
            expected_artifact_storage_object_id=model_execution[
                "model_artifact_storage_object_id"
            ],
            expected_terminal_storage_object_id=model_execution[
                "model_sandbox_command_output_storage_object_id"
            ],
            expected_terminal_output_uri=model_execution[
                "model_sandbox_command_output_uri"
            ],
            expected_browser_storage_object_id=model_execution[
                "model_browser_action_storage_object_id"
            ],
            expected_execution_model_route=model_execution[
                "model_execution_route_label"
            ],
            expected_event_count=len(model_execution["model_run_event_types"]),
            expected_event_sequence_label=event_sequence_label(
                model_execution["model_run_event_sequences"]
            ),
            expected_event_closure_label=model_execution[
                "model_run_event_closure_label"
            ],
            expected_trace_span_count=model_execution["model_trace_span_count"],
            expected_trace_event_count=model_execution["model_trace_event_count"],
            expected_trace_billing_count=model_execution[
                "model_trace_billing_meter_count"
            ],
            expected_trace_audit_count=model_execution["model_trace_audit_event_count"],
        )
        if browser_workspace.draft_status == "Draft applied":
            solution_pack_reuse = verify_solution_pack_reuse(
                http_client,
                config,
                bootstrap["starter_workspace_id"],
                auth_headers,
            )
    finally:
        if browser_session_opened:
            delete_browser_session(
                http_client,
                config,
                bootstrap["tenant_id"],
                bootstrap["starter_workspace_id"],
                run["run_id"],
            )
    return LocalCloudPocVerificationResult(
        api_base_url=redacted_url_for_result(config.api_base_url),
        browser_base_url=redacted_url_for_result(config.browser_base_url),
        web_base_url=(
            redacted_url_for_result(config.web_base_url)
            if config.web_base_url is not None
            else None
        ),
        api_health_ok=True,
        browser_health_ok=True,
        browser_controller_auth_enforced=browser_controller_auth_enforced,
        **browser_controller_auth_evidence,
        **browser_controller_capabilities,
        web_ok=web_ok,
        tenant_id=bootstrap["tenant_id"],
        owner_user_id=bootstrap["owner_user_id"],
        tenant_ready=tenant_ready,
        model_gateway_configured=model_gateway_configured,
        model_gateway_missing=model_gateway_missing,
        sandbox_configured=sandbox_configured,
        sandbox_provider=sandbox_provider,
        sandbox_missing=sandbox_missing,
        **sandbox_capabilities,
        run_id=run["run_id"],
        execute_status_code=execute.status_code,
        execute_code=execute.json_body().get("code") if execute.body else None,
        run_status=model_execution["run_status"],
        artifact_count=model_execution["artifact_count"],
        artifact_names=model_execution["artifact_names"],
        model_artifact_required_name_found=model_execution[
            "model_artifact_required_name_found"
        ],
        model_artifact_storage_object_count=model_execution[
            "model_artifact_storage_object_count"
        ],
        model_artifact_total_download_bytes=model_execution[
            "model_artifact_total_download_bytes"
        ],
        model_artifact_storage_object_id=model_execution[
            "model_artifact_storage_object_id"
        ],
        model_artifact_download_bytes=model_execution["model_artifact_download_bytes"],
        model_artifact_required_text_found=model_execution[
            "model_artifact_required_text_found"
        ],
        model_run_event_types=model_execution["model_run_event_types"],
        model_sandbox_command_event_seen=model_execution[
            "model_sandbox_command_event_seen"
        ],
        model_artifact_promoted_event_seen=model_execution[
            "model_artifact_promoted_event_seen"
        ],
        model_run_event_payload_safe=model_execution["model_run_event_payload_safe"],
        model_sandbox_command_exit_code=model_execution[
            "model_sandbox_command_exit_code"
        ],
        model_sandbox_command_output_uri=model_execution[
            "model_sandbox_command_output_uri"
        ],
        model_sandbox_command_output_storage_object_id=model_execution[
            "model_sandbox_command_output_storage_object_id"
        ],
        model_browser_action_storage_object_id=model_execution[
            "model_browser_action_storage_object_id"
        ],
        model_artifact_promoted_storage_object_id=model_execution[
            "model_artifact_promoted_storage_object_id"
        ],
        model_artifact_event_matches_storage_object=model_execution[
            "model_artifact_event_matches_storage_object"
        ],
        model_runtime_state_status=model_execution["model_runtime_state_status"],
        model_runtime_sandbox_session_id=model_execution[
            "model_runtime_sandbox_session_id"
        ],
        model_runtime_browser_session_id=model_execution[
            "model_runtime_browser_session_id"
        ],
        model_runtime_completed_step_count=model_execution[
            "model_runtime_completed_step_count"
        ],
        model_runtime_promoted_artifact_path_count=model_execution[
            "model_runtime_promoted_artifact_path_count"
        ],
        model_runtime_required_artifact_path_found=model_execution[
            "model_runtime_required_artifact_path_found"
        ],
        model_trace_span_count=model_execution["model_trace_span_count"],
        model_trace_event_count=model_execution["model_trace_event_count"],
        model_trace_billing_meter_count=model_execution[
            "model_trace_billing_meter_count"
        ],
        model_trace_audit_event_count=model_execution["model_trace_audit_event_count"],
        model_trace_runtime_tool_call_seen=model_execution[
            "model_trace_runtime_tool_call_seen"
        ],
        model_trace_billing_tool_call_seen=model_execution[
            "model_trace_billing_tool_call_seen"
        ],
        model_trace_audit_tool_executed_seen=model_execution[
            "model_trace_audit_tool_executed_seen"
        ],
        model_trace_payload_safe=model_execution["model_trace_payload_safe"],
        sandbox_session_id=sandbox["session_id"],
        sandbox_exit_code=sandbox["exit_code"],
        sandbox_output_uri=sandbox["output_uri"],
        sandbox_output_storage_object_id=sandbox["sandbox_output_storage_object_id"],
        sandbox_output_download_bytes=sandbox["sandbox_output_download_bytes"],
        sandbox_session_destroyed=sandbox["sandbox_session_destroyed"],
        sandbox_destroy_status_confirmed=sandbox["sandbox_destroy_status_confirmed"],
        sandbox_post_destroy_command_blocked=sandbox[
            "sandbox_post_destroy_command_blocked"
        ],
        browser_screenshot_uri=sandbox["browser_screenshot_uri"],
        browser_screenshot_storage_object_id=sandbox[
            "browser_screenshot_storage_object_id"
        ],
        browser_screenshot_download_bytes=sandbox[
            "browser_screenshot_download_bytes"
        ],
        browser_session_id=config.browser_session_id,
        browser_session_listed=browser_session_scope["browser_session_listed"],
        browser_tenant_session_scope_enforced=browser_session_scope[
            "browser_tenant_session_scope_enforced"
        ],
        browser_session_read_scope_enforced=browser_session_scope[
            "browser_session_read_scope_enforced"
        ],
        browser_session_delete_scope_enforced=browser_session_scope[
            "browser_session_delete_scope_enforced"
        ],
        browser_extract_text=browser_text,
        browser_workspace_text=browser_workspace.text,
        browser_workspace_bootstrap_status=browser_workspace.bootstrap_status,
        browser_workspace_bootstrap_tenant_id=browser_workspace.bootstrap_tenant_id,
        browser_workspace_bootstrap_user_id=browser_workspace.bootstrap_user_id,
        browser_workspace_bootstrap_workspace_id=(
            browser_workspace.bootstrap_workspace_id
        ),
        browser_workspace_bootstrap_token_cleared=(
            browser_workspace.bootstrap_token_cleared
        ),
        browser_workspace_auth_status=browser_workspace.auth_status,
        browser_workspace_readiness_status=browser_workspace.readiness_status,
        browser_workspace_readiness_model=browser_workspace.readiness_model,
        browser_workspace_readiness_sandbox=browser_workspace.readiness_sandbox,
        browser_workspace_submit_text=browser_workspace.submit_text,
        browser_workspace_execution_model_route=(
            browser_workspace.execution_model_route
        ),
        browser_workspace_evidence_summary=browser_workspace.evidence_summary,
        browser_workspace_delivery_summary=browser_workspace.delivery_summary,
        browser_workspace_delivery_chain_status=(
            browser_workspace.delivery_chain_status
        ),
        browser_workspace_delivery_chain_run_id=browser_workspace.delivery_chain_run_id,
        browser_workspace_delivery_chain_sandbox_session_id=(
            browser_workspace.delivery_chain_sandbox_session_id
        ),
        browser_workspace_delivery_chain_artifact_storage_object_id=(
            browser_workspace.delivery_chain_artifact_storage_object_id
        ),
        browser_workspace_delivery_chain_terminal_storage_object_id=(
            browser_workspace.delivery_chain_terminal_storage_object_id
        ),
        browser_workspace_delivery_chain_browser_storage_object_id=(
            browser_workspace.delivery_chain_browser_storage_object_id
        ),
        browser_workspace_event_integrity_status=(
            browser_workspace.event_integrity_status
        ),
        browser_workspace_event_integrity_count=(
            browser_workspace.event_integrity_count
        ),
        browser_workspace_event_integrity_sequence=(
            browser_workspace.event_integrity_sequence
        ),
        browser_workspace_event_integrity_closure=(
            browser_workspace.event_integrity_closure
        ),
        browser_workspace_trace_status_text=browser_workspace.trace_status_text,
        browser_workspace_trace_span_count_text=(
            browser_workspace.trace_span_count_text
        ),
        browser_workspace_trace_event_count_text=(
            browser_workspace.trace_event_count_text
        ),
        browser_workspace_trace_billing_count_text=(
            browser_workspace.trace_billing_count_text
        ),
        browser_workspace_trace_audit_count_text=(
            browser_workspace.trace_audit_count_text
        ),
        browser_workspace_trace_error_text=browser_workspace.trace_error_text,
        browser_workspace_browser_storage_object_id=(
            browser_workspace.browser_storage_object_id
        ),
        browser_workspace_browser_preview_storage_object_id=(
            browser_workspace.browser_preview_storage_object_id
        ),
        browser_workspace_artifact_preview_text=(
            browser_workspace.artifact_preview_text
        ),
        browser_workspace_artifact_preview_storage_object_id=(
            browser_workspace.artifact_preview_storage_object_id
        ),
        browser_workspace_artifact_download_storage_object_id=(
            browser_workspace.artifact_download_storage_object_id
        ),
        browser_workspace_artifact_download_status=(
            browser_workspace.artifact_download_status
        ),
        browser_workspace_artifact_downloaded_storage_object_id=(
            browser_workspace.artifact_downloaded_storage_object_id
        ),
        browser_workspace_terminal_text=browser_workspace.terminal_text,
        browser_workspace_terminal_output_storage_object_id=(
            browser_workspace.terminal_output_storage_object_id
        ),
        browser_workspace_feedback_status=browser_workspace.feedback_status,
        browser_workspace_feedback_api_seen=browser_workspace.feedback_api_seen,
        browser_workspace_feedback_rating=browser_workspace.feedback_rating,
        browser_workspace_missing_skill_feedback_status=(
            browser_workspace.missing_skill_feedback_status
        ),
        browser_workspace_missing_skill_feedback_api_count=(
            browser_workspace.missing_skill_feedback_api_count
        ),
        browser_workspace_candidate_status=browser_workspace.candidate_status,
        browser_workspace_eval_candidate_api_count=(
            browser_workspace.eval_candidate_api_count
        ),
        browser_workspace_eval_candidate_review_api_count=(
            browser_workspace.eval_candidate_review_api_count
        ),
        browser_workspace_pack_candidate_status=(
            browser_workspace.pack_candidate_status
        ),
        browser_workspace_pack_candidate_api_count=(
            browser_workspace.pack_candidate_api_count
        ),
        browser_workspace_pack_candidate_review_api_count=(
            browser_workspace.pack_candidate_review_api_count
        ),
        browser_workspace_draft_status=browser_workspace.draft_status,
        browser_workspace_draft_api_status=browser_workspace.draft_api_status,
        browser_workspace_draft_api_applied=browser_workspace.draft_api_applied,
        browser_workspace_solution_pack_install_status=(
            browser_workspace.solution_pack_install_status
        ),
        browser_workspace_solution_pack_install_api_seen=(
            browser_workspace.solution_pack_install_api_seen
        ),
        browser_workspace_solution_pack_install_skill_count=(
            browser_workspace.solution_pack_install_skill_count
        ),
        browser_workspace_skill_invoke_status=browser_workspace.skill_invoke_status,
        browser_workspace_skill_run_status=browser_workspace.skill_run_status,
        browser_workspace_skill_run_id=browser_workspace.skill_run_id,
        browser_workspace_skill_run_api_status=browser_workspace.skill_run_api_status,
        browser_workspace_skill_run_artifact_count=(
            browser_workspace.skill_run_artifact_count
        ),
        browser_workspace_skill_run_artifact_download_bytes=(
            browser_workspace.skill_run_artifact_download_bytes
        ),
        browser_workspace_skill_run_required_text_found=(
            browser_workspace.skill_run_required_text_found
        ),
        browser_workspace_skill_invocation_event_seen=(
            browser_workspace.skill_invocation_event_seen
        ),
        browser_workspace_skill_invocation_event_matches_skill=(
            browser_workspace.skill_invocation_event_matches_skill
        ),
        browser_workspace_skill_run_sandbox_command_event_seen=(
            browser_workspace.skill_run_sandbox_command_event_seen
        ),
        browser_workspace_skill_run_artifact_promoted_event_seen=(
            browser_workspace.skill_run_artifact_promoted_event_seen
        ),
        browser_workspace_skill_run_event_payload_safe=(
            browser_workspace.skill_run_event_payload_safe
        ),
        browser_workspace_skill_runtime_state_status=(
            browser_workspace.skill_runtime_state_status
        ),
        browser_workspace_skill_runtime_sandbox_session_id=(
            browser_workspace.skill_runtime_sandbox_session_id
        ),
        browser_workspace_skill_runtime_required_artifact_path_found=(
            browser_workspace.skill_runtime_required_artifact_path_found
        ),
        browser_workspace_skill_trace_span_count=(
            browser_workspace.skill_trace_span_count
        ),
        browser_workspace_skill_trace_event_count=(
            browser_workspace.skill_trace_event_count
        ),
        browser_workspace_skill_trace_billing_meter_count=(
            browser_workspace.skill_trace_billing_meter_count
        ),
        browser_workspace_skill_trace_audit_event_count=(
            browser_workspace.skill_trace_audit_event_count
        ),
        browser_workspace_skill_trace_runtime_tool_call_seen=(
            browser_workspace.skill_trace_runtime_tool_call_seen
        ),
        browser_workspace_skill_trace_billing_tool_call_seen=(
            browser_workspace.skill_trace_billing_tool_call_seen
        ),
        browser_workspace_skill_trace_audit_tool_executed_seen=(
            browser_workspace.skill_trace_audit_tool_executed_seen
        ),
        browser_workspace_skill_trace_payload_safe=(
            browser_workspace.skill_trace_payload_safe
        ),
        browser_workspace_skill_trace_status_text=(
            browser_workspace.skill_trace_status_text
        ),
        browser_workspace_skill_trace_span_count_text=(
            browser_workspace.skill_trace_span_count_text
        ),
        browser_workspace_skill_trace_event_count_text=(
            browser_workspace.skill_trace_event_count_text
        ),
        browser_workspace_skill_trace_billing_count_text=(
            browser_workspace.skill_trace_billing_count_text
        ),
        browser_workspace_skill_trace_audit_count_text=(
            browser_workspace.skill_trace_audit_count_text
        ),
        browser_workspace_skill_trace_error_text=(
            browser_workspace.skill_trace_error_text
        ),
        browser_workspace_skill_run_history_status=(
            browser_workspace.skill_run_history_status
        ),
        browser_workspace_skill_run_history_text=(
            browser_workspace.skill_run_history_text
        ),
        browser_workspace_skill_history_selection_trace_status=(
            browser_workspace.skill_history_selection_trace_status
        ),
        browser_workspace_skill_history_selection_delivery_summary=(
            browser_workspace.skill_history_selection_delivery_summary
        ),
        browser_workspace_skill_history_selection_delivery_chain_status=(
            browser_workspace.skill_history_selection_delivery_chain_status
        ),
        browser_workspace_skill_history_selection_delivery_chain_run_id=(
            browser_workspace.skill_history_selection_delivery_chain_run_id
        ),
        browser_workspace_skill_history_selection_delivery_chain_sandbox_session=(
            browser_workspace.skill_history_selection_delivery_chain_sandbox_session
        ),
        browser_workspace_skill_history_selection_delivery_chain_artifact_storage=(
            browser_workspace.skill_history_selection_delivery_chain_artifact_storage
        ),
        browser_workspace_skill_history_selection_delivery_chain_terminal_storage=(
            browser_workspace.skill_history_selection_delivery_chain_terminal_storage
        ),
        browser_workspace_skill_history_selection_event_integrity_status=(
            browser_workspace.skill_history_selection_event_integrity_status
        ),
        browser_workspace_skill_history_selection_event_integrity_count=(
            browser_workspace.skill_history_selection_event_integrity_count
        ),
        browser_workspace_skill_history_selection_event_integrity_sequence=(
            browser_workspace.skill_history_selection_event_integrity_sequence
        ),
        browser_workspace_skill_history_selection_event_integrity_closure=(
            browser_workspace.skill_history_selection_event_integrity_closure
        ),
        browser_workspace_skill_history_selection_terminal_text=(
            browser_workspace.skill_history_selection_terminal_text
        ),
        browser_workspace_skill_history_selection_terminal_output_storage_object_id=(
            browser_workspace.skill_history_selection_terminal_output_storage_object_id
        ),
        browser_workspace_skill_history_selection_artifact_preview_text=(
            browser_workspace.skill_history_selection_artifact_preview_text
        ),
        browser_workspace_skill_history_selection_previewed_storage_object_id=(
            browser_workspace.skill_history_selection_previewed_storage_object_id
        ),
        browser_workspace_skill_history_selection_runtime_state_status=(
            browser_workspace.skill_history_selection_runtime_state_status
        ),
        browser_workspace_skill_history_selection_runtime_sandbox_session=(
            browser_workspace.skill_history_selection_runtime_sandbox_session
        ),
        browser_workspace_skill_history_selection_runtime_artifact_count=(
            browser_workspace.skill_history_selection_runtime_artifact_count
        ),
        browser_workspace_skill_history_selection_execution_summary=(
            browser_workspace.skill_history_selection_execution_summary
        ),
        browser_workspace_skill_history_selection_execution_model_route=(
            browser_workspace.skill_history_selection_execution_model_route
        ),
        browser_workspace_skill_history_selection_execution_sandbox=(
            browser_workspace.skill_history_selection_execution_sandbox
        ),
        browser_workspace_skill_history_selection_execution_artifact=(
            browser_workspace.skill_history_selection_execution_artifact
        ),
        browser_workspace_skill_history_selection_download_storage_object_id=(
            browser_workspace.skill_history_selection_download_storage_object_id
        ),
        browser_workspace_skill_history_selection_download_status=(
            browser_workspace.skill_history_selection_download_status
        ),
        browser_workspace_skill_history_selection_downloaded_storage_object_id=(
            browser_workspace.skill_history_selection_downloaded_storage_object_id
        ),
        browser_workspace_skill_history_selection_feedback_status=(
            browser_workspace.skill_history_selection_feedback_status
        ),
        browser_workspace_skill_history_selection_feedback_api_seen=(
            browser_workspace.skill_history_selection_feedback_api_seen
        ),
        browser_workspace_skill_history_selection_feedback_rating=(
            browser_workspace.skill_history_selection_feedback_rating
        ),
        browser_workspace_skill_evidence_summary=(
            browser_workspace.skill_evidence_summary
        ),
        browser_workspace_skill_delivery_summary=(
            browser_workspace.skill_delivery_summary
        ),
        browser_workspace_skill_artifact_preview_text=(
            browser_workspace.skill_artifact_preview_text
        ),
        solution_pack_reuse_version=solution_pack_reuse.version,
        solution_pack_reuse_skill_id=solution_pack_reuse.skill_id,
        solution_pack_reuse_version_count=solution_pack_reuse.version_count,
        solution_pack_reuse_marketplace_visible=(
            solution_pack_reuse.marketplace_visible
        ),
        solution_pack_reuse_workspace_installed=(
            solution_pack_reuse.workspace_installed
        ),
        solution_pack_reuse_invocation_ready=solution_pack_reuse.invocation_ready,
        solution_pack_reuse_missing_required_scopes=(
            solution_pack_reuse.missing_required_scopes
        ),
    )


def verify_web(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
) -> bool:
    if config.web_base_url is None:
        return False
    response = request_text(client, "GET", config.web_base_url, "/")
    assert_status(response, {200}, "web workspace check failed")
    require_text_fragments(
        response.body,
        {
            "title": "Taroai Workspace",
            "chat column": 'data-testid="chat-column"',
            "CREAO-compatible composer hint": (
                "Press Enter to send, Shift+Enter for a new line."
            ),
            "login email input": 'id="login-email"',
            "login password input": 'id="login-password"',
            "tenant bootstrap slug input": 'id="tenant-slug"',
            "tenant bootstrap owner input": 'id="owner-display-name"',
            "tenant bootstrap token input": 'id="bootstrap-token"',
            "tenant bootstrap button": 'id="bootstrap-login-button"',
            "tenant bootstrap status": "data-bootstrap-status",
            "login button": 'id="login-button"',
            "logout button": 'id="logout-button"',
            "auth status": "data-auth-status",
            "readiness status": "data-readiness-status",
            "readiness model": "data-readiness-model",
            "readiness sandbox": "data-readiness-sandbox",
            "run controls": 'data-testid="run-controls"',
            "run control status": "data-run-control-status",
            "cancel run button": 'id="cancel-run-button"',
            "retry run button": 'id="retry-run-button"',
            "run history panel": 'data-testid="run-history"',
            "run history status": "data-run-history-status",
            "run history list": "data-run-history-list",
            "run history refresh": "data-run-history-refresh",
            "browser storage object": "data-browser-storage-object",
            "browser preview storage object": "data-browser-preview-storage-object",
            "browser preview storage object id": (
                "data-browser-preview-storage-object-id"
            ),
            "artifact download status": "data-artifact-download-status",
            "artifact download state": "data-download-state",
            "artifact downloaded storage object": (
                "data-artifact-downloaded-storage-object"
            ),
            "artifact downloaded storage object id": (
                "data-download-storage-object-id"
            ),
            "artifact preview status": "data-artifact-preview-status",
            "artifact preview title": "data-artifact-preview-title",
            "artifact preview storage object": (
                "data-artifact-preview-storage-object"
            ),
            "artifact preview storage object id": "data-preview-storage-object-id",
            "artifact preview content": "data-artifact-preview-content",
            "run feedback controls": "data-run-feedback-panel",
            "run feedback status": "data-run-feedback-status",
            "run positive feedback button": 'id="run-feedback-positive"',
            "run negative feedback button": 'id="run-feedback-negative"',
            "solution pack panel": 'data-testid="solution-pack-panel"',
            "solution pack status": "data-solution-pack-status",
            "solution pack list": "data-solution-pack-list",
            "solution pack refresh": "data-solution-pack-refresh",
            "solution pack install button": 'id="install-solution-pack-button"',
            "solution pack install status": "data-solution-pack-install-status",
            "workspace skills panel": 'data-testid="workspace-skills-panel"',
            "workspace skills status": "data-skills-status",
            "workspace skills list": "data-skills-list",
            "workspace skills refresh": "data-skills-refresh",
            "skill invoke input": 'id="skill-invoke-input"',
            "skill invoke button": 'id="invoke-skill-button"',
            "skill invoke status": "data-skill-invoke-status",
            "missing skill feedback status": "data-cs-missing-skill-status",
            "missing skill feedback name": 'id="cs-missing-skill-name"',
            "missing skill feedback comment": 'id="cs-missing-skill-comment"',
            "missing skill feedback solution pack": 'id="cs-missing-skill-solution-pack"',
            "missing skill feedback button": 'id="cs-submit-missing-skill"',
            "candidate generation controls": "data-cs-candidate-action-status",
            "candidate generation eval button": 'id="cs-create-eval-candidates"',
            "candidate generation pack button": 'id="cs-create-pack-candidates"',
            "eval candidate review selected": "data-cs-eval-candidate-selected",
            "eval candidate accept button": 'id="cs-accept-eval-candidate"',
            "eval candidate reject button": 'id="cs-reject-eval-candidate"',
            "pack candidate review selected": "data-cs-pack-candidate-selected",
            "pack candidate accept button": 'id="cs-accept-pack-candidate"',
            "pack candidate reject button": 'id="cs-reject-pack-candidate"',
            "trace panel": 'data-testid="run-trace"',
            "trace status": "data-trace-status",
            "trace span count": "data-trace-span-count",
            "trace event count": "data-trace-event-count",
            "trace billing count": "data-trace-billing-count",
            "trace audit count": "data-trace-audit-count",
            "trace error classification": "data-trace-error-classification",
            "trace list": "data-trace-list",
            "runtime state panel": 'data-testid="runtime-state"',
            "runtime state status": "data-runtime-state-status",
            "runtime current step": "data-runtime-current-step",
            "runtime completed count": "data-runtime-completed-count",
            "runtime sandbox session": "data-runtime-sandbox-session",
            "runtime browser session": "data-runtime-browser-session",
            "runtime artifact count": "data-runtime-artifact-count",
            "execution loop": 'data-testid="execution-loop"',
            "execution summary": "data-execution-summary",
            "execution model route": "data-execution-model-route",
            "execution run": "data-execution-run",
            "execution plan": "data-execution-plan",
            "execution sandbox": "data-execution-sandbox",
            "execution browser": "data-execution-browser",
            "execution artifact": "data-execution-artifact",
            "approval panel": 'data-testid="approval-panel"',
            "approval status": "data-approval-status",
            "approval copy": "data-approval-copy",
            "approval resolution": "data-approval-resolution",
            "approval resolution state": "data-resolution-state",
            "approval approve button": 'id="approve-button"',
            "approval reject button": 'id="reject-button"',
            "workspace input": 'id="workspace-id"',
            "workspace script": "./assets/main.js",
            "delivery summary": "data-delivery-summary",
            "sandbox terminal": 'data-testid="sandbox-terminal"',
            "terminal status": "data-terminal-status",
            "terminal output": "data-terminal-output",
            "terminal output storage object": "data-terminal-output-storage-object",
            "terminal output storage object id": "data-terminal-storage-object-id",
            "delivery chain panel": 'data-testid="delivery-chain"',
            "delivery chain status": "data-delivery-chain-status",
            "delivery chain state": "data-delivery-chain-state",
            "delivery chain run": "data-delivery-chain-run",
            "delivery chain sandbox": "data-delivery-chain-sandbox",
            "delivery chain artifact storage": "data-delivery-chain-artifact-storage",
            "delivery chain terminal storage": "data-delivery-chain-terminal-storage",
            "delivery chain browser storage": "data-delivery-chain-browser-storage",
            "event integrity panel": 'data-testid="event-integrity"',
            "event integrity status": "data-event-integrity-status",
            "event integrity state": "data-event-integrity-state",
            "event integrity count": "data-event-integrity-count",
            "event integrity sequence": "data-event-integrity-sequence",
            "event integrity closure": "data-event-integrity-closure",
        },
        "web workspace response",
    )
    script = request_text(client, "GET", config.web_base_url, "/assets/main.js")
    assert_status(script, {200}, "web workspace script check failed")
    require_text_fragments(
        script.body,
        {
            "login endpoint": '"/api/auth/login"',
            "readiness endpoint": '"/readyz"',
            "model readiness": "model_gateway",
            "sandbox readiness": "sandbox",
            "readiness missing list": "missing.join",
            "URL config prefill": "applyUrlConfiguration();",
            "URL config params": "new URLSearchParams(window.location.search)",
            "URL config API base persistence": 'apiBase: "taroai.apiBase"',
            "URL config tenant persistence": 'tenantId: "taroai.tenantId"',
            "URL config user persistence": 'userId: "taroai.userId"',
            "URL config workspace persistence": 'workspaceId: "taroai.workspaceId"',
            "URL config email persistence": 'email: "taroai.authEmail"',
            "URL config state assignment": "state[key] = value",
            "URL secret access token scrub": 'urlParams.delete("accessToken")',
            "URL secret password scrub": 'urlParams.delete("password")',
            "URL secret history scrub": "window.history.replaceState",
            "tenant bootstrap endpoint": '"/api/tenants/bootstrap"',
            "tenant bootstrap token header": '"X-Bootstrap-Token"',
            "tenant bootstrap slug": "tenant_slug: state.tenantSlug",
            "tenant bootstrap owner name": (
                "owner_display_name: state.ownerDisplayName"
            ),
            "tenant bootstrap owner password": (
                "owner_password: elements.loginPassword.value"
            ),
            "tenant bootstrap workspace sync": "result.starter_workspace_id",
            "tenant bootstrap token clear": 'elements.bootstrapToken.value = ""',
            "tenant bootstrap slug persistence": (
                'localStorage.setItem("taroai.tenantSlug"'
            ),
            "tenant bootstrap owner persistence": (
                'localStorage.setItem("taroai.ownerDisplayName"'
            ),
            "tenant bootstrap action": "bootstrapTenant()",
            "authorization header": '"Authorization"',
            "bearer token": '"Bearer "',
            "tenant login sync": "result.tenant_id",
            "tenant persistence": 'localStorage.setItem("taroai.tenantId"',
            "user login sync": "result.user_id",
            "user persistence": 'localStorage.setItem("taroai.userId"',
            "workspace state": "state.workspaceId",
            "workspace persistence": 'localStorage.setItem("taroai.workspaceId"',
            "auth failure state clear": (
                'clearAuthenticatedWorkspaceState("Authentication failed.");'
            ),
            "auth expiry handler": "handleAuthExpired(response.status)",
            "auth expiry status check": "status === 401 || status === 403",
            "auth expiry state clear": (
                'clearAuthenticatedWorkspaceState("Authentication expired.");'
            ),
            "auth expiry auth status": 'renderAuth("Auth required")',
            "response body parser": "parseResponseBody(text)",
            "response body plain text fallback": "return { message: text }",
            "storage content auth error handler": "raiseStorageFetchError(response)",
            "logout state clear": "clearAuthenticatedWorkspaceState()",
            "logout run clear": "state.currentRunId = null",
            "logout storage clear": "state.storageObjects = []",
            "logout terminal message": 'terminalMessage = "Signed out."',
            "logout terminal clear": "renderTerminal(terminalMessage)",
            "logout conversation reset": "resetConversation()",
            "logout conversation replacement": "elements.conversation.replaceChildren",
            "cancel run endpoint": '`/api/runs/${state.currentRunId}/cancel`',
            "retry run endpoint": '`/api/runs/${state.currentRunId}/retry`',
            "run controls renderer": "renderRunControls()",
            "run history loader": "loadRunHistory()",
            "run history renderer": "renderRunHistory(",
            "run history selector": "selectRunFromHistory(",
            "run history item": "data-run-history-id",
            "browser preview storage object renderer": (
                "renderBrowserPreviewStorageObject("
            ),
            "browser preview storage object element": "browserPreviewStorageObject",
            "browser preview storage object state": (
                "dataset.browserPreviewStorageObjectId"
            ),
            "artifact preview action": "previewArtifact(",
            "artifact preview renderer": "renderArtifactPreview(",
            "artifact preview storage object": "data-preview-storage-object-id",
            "artifact preview storage object element": "artifactPreviewStorageObject",
            "artifact preview storage object state": "dataset.previewStorageObjectId",
            "artifact download renderer": "renderArtifactDownloadStatus(",
            "artifact downloaded storage object": "artifactDownloadedStorageObject",
            "artifact downloaded storage object state": (
                "dataset.downloadStorageObjectId"
            ),
            "artifact download state": "dataset.downloadState",
            "trace loader": "loadRunTrace()",
            "trace renderer": "renderRunTrace(",
            "trace endpoint": "`/api/runs/${state.currentRunId}/trace`",
            "runtime state loader": "loadRuntimeState()",
            "runtime state renderer": "renderRuntimeState(",
            "runtime state endpoint": "`/api/runs/${state.currentRunId}/state`",
            "terminal safe output": "safeTerminalOutput(",
            "terminal output storage object": "terminalOutputStorageObject",
            "terminal output storage object state": "dataset.terminalStorageObjectId",
            "terminal output storage object resolver": (
                "storageObjectForTerminalOutputUri("
            ),
            "execution loop renderer": "renderExecutionLoop()",
            "execution loop summary": "executionLoopSummary",
            "execution loop plan": "elements.executionLoopPlan",
            "evidence renderer": "renderRunEvidence()",
            "evidence checklist": "buildRunEvidenceItems(",
            "evidence state": "data-evidence-status",
            "session token storage": "sessionStorage.setItem",
            "session token removal": "sessionStorage.removeItem",
            "delivery summary renderer": "renderDeliverySummary()",
            "delivery summary element": "elements.deliverySummary",
            "delivery summary state": "dataset.deliveryState",
            "downloadable artifacts": "downloadableArtifacts()",
            "delivery chain renderer": "renderDeliveryChain()",
            "delivery chain evidence": "buildDeliveryChainEvidence()",
            "delivery chain status element": "deliveryChainStatus",
            "delivery chain run element": "deliveryChainRun",
            "delivery chain sandbox element": "deliveryChainSandbox",
            "delivery chain artifact storage element": "deliveryChainArtifactStorage",
            "delivery chain terminal storage element": "deliveryChainTerminalStorage",
            "delivery chain browser storage element": "deliveryChainBrowserStorage",
            "event integrity renderer": "renderEventIntegrity()",
            "event integrity evidence": "buildEventIntegrityEvidence()",
            "event integrity status element": "eventIntegrityStatus",
            "event integrity sequence element": "eventIntegritySequence",
            "event integrity closure element": "eventIntegrityClosure",
            "event stream sequence evidence": "event stream sequence",
            "event stream integrity issue state": "eventStreamIntegrityIssues",
            "event stream integrity recorder": (
                "recordEventStreamIntegrityIssues(newEvents)"
            ),
            "event stream identity merge": "eventIdentity(event)",
            "event stream duplicate guard": "eventAlreadyLoaded(event)",
            "event stream ordered merge": "compareEventsBySequence",
            "event stream sorted state": "state.events.sort(compareEventsBySequence)",
            "event stream cursor update": "lastFiniteEventSequence(state.events)",
            "event stream sequence normalizer": "eventSequence(event)",
            "event stream missing sequence guard": "eventsMissingSequence",
            "event stream missing sequence issue": (
                "event stream sequence is missing"
            ),
            "event stream nonmonotonic issue": (
                "incoming event stream sequence is not monotonic"
            ),
            "SSE event type parser": "eventLineType",
            "SSE event id parser": "eventLineId",
            "SSE multiline data parser": "dataLines",
            "SSE multiline data join": "dataLines.join",
            "SSE event type fallback": "parsed.type = parsed.type || eventLineType",
            "SSE event id fallback": "parsed.id = parsed.id || eventLineId",
            "storage-backed artifacts": "readyStorageBackedArtifacts()",
            "artifact auto preview": "autoPreviewFirstDeliveredArtifact(",
            "artifact preview retry state": "previewedRunIds",
            "artifact preview retry guard": "state.previewedRunIds.has(state.currentRunId)",
            "artifact preview success latch": "state.previewedRunIds.add(state.currentRunId)",
            "run feedback submission": "submitRunFeedback(",
            "run feedback renderer": "renderRunFeedback(",
            "run feedback state": "feedbackSubmittedRunIds",
            "run feedback endpoint": '"/api/customer-success/feedback"',
            "run feedback type": 'feedback_type: "thumbs_rating"',
            "run feedback target": 'target_type: "run"',
            "run feedback artifact count": "artifact_count: readyArtifacts.length",
            "approval pending state": "state.pendingApprovalId",
            "approval approve endpoint": (
                "`/api/runs/${state.currentRunId}/approvals`"
            ),
            "approval reject endpoint": (
                "`/api/runs/${state.currentRunId}/approvals/reject`"
            ),
            "approval latest event": "latestApprovalEvent(",
            "approval resolution element": "approvalResolution",
            "approval resolution renderer": "renderApprovalResolution(",
            "approval resolution parts": "approvalResolutionParts(",
            "approval resolution id evidence": "payload.approval_id",
            "approval resolution actor evidence": "payload.resolved_by_user_id",
            "approval resolved event": 'event.type === "approval.resolved"',
            "approval rejected event": 'event.type === "approval.rejected"',
            "solution pack loader": "loadSolutionPacks()",
            "solution pack renderer": "renderSolutionPacks(",
            "solution pack list endpoint": 'apiFetch("/api/solution-packs")',
            "solution pack selector": "data-solution-pack-id",
            "solution pack install action": "installSelectedSolutionPack()",
            "solution pack install endpoint": (
                "`/api/solution-packs/${encodeURIComponent(pack.manifest.id)}/install`"
            ),
            "solution pack install workspace": "workspace_ids: [state.workspaceId]",
            "workspace skills loader": "loadWorkspaceSkills()",
            "workspace skills renderer": "renderWorkspaceSkills(",
            "workspace skills endpoint": (
                "`/api/workspaces/${encodeURIComponent(state.workspaceId)}/skills`"
            ),
            "workspace skill readiness": "invocation_ready",
            "workspace skill missing scopes": "missing_required_scopes",
            "workspace skill selector": "data-workspace-skill-id",
            "workspace skill invoke": "invokeSelectedWorkspaceSkill()",
            "workspace skill invoke endpoint": (
                "`/api/workspaces/${encodeURIComponent(state.workspaceId)}/skills/${encodeURIComponent(skill.skill_id)}/invoke`"
            ),
            "missing skill feedback": "submitMissingSkillFeedback(",
            "missing skill feedback status": "customerSuccessMissingSkillStatus",
            "missing skill feedback type": 'feedback_type: "missing_skill"',
            "missing skill feedback target": 'target_type: "solution_pack"',
            "missing skill feedback pack": "solution_pack_id: solutionPackId",
            "missing skill feedback name": "missing_skill_name: missingSkillName",
            "missing skill feedback source": 'source: "workspace_skill_request"',
            "candidate generation": "createCustomerSuccessEvaluationCandidates(",
            "pack candidate generation": "createCustomerSuccessSolutionPackCandidates(",
            "candidate generation status": "customerSuccessCandidateStatus",
            "candidate generation eval endpoint": '"/api/customer-success/evaluation-candidates"',
            "candidate generation pack endpoint": '"/api/customer-success/solution-pack-candidates"',
            "candidate generation repeated threshold": "minimum_repeated_feedback: 3",
            "eval candidate review": "reviewSelectedEvaluationCandidate(",
            "eval candidate review payload": "evaluationCandidateReviewPayload(",
            "eval candidate accept status": 'status: "accepted"',
            "eval candidate reject status": 'status: "rejected"',
            "eval candidate review endpoint": (
                "`/api/customer-success/evaluation-candidates/${candidate.id}/review`"
            ),
            "pack candidate review": "reviewSelectedSolutionPackCandidate(",
            "pack candidate review renderer": "renderSolutionPackCandidateReview(",
            "pack candidate selector": "selectedSolutionPackCandidate(",
            "pack candidate review payload": "solutionPackCandidateReviewPayload(",
            "pack candidate review endpoint": (
                "`/api/customer-success/solution-pack-candidates/${candidate.id}/review`"
            ),
            "pack candidate accepted status": "Pack candidate accepted",
            "pack candidate draft id": "publication_draft_id",
        },
        "web workspace script",
    )
    reject_text_fragments(
        script.body,
        {
            "raw sandbox command stream": "latest.stdout ||",
            "raw sandbox command error stream": "latest.stderr ||",
            "raw sandbox command renderer": "[stdout, stderr]",
            "raw sandbox command payload spread": "...payload",
        },
        "web workspace script",
    )
    return True


def require_text_fragments(
    body: str,
    fragments: dict[str, str],
    context: str,
) -> None:
    for label, fragment in fragments.items():
        if fragment not in body:
            raise RuntimeError(f"{context} did not include {label}")


def reject_text_fragments(
    body: str,
    fragments: dict[str, str],
    context: str,
) -> None:
    for label, fragment in fragments.items():
        if fragment in body:
            raise RuntimeError(f"{context} included {label}")


def bootstrap_tenant(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
) -> dict[str, Any]:
    response = request_json(
        client,
        "POST",
        config.api_base_url,
        "/api/tenants/bootstrap",
        payload={
            "tenant_slug": config.tenant_slug,
            "owner_email": config.owner_email,
            "owner_display_name": config.owner_display_name,
            "owner_password": config.owner_password,
        },
        headers={"X-Bootstrap-Token": config.bootstrap_token},
    )
    assert_status(response, {200, 201}, "tenant bootstrap failed")
    body = response.json_body()
    if not body.get("readiness", {}).get("ready"):
        raise RuntimeError("tenant bootstrap readiness did not pass")
    return body


def login(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    tenant_id: str,
) -> str:
    response = request_json(
        client,
        "POST",
        config.api_base_url,
        "/api/auth/login",
        payload={
            "tenant_id": tenant_id,
            "email": config.owner_email,
            "password": config.owner_password,
        },
    )
    assert_status(response, {200}, "owner login failed")
    access_token = response.json_body().get("access_token")
    if not access_token:
        raise RuntimeError("owner login did not return an access token")
    return str(access_token)


def ensure_solution_pack_for_draft(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    headers: dict[str, str],
) -> None:
    response = request_text(
        client,
        "GET",
        config.api_base_url,
        "/api/solution-packs",
        headers=headers,
    )
    assert_status(response, {200}, "solution pack list failed")
    if solution_pack_list_contains(response.json_value(), config.browser_workspace_solution_pack_id):
        return
    created = request_json(
        client,
        "POST",
        config.api_base_url,
        "/api/solution-packs",
        payload=solution_pack_manifest_payload(config),
        headers=headers,
    )
    assert_status(created, {201}, "solution pack registration failed")


def solution_pack_list_contains(value: Any, pack_id: str) -> bool:
    if isinstance(value, dict):
        value = value.get("items", [])
    if not isinstance(value, list):
        return False
    for item in value:
        if not isinstance(item, dict):
            continue
        manifest = item.get("manifest")
        if isinstance(manifest, dict) and manifest.get("id") == pack_id:
            return True
        if item.get("id") == pack_id:
            return True
    return False


def solution_pack_manifest_payload(config: LocalCloudPocVerificationConfig) -> dict[str, Any]:
    return {
        "id": config.browser_workspace_solution_pack_id,
        "version": "1.0.0",
        "name": "Renewal Operations",
        "description": "Starter solution pack for local cloud PoC skill evolution.",
        "industry": "sales",
        "use_cases": ["renewal operations"],
        "skills": [],
        "success_metrics": ["reusable_skill_requests_resolved"],
        "rollout_checklist": ["Review generated skill before publishing."],
    }


def verify_solution_pack_reuse(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    workspace_id: str,
    headers: dict[str, str],
) -> LocalCloudPocSolutionPackReuseVerification:
    skill_ids = draft_skill_ids(config)
    if not skill_ids:
        raise RuntimeError("solution pack reuse verification has no draft skill ids")
    skill_id = skill_ids[0]
    pack_path = quote(config.browser_workspace_solution_pack_id, safe="")
    workspace_path = quote(workspace_id, safe="")
    skill_path = quote(skill_id, safe="")

    versions = request_list(
        client,
        "GET",
        config.api_base_url,
        f"/api/solution-packs/{pack_path}/versions",
        headers,
        "solution pack version evidence",
    )
    version_count = len(versions)
    if not solution_pack_entries_contain_skill(
        versions,
        config.browser_workspace_solution_pack_id,
        config.browser_workspace_draft_pack_version,
        skill_id,
    ):
        raise RuntimeError("solution pack applied version did not include draft skill")

    packs = request_list(
        client,
        "GET",
        config.api_base_url,
        "/api/solution-packs",
        headers,
        "solution pack marketplace evidence",
    )
    pack_visible = solution_pack_entries_contain_skill(
        packs,
        config.browser_workspace_solution_pack_id,
        config.browser_workspace_draft_pack_version,
        skill_id,
        required_status="published",
    )
    if not pack_visible:
        raise RuntimeError("solution pack marketplace did not expose applied version")

    install = request_json(
        client,
        "POST",
        config.api_base_url,
        f"/api/solution-packs/{pack_path}/install",
        payload={"workspace_ids": [workspace_id]},
        headers=headers,
    )
    assert_status(install, {200, 201}, "solution pack reuse install failed")
    install_body = install.json_body()
    if install_body.get("version") != config.browser_workspace_draft_pack_version:
        raise RuntimeError("solution pack install did not use applied version")
    installed_skill_ids = list(install_body.get("installed_skill_ids") or [])
    if skill_id not in installed_skill_ids:
        raise RuntimeError("solution pack install did not install draft skill")

    installations = request_list(
        client,
        "GET",
        config.api_base_url,
        "/api/solution-pack-installations",
        headers,
        "solution pack installation evidence",
    )
    installation_visible = any(
        item.get("pack_id") == config.browser_workspace_solution_pack_id
        and item.get("version") == config.browser_workspace_draft_pack_version
        and skill_id in list(item.get("installed_skill_ids") or [])
        and workspace_id in list(item.get("workspace_ids") or [])
        for item in installations
    )
    if not installation_visible:
        raise RuntimeError("solution pack installation evidence did not include draft skill")

    workspace_skills = request_list(
        client,
        "GET",
        config.api_base_url,
        f"/api/workspaces/{workspace_path}/skills",
        headers,
        "workspace skill installation evidence",
    )
    workspace_skill = next(
        (item for item in workspace_skills if item.get("skill_id") == skill_id),
        None,
    )
    workspace_installed = workspace_skill is not None
    if not workspace_installed:
        raise RuntimeError("workspace skill list did not include draft skill")
    invocation_ready = bool(workspace_skill.get("invocation_ready"))
    missing_required_scopes = [
        str(scope)
        for scope in list(workspace_skill.get("missing_required_scopes") or [])
    ]

    marketplace_skills = request_list(
        client,
        "GET",
        config.api_base_url,
        f"/api/skills?workspace_id={workspace_path}",
        headers,
        "skill marketplace evidence",
    )
    skill_visible = any(
        isinstance(item.get("manifest"), dict)
        and item["manifest"].get("id") == skill_id
        and item.get("status") == "published"
        for item in marketplace_skills
    )
    if not skill_visible:
        skill_entry = request_json(
            client,
            "GET",
            config.api_base_url,
            f"/api/skills/{skill_path}?workspace_id={workspace_path}",
            headers=headers,
        )
        assert_status(skill_entry, {200}, "skill marketplace direct evidence failed")
        body = skill_entry.json_body()
        skill_visible = (
            isinstance(body.get("manifest"), dict)
            and body["manifest"].get("id") == skill_id
            and body.get("status") == "published"
        )
    if not skill_visible:
        raise RuntimeError("skill marketplace did not expose draft skill")

    return LocalCloudPocSolutionPackReuseVerification(
        version=config.browser_workspace_draft_pack_version,
        skill_id=skill_id,
        version_count=version_count,
        marketplace_visible=skill_visible and pack_visible,
        workspace_installed=workspace_installed and installation_visible,
        invocation_ready=invocation_ready,
        missing_required_scopes=missing_required_scopes,
    )


def request_list(
    client: LocalCloudPocHttpClient,
    method: str,
    base_url: str,
    path: str,
    headers: dict[str, str],
    label: str,
) -> list[dict[str, Any]]:
    response = request_text(client, method, base_url, path, headers=headers)
    assert_status(response, {200}, f"{label} failed")
    value = response.json_value()
    if isinstance(value, dict):
        value = value.get("items", [])
    if not isinstance(value, list):
        raise RuntimeError(f"{label} returned an unexpected response shape")
    return [item for item in value if isinstance(item, dict)]


def draft_skill_ids(config: LocalCloudPocVerificationConfig) -> list[str]:
    parsed = json.loads(config.browser_workspace_draft_skill_manifest_json)
    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list):
        raise RuntimeError("solution pack draft skill manifest must be a JSON object or list")
    skill_ids: list[str] = []
    for item in parsed:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            skill_ids.append(item["id"])
    return skill_ids


def solution_pack_entries_contain_skill(
    entries: list[dict[str, Any]],
    pack_id: str,
    version: str,
    skill_id: str,
    required_status: str | None = None,
) -> bool:
    for entry in entries:
        if required_status is not None and entry.get("status") != required_status:
            continue
        manifest = entry.get("manifest")
        if not isinstance(manifest, dict):
            continue
        if manifest.get("id") != pack_id or manifest.get("version") != version:
            continue
        skills = manifest.get("skills")
        if not isinstance(skills, list):
            continue
        if any(isinstance(skill, dict) and skill.get("id") == skill_id for skill in skills):
            return True
    return False


def create_run(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    workspace_id: str,
    headers: dict[str, str],
) -> dict[str, Any]:
    response = request_json(
        client,
        "POST",
        config.api_base_url,
        "/api/runs",
        payload={
            "workspace_id": workspace_id,
            "message": config.run_message,
            "mode": "autonomous",
        },
        headers=headers,
    )
    assert_status(response, {201}, "run creation failed")
    return response.json_body()


def execute_run(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    run_id: str,
    headers: dict[str, str],
    model_gateway_configured: bool,
) -> LocalCloudPocHttpResponse:
    response = request_json(
        client,
        "POST",
        config.api_base_url,
        f"/api/runs/{run_id}/execute",
        headers=headers,
    )
    if model_gateway_configured:
        assert_status(response, {200, 202}, "configured model run execution failed")
        return response
    if config.require_model_execution:
        raise RuntimeError("model gateway is not configured")
    assert_status(response, {503}, "unconfigured model gateway did not fail predictably")
    code = response.json_body().get("code")
    if code != "model_gateway_unavailable":
        raise RuntimeError(f"unexpected unconfigured model gateway error code: {code}")
    return response


def verify_model_execution(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    run_id: str,
    headers: dict[str, str],
    model_gateway_configured: bool,
) -> dict[str, Any]:
    if not model_gateway_configured:
        return {
            "run_status": None,
            "artifact_count": 0,
            "artifact_names": [],
            "model_artifact_required_name_found": False,
            "model_artifact_storage_object_count": 0,
            "model_artifact_total_download_bytes": 0,
            "model_artifact_storage_object_id": None,
            "model_artifact_download_bytes": 0,
            "model_artifact_required_text_found": False,
            "model_run_event_types": [],
            "model_run_event_sequences": [],
            "model_run_event_closure_label": None,
            "model_execution_route_label": None,
            "model_sandbox_command_event_seen": False,
            "model_artifact_promoted_event_seen": False,
            "model_run_event_payload_safe": False,
            "model_sandbox_command_exit_code": None,
            "model_sandbox_command_output_uri": None,
            "model_sandbox_command_output_storage_object_id": None,
            "model_browser_action_storage_object_id": None,
            "model_artifact_promoted_storage_object_id": None,
            "model_artifact_event_matches_storage_object": False,
            "model_runtime_state_status": None,
            "model_runtime_sandbox_session_id": None,
            "model_runtime_browser_session_id": None,
            "model_runtime_completed_step_count": 0,
            "model_runtime_promoted_artifact_path_count": 0,
            "model_runtime_required_artifact_path_found": False,
            "model_trace_span_count": 0,
            "model_trace_event_count": 0,
            "model_trace_billing_meter_count": 0,
            "model_trace_audit_event_count": 0,
            "model_trace_runtime_tool_call_seen": False,
            "model_trace_billing_tool_call_seen": False,
            "model_trace_audit_tool_executed_seen": False,
            "model_trace_payload_safe": False,
        }
    run_status = wait_for_run_success(client, config, run_id, headers)
    artifacts = list_run_artifacts(client, config, run_id, headers)
    if not artifacts:
        raise RuntimeError("configured model run did not publish any artifacts")
    artifact_names = [str(artifact.get("name")) for artifact in artifacts]
    required_name_found = config.model_artifact_required_name in artifact_names
    if not required_name_found:
        raise RuntimeError(
            "configured model run did not publish required artifact: "
            f"{config.model_artifact_required_name}"
        )
    required_artifact = next(
        artifact
        for artifact in artifacts
        if str(artifact.get("name") or "") == config.model_artifact_required_name
    )
    storage_objects = list_run_storage_objects(client, config, run_id, headers)
    artifact_downloads = download_model_artifact_storage_objects(
        client,
        config,
        artifacts,
        storage_objects,
        headers,
    )
    storage_object = find_storage_object_for_artifact(required_artifact, storage_objects)
    if storage_object is None:
        raise RuntimeError(
            "configured model run artifact did not resolve to a storage object"
        )
    storage_object_id = str(storage_object["id"])
    content_bytes = artifact_downloads[storage_object_id]
    content = content_bytes.decode("utf-8", errors="replace")
    required_text_found = config.model_artifact_required_text in content
    if not required_text_found:
        raise RuntimeError(
            "configured model run artifact content did not include required text"
    )
    event_check = inspect_run_events(client, config, run_id, headers)
    require_no_cleanup_failure_events(event_check, "configured model run")
    event_types = event_check["event_types"]
    sandbox_command_event_seen = "sandbox.command.executed" in event_types
    artifact_promoted_event_seen = "sandbox.artifact.promoted" in event_types
    if not sandbox_command_event_seen:
        raise RuntimeError(
            "configured model run did not emit sandbox.command.executed"
        )
    if not artifact_promoted_event_seen:
        raise RuntimeError(
            "configured model run did not emit sandbox.artifact.promoted"
        )
    if not event_check["payload_safe"]:
        raise RuntimeError("run event stream leaked raw sandbox output")
    sandbox_command_exit_code = event_check["sandbox_command_exit_code"]
    if sandbox_command_exit_code != 0:
        raise RuntimeError(
            "configured model run sandbox command event did not report exit_code 0"
        )
    sandbox_command_output_storage_object_id = None
    sandbox_command_output_uri = event_check["sandbox_command_output_uri"]
    if sandbox_command_output_uri is not None:
        sandbox_command_output_storage_object = (
            find_storage_object_for_sandbox_command_output(
                storage_objects,
                sandbox_command_output_uri,
            )
        )
        if sandbox_command_output_storage_object is None:
            raise RuntimeError(
                "configured model run sandbox command output URI did not resolve "
                "to a storage object"
            )
        sandbox_command_output_storage_object_id = str(
            sandbox_command_output_storage_object["id"]
        )
    browser_action_storage_object_id = event_check["browser_action_storage_object_id"]
    if browser_action_storage_object_id is not None:
        browser_storage_object = next(
            (
                storage_object
                for storage_object in storage_objects
                if str(storage_object.get("id") or "")
                == browser_action_storage_object_id
                and storage_object.get("purpose") == "browser"
            ),
            None,
        )
        if browser_storage_object is None:
            raise RuntimeError(
                "configured model run browser action storage object did not resolve "
                "to run storage"
            )
        screenshot_uri = event_check["browser_action_screenshot_uri"]
        bucket = browser_storage_object.get("bucket")
        key = browser_storage_object.get("key")
        storage_uri = f"s3://{bucket}/{key}" if bucket and key else ""
        if screenshot_uri and storage_uri and screenshot_uri != storage_uri:
            raise RuntimeError(
                "configured model run browser action screenshot URI did not match "
                "storage object"
            )
    promoted_event = find_artifact_promoted_event(
        event_check,
        str(storage_object["id"]),
        config.model_artifact_required_name,
    )
    promoted_storage_object_id = (
        str(promoted_event["storage_object_id"]) if promoted_event else None
    )
    promoted_artifact_name = (
        str(promoted_event["artifact_name"]) if promoted_event else None
    )
    artifact_event_matches_storage_object = promoted_event is not None
    if not artifact_event_matches_storage_object:
        raise RuntimeError(
            "configured model run artifact event did not match downloaded storage object"
        )
    require_model_run_event_order(
        event_types,
        event_check["event_sequences"],
    )
    runtime_state = inspect_runtime_state(
        client,
        config,
        run_id,
        headers,
        expected_status=run_status,
    )
    require_sandbox_command_session_matches_runtime(
        event_check,
        runtime_state,
        "configured model run",
    )
    require_browser_action_session_matches_runtime(
        event_check,
        runtime_state,
        "configured model run",
    )
    trace_check = inspect_run_trace(client, config, run_id, headers)
    return {
        "run_status": run_status,
        "artifact_count": len(artifacts),
        "artifact_names": artifact_names,
        "model_artifact_required_name_found": required_name_found,
        "model_artifact_storage_object_count": len(artifact_downloads),
        "model_artifact_total_download_bytes": sum(
            len(content) for content in artifact_downloads.values()
        ),
        "model_artifact_storage_object_id": storage_object_id,
        "model_artifact_download_bytes": len(content_bytes),
        "model_artifact_required_text_found": required_text_found,
        "model_run_event_types": event_types,
        "model_run_event_sequences": event_check["event_sequences"],
        "model_run_event_closure_label": event_closure_label(event_types),
        "model_execution_route_label": event_check["execution_model_route_label"],
        "model_sandbox_command_event_seen": sandbox_command_event_seen,
        "model_artifact_promoted_event_seen": artifact_promoted_event_seen,
        "model_run_event_payload_safe": event_check["payload_safe"],
        "model_sandbox_command_exit_code": sandbox_command_exit_code,
        "model_sandbox_command_output_uri": sandbox_command_output_uri,
        "model_sandbox_command_output_storage_object_id": (
            sandbox_command_output_storage_object_id
        ),
        "model_browser_action_storage_object_id": browser_action_storage_object_id,
        "model_artifact_promoted_storage_object_id": promoted_storage_object_id,
        "model_artifact_event_matches_storage_object": (
            artifact_event_matches_storage_object
        ),
        "model_runtime_state_status": runtime_state["status"],
        "model_runtime_sandbox_session_id": runtime_state["sandbox_session_id"],
        "model_runtime_browser_session_id": runtime_state["browser_session_id"],
        "model_runtime_completed_step_count": runtime_state["completed_step_count"],
        "model_runtime_promoted_artifact_path_count": runtime_state[
            "promoted_artifact_path_count"
        ],
        "model_runtime_required_artifact_path_found": runtime_state[
            "required_artifact_path_found"
        ],
        "model_trace_span_count": trace_check["span_count"],
        "model_trace_event_count": trace_check["trace_event_count"],
        "model_trace_billing_meter_count": trace_check["billing_meter_count"],
        "model_trace_audit_event_count": trace_check["audit_event_count"],
        "model_trace_runtime_tool_call_seen": trace_check["runtime_tool_call_seen"],
        "model_trace_billing_tool_call_seen": trace_check["billing_tool_call_seen"],
        "model_trace_audit_tool_executed_seen": trace_check[
            "audit_tool_executed_seen"
        ],
        "model_trace_payload_safe": trace_check["payload_safe"],
    }


def parse_browser_workspace_skill_run_id(status: str) -> str:
    status = status.strip()
    prefix = "Run "
    if not status.startswith(prefix):
        raise RuntimeError(
            "browser workspace skill invoke did not surface a run identifier"
        )
    run_id_parts = status[len(prefix) :].split()
    run_id = run_id_parts[0] if run_id_parts else ""
    if not run_id:
        raise RuntimeError(
            "browser workspace skill invoke did not surface a run identifier"
        )
    return run_id


def verify_browser_workspace_skill_run_artifact(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    skill_run_status: str,
    expected_workspace_id: str,
    headers: dict[str, str],
) -> dict[str, Any]:
    run_id = parse_browser_workspace_skill_run_id(skill_run_status)
    run_status = wait_for_run_success(client, config, run_id, headers)
    skill_ids = draft_skill_ids(config)
    expected_skill_id = skill_ids[0] if skill_ids else ""
    inspect_run_workspace(
        client,
        config,
        run_id,
        expected_workspace_id,
        expected_skill_id,
        headers,
        "browser workspace skill run",
    )
    artifacts = list_run_artifacts(client, config, run_id, headers)
    if not artifacts:
        raise RuntimeError("browser workspace skill run did not publish any artifacts")
    required_artifact = None
    for artifact in artifacts:
        if str(artifact.get("name") or "") == config.model_artifact_required_name:
            required_artifact = artifact
            break
    if required_artifact is None:
        raise RuntimeError(
            "browser workspace skill run did not publish required artifact: "
            f"{config.model_artifact_required_name}"
        )
    storage_objects = list_run_storage_objects(client, config, run_id, headers)
    artifact_downloads = download_model_artifact_storage_objects(
        client,
        config,
        artifacts,
        storage_objects,
        headers,
    )
    storage_object = find_storage_object_for_artifact(
        required_artifact,
        storage_objects,
    )
    if storage_object is None:
        raise RuntimeError(
            "browser workspace skill run artifact did not resolve to a storage object"
        )
    storage_object_id = str(storage_object["id"])
    content_bytes = artifact_downloads[storage_object_id]
    content = content_bytes.decode("utf-8", errors="replace")
    required_text_found = config.model_artifact_required_text in content
    if not required_text_found:
        raise RuntimeError(
            "browser workspace skill run artifact content did not include required text"
        )
    event_check = inspect_run_events(client, config, run_id, headers)
    require_no_cleanup_failure_events(event_check, "browser workspace skill run")
    event_types = event_check["event_types"]
    invocation_event_seen = "skill.workflow_invoked" in event_types
    sandbox_command_event_seen = "sandbox.command.executed" in event_types
    artifact_promoted_event_seen = "sandbox.artifact.promoted" in event_types
    if not invocation_event_seen:
        raise RuntimeError(
            "browser workspace skill run did not emit skill.workflow_invoked"
        )
    invocation_event_matches_skill = (
        event_check["skill_workflow_invoked_skill_id"] == expected_skill_id
    )
    if not invocation_event_matches_skill:
        raise RuntimeError(
            "browser workspace skill invocation event did not match invoked skill"
        )
    if not sandbox_command_event_seen:
        raise RuntimeError(
            "browser workspace skill run did not emit sandbox.command.executed"
        )
    if not artifact_promoted_event_seen:
        raise RuntimeError(
            "browser workspace skill run did not emit sandbox.artifact.promoted"
        )
    if not event_check["payload_safe"]:
        raise RuntimeError("browser workspace skill run event stream leaked raw output")
    command_output_uri = event_check["sandbox_command_output_uri"]
    if not command_output_uri:
        raise RuntimeError(
            "browser workspace skill run did not expose sandbox command output uri"
        )
    command_output_storage_object = find_storage_object_for_sandbox_command_output(
        storage_objects,
        command_output_uri,
    )
    if command_output_storage_object is None:
        raise RuntimeError(
            "browser workspace skill run sandbox command output did not resolve "
            "to a storage object"
        )
    command_output_storage_object_id = str(command_output_storage_object["id"])
    promoted_event = find_artifact_promoted_event(
        event_check,
        storage_object_id,
        config.model_artifact_required_name,
    )
    if promoted_event is None:
        raise RuntimeError(
            "browser workspace skill run artifact event did not match storage object"
        )
    require_model_run_event_order(
        event_types,
        event_check["event_sequences"],
        "browser workspace skill run",
    )
    runtime_state = inspect_runtime_state(
        client,
        config,
        run_id,
        headers,
        expected_status=run_status,
    )
    require_sandbox_command_session_matches_runtime(
        event_check,
        runtime_state,
        "browser workspace skill run",
    )
    trace_check = inspect_run_trace(client, config, run_id, headers)
    return {
        "run_id": run_id,
        "run_status": run_status,
        "artifact_count": len(artifacts),
        "artifact_download_bytes": sum(
            len(content_bytes) for content_bytes in artifact_downloads.values()
        ),
        "required_text_found": required_text_found,
        "invocation_event_seen": invocation_event_seen,
        "invocation_event_matches_skill": invocation_event_matches_skill,
        "sandbox_command_event_seen": sandbox_command_event_seen,
        "artifact_promoted_event_seen": artifact_promoted_event_seen,
        "event_payload_safe": event_check["payload_safe"],
        "event_count": len(event_types),
        "event_sequence_label": event_sequence_label(event_check["event_sequences"]),
        "event_closure_label": event_closure_label(event_types),
        "execution_model_route_label": event_check["execution_model_route_label"],
        "command_output_uri": command_output_uri,
        "runtime_state_status": runtime_state["status"],
        "runtime_sandbox_session_id": runtime_state["sandbox_session_id"],
        "runtime_required_artifact_path_found": runtime_state[
            "required_artifact_path_found"
        ],
        "trace_span_count": trace_check["span_count"],
        "trace_event_count": trace_check["trace_event_count"],
        "trace_billing_meter_count": trace_check["billing_meter_count"],
        "trace_audit_event_count": trace_check["audit_event_count"],
        "trace_runtime_tool_call_seen": trace_check["runtime_tool_call_seen"],
        "trace_billing_tool_call_seen": trace_check["billing_tool_call_seen"],
        "trace_audit_tool_executed_seen": trace_check["audit_tool_executed_seen"],
        "trace_payload_safe": trace_check["payload_safe"],
        "storage_object_id": storage_object_id,
        "command_output_storage_object_id": command_output_storage_object_id,
    }


def inspect_run_workspace(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    run_id: str,
    expected_workspace_id: str,
    expected_agent_id: str,
    headers: dict[str, str],
    label: str,
) -> dict[str, Any]:
    response = request_json(
        client,
        "GET",
        config.api_base_url,
        f"/api/runs/{run_id}",
        headers=headers,
    )
    assert_status(response, {200}, f"{label} workspace evidence failed")
    body = response.json_body()
    if body.get("workspace_id") != expected_workspace_id:
        raise RuntimeError(
            f"{label} did not belong to workspace"
            f" (run_id: {run_id}; expected_workspace_id: {expected_workspace_id}; "
            f"actual_workspace_id: {body.get('workspace_id')})"
        )
    if expected_agent_id and body.get("agent_id") != expected_agent_id:
        raise RuntimeError(
            f"{label} did not record invoked skill"
            f" (run_id: {run_id}; expected_skill_id: {expected_agent_id}; "
            f"actual_agent_id: {body.get('agent_id')})"
        )
    return body


def inspect_run_trace(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    run_id: str,
    headers: dict[str, str],
) -> dict[str, Any]:
    response = request_text(
        client,
        "GET",
        config.api_base_url,
        f"/api/runs/{run_id}/trace",
        headers=headers,
    )
    assert_status(response, {200}, "run trace check failed")
    body = response.json_body()
    spans = body.get("spans")
    trace_events = body.get("trace_events")
    billing_meters = body.get("billing_meters")
    audit_events = body.get("audit_events")
    if not isinstance(spans, list):
        raise RuntimeError("run trace check returned an unexpected spans shape")
    if not isinstance(trace_events, list):
        raise RuntimeError("run trace check returned an unexpected trace_events shape")
    if not isinstance(billing_meters, list):
        raise RuntimeError("run trace check returned an unexpected billing_meters shape")
    if not isinstance(audit_events, list):
        raise RuntimeError("run trace check returned an unexpected audit_events shape")
    runtime_tool_call_seen = any(
        isinstance(span, dict) and span.get("name") == "runtime.tool_call"
        for span in spans
    )
    billing_tool_call_seen = any(
        isinstance(meter, dict) and meter.get("meter_type") == "tool_call_count"
        for meter in billing_meters
    )
    audit_tool_executed_seen = any(
        isinstance(event, dict) and event.get("event_type") == "tool.executed"
        for event in audit_events
    )
    if not runtime_tool_call_seen:
        raise RuntimeError("run trace did not include runtime.tool_call span")
    if not billing_tool_call_seen:
        raise RuntimeError("run trace did not include tool_call_count billing meter")
    if not audit_tool_executed_seen:
        raise RuntimeError("run trace did not include tool.executed audit event")
    payload_safe = not contains_raw_sandbox_output(body)
    if not payload_safe:
        raise RuntimeError("run trace leaked raw sandbox output")
    return {
        "span_count": len(spans),
        "trace_event_count": len(trace_events),
        "billing_meter_count": len(billing_meters),
        "audit_event_count": len(audit_events),
        "runtime_tool_call_seen": runtime_tool_call_seen,
        "billing_tool_call_seen": billing_tool_call_seen,
        "audit_tool_executed_seen": audit_tool_executed_seen,
        "payload_safe": payload_safe,
    }


def inspect_runtime_state(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    run_id: str,
    headers: dict[str, str],
    expected_status: str,
) -> dict[str, Any]:
    response = request_json(
        client,
        "GET",
        config.api_base_url,
        f"/api/runs/{run_id}/state",
        headers=headers,
    )
    assert_status(response, {200}, "run runtime state check failed")
    body = response.json_body()
    status = str(body.get("status") or "")
    if status != expected_status:
        raise RuntimeError(
            "configured model run runtime state status did not match run status"
        )
    sandbox_session_id = str(body.get("sandbox_session_id") or "")
    if not sandbox_session_id:
        raise RuntimeError(
            "configured model run runtime state did not record sandbox session"
        )
    browser_session_raw = body.get("browser_session_id")
    browser_session_id = (
        str(browser_session_raw) if browser_session_raw is not None else None
    )
    completed_step_ids = body.get("completed_step_ids")
    if not isinstance(completed_step_ids, list) or not completed_step_ids:
        raise RuntimeError(
            "configured model run runtime state did not record completed steps"
        )
    promoted_paths = body.get("promoted_sandbox_artifact_paths")
    if not isinstance(promoted_paths, list):
        promoted_paths = []
    promoted_path_values = [str(path) for path in promoted_paths]
    required_path = f"/workspace/artifacts/{config.model_artifact_required_name}"
    required_path_found = required_path in promoted_path_values
    if not required_path_found:
        raise RuntimeError(
            "configured model run runtime state did not record promoted artifact path"
        )
    return {
        "status": status,
        "sandbox_session_id": sandbox_session_id,
        "browser_session_id": browser_session_id,
        "completed_step_count": len(completed_step_ids),
        "promoted_artifact_path_count": len(promoted_path_values),
        "required_artifact_path_found": required_path_found,
    }


def require_sandbox_command_session_matches_runtime(
    event_check: dict[str, Any],
    runtime_state: dict[str, Any],
    label: str,
) -> None:
    event_session_id = event_check.get("sandbox_command_session_id")
    runtime_session_id = runtime_state.get("sandbox_session_id")
    if not event_session_id:
        raise RuntimeError(
            f"{label} sandbox command event did not record sandbox session"
        )
    if event_session_id != runtime_session_id:
        raise RuntimeError(
            f"{label} sandbox command session did not match runtime state"
        )


def require_browser_action_session_matches_runtime(
    event_check: dict[str, Any],
    runtime_state: dict[str, Any],
    label: str,
) -> None:
    has_browser_evidence = (
        event_check.get("browser_action_session_id") is not None
        or event_check.get("browser_action_storage_object_id") is not None
    )
    if not has_browser_evidence:
        return
    event_session_id = event_check.get("browser_action_session_id")
    runtime_session_id = runtime_state.get("browser_session_id")
    if not event_session_id:
        raise RuntimeError(
            f"{label} browser action event did not record browser session"
        )
    if not runtime_session_id or event_session_id != runtime_session_id:
        raise RuntimeError(
            f"{label} browser action session did not match runtime state"
        )


def wait_for_run_success(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    run_id: str,
    headers: dict[str, str],
) -> str:
    final_statuses = {"succeeded", "failed", "cancelled", "timed_out"}
    last_status: str | None = None
    for attempt in range(config.run_status_poll_attempts):
        response = request_json(
            client,
            "GET",
            config.api_base_url,
            f"/api/runs/{run_id}",
            headers=headers,
        )
        assert_status(response, {200}, "run status check failed")
        status = str(response.json_body().get("status") or "")
        last_status = status
        if status in final_statuses:
            if status != "succeeded":
                raise RuntimeError(f"configured model run finished with status: {status}")
            return status
        if attempt < config.run_status_poll_attempts - 1:
            time.sleep(config.run_status_poll_interval_seconds)
    raise RuntimeError(f"configured model run did not finish; last status: {last_status}")


def list_run_artifacts(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    run_id: str,
    headers: dict[str, str],
) -> list[dict[str, Any]]:
    response = request_text(
        client,
        "GET",
        config.api_base_url,
        f"/api/runs/{run_id}/artifacts",
        headers=headers,
    )
    assert_status(response, {200}, "run artifact check failed")
    parsed = response.json_value()
    if isinstance(parsed, list):
        artifacts = parsed
    elif isinstance(parsed, dict) and isinstance(parsed.get("items"), list):
        artifacts = parsed["items"]
    else:
        raise RuntimeError("run artifact check returned an unexpected response shape")
    return [artifact for artifact in artifacts if isinstance(artifact, dict)]


def inspect_run_events(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    run_id: str,
    headers: dict[str, str],
) -> dict[str, Any]:
    response = request_text(
        client,
        "GET",
        config.api_base_url,
        f"/api/runs/{run_id}/events",
        headers=headers,
    )
    assert_status(response, {200}, "run event stream check failed")
    event_types: list[str] = []
    event_sequences: list[int] = []
    payload_safe = True
    sandbox_command_session_id: str | None = None
    sandbox_command_exit_code: int | None = None
    sandbox_command_output_uri: str | None = None
    browser_action_session_id: str | None = None
    browser_action_storage_object_id: str | None = None
    browser_action_screenshot_uri: str | None = None
    artifact_promoted_storage_object_id: str | None = None
    artifact_promoted_name: str | None = None
    artifact_promoted_events: list[dict[str, str | None]] = []
    execution_model_route_label: str | None = None
    skill_workflow_invoked_skill_id: str | None = None
    cleanup_failure_event_types: list[str] = []
    for block in response.body.split("\n\n"):
        event_type, payload = parse_sse_event_block(block)
        if event_type:
            event_types.append(event_type)
            if (
                event_type in RUN_CLEANUP_FAILURE_EVENT_TYPES
                and event_type not in cleanup_failure_event_types
            ):
                cleanup_failure_event_types.append(event_type)
        if contains_raw_sandbox_output(payload):
            payload_safe = False
        event_payload = payload.get("payload") if isinstance(payload, dict) else None
        sequence = payload.get("sequence") if isinstance(payload, dict) else None
        if sequence is None and isinstance(event_payload, dict):
            sequence = event_payload.get("sequence")
        if sequence is not None:
            event_sequences.append(int(sequence))
        if isinstance(event_payload, dict):
            if event_type == "skill.workflow_invoked":
                if event_payload.get("skill_id") is not None:
                    skill_workflow_invoked_skill_id = str(event_payload["skill_id"])
            if event_type in {"plan.created", "model.plan.created"}:
                execution_model_route_label = execution_model_route_from_payload(
                    event_payload
                )
            if (
                event_type == "sandbox.command.executed"
                and event_payload.get("exit_code") is not None
            ):
                if event_payload.get("session_id") is not None:
                    sandbox_command_session_id = str(event_payload["session_id"])
                sandbox_command_exit_code = int(event_payload["exit_code"])
                if event_payload.get("output_uri") is not None:
                    sandbox_command_output_uri = str(event_payload["output_uri"])
            if event_type == "browser.action.performed":
                if event_payload.get("session_id") is not None:
                    browser_action_session_id = str(event_payload["session_id"])
                if event_payload.get("screenshot_uri") is not None:
                    browser_action_screenshot_uri = str(
                        event_payload["screenshot_uri"]
                    )
                storage_object_id = (
                    event_payload.get("storage_object_id")
                    or event_payload.get("screenshot_storage_object_id")
                )
                if storage_object_id is not None:
                    browser_action_storage_object_id = str(storage_object_id)
            if event_type == "sandbox.artifact.promoted":
                promoted_event = {
                    "storage_object_id": None,
                    "artifact_name": None,
                }
                if event_payload.get("storage_object_id") is not None:
                    artifact_promoted_storage_object_id = str(
                        event_payload["storage_object_id"]
                    )
                    promoted_event["storage_object_id"] = (
                        artifact_promoted_storage_object_id
                    )
                if event_payload.get("artifact_name") is not None:
                    artifact_promoted_name = str(event_payload["artifact_name"])
                    promoted_event["artifact_name"] = artifact_promoted_name
                artifact_promoted_events.append(promoted_event)
    return {
        "event_types": event_types,
        "event_sequences": event_sequences,
        "payload_safe": payload_safe,
        "sandbox_command_session_id": sandbox_command_session_id,
        "sandbox_command_exit_code": sandbox_command_exit_code,
        "sandbox_command_output_uri": sandbox_command_output_uri,
        "browser_action_session_id": browser_action_session_id,
        "browser_action_storage_object_id": browser_action_storage_object_id,
        "browser_action_screenshot_uri": browser_action_screenshot_uri,
        "artifact_promoted_storage_object_id": artifact_promoted_storage_object_id,
        "artifact_promoted_name": artifact_promoted_name,
        "artifact_promoted_events": artifact_promoted_events,
        "execution_model_route_label": execution_model_route_label,
        "skill_workflow_invoked_skill_id": skill_workflow_invoked_skill_id,
        "cleanup_failure_event_types": cleanup_failure_event_types,
    }


def find_artifact_promoted_event(
    event_check: dict[str, Any],
    storage_object_id: str,
    artifact_name: str,
) -> dict[str, str | None] | None:
    events = event_check.get("artifact_promoted_events")
    if not isinstance(events, list):
        return None
    for event in events:
        if not isinstance(event, dict):
            continue
        if (
            event.get("storage_object_id") == storage_object_id
            and event.get("artifact_name") == artifact_name
        ):
            return event
    return None


def execution_model_route_from_payload(payload: dict[str, Any]) -> str | None:
    provider = str(payload.get("provider") or "provider unknown")
    model = payload.get("model")
    parts = [provider]
    if model:
        parts.append(str(model))
    usage = payload.get("usage")
    if isinstance(usage, dict):
        total_tokens = usage.get("total_tokens")
        if total_tokens is not None:
            parts.append(f"{total_tokens} tokens")
    provider_attempts = payload.get("provider_attempts")
    if isinstance(provider_attempts, list) and len(provider_attempts) > 1:
        parts.append(f"{len(provider_attempts)} attempts")
    if parts == ["provider unknown"]:
        return None
    return " · ".join(parts)


def require_no_cleanup_failure_events(
    event_check: dict[str, Any],
    label: str,
) -> None:
    cleanup_failure_event_types = event_check.get("cleanup_failure_event_types") or []
    if cleanup_failure_event_types:
        raise RuntimeError(
            f"{label} cleanup failed: "
            + ", ".join(str(event_type) for event_type in cleanup_failure_event_types)
        )


def require_model_run_event_order(
    event_types: list[str],
    event_sequences: list[int],
    label: str = "configured model run",
) -> None:
    if len(event_sequences) != len(event_types):
        raise RuntimeError(f"{label} event stream sequence was missing")

    for previous_sequence, current_sequence in zip(
        event_sequences, event_sequences[1:]
    ):
        if current_sequence <= previous_sequence:
            raise RuntimeError(f"{label} event stream sequence was not monotonic")

    def first_index(event_type: str) -> int | None:
        try:
            return event_types.index(event_type)
        except ValueError:
            return None

    command_index = first_index("sandbox.command.executed")
    skill_index = first_index("skill.workflow_invoked")
    browser_index = first_index("browser.action.performed")
    artifact_index = first_index("sandbox.artifact.promoted")
    succeeded_index = first_index("run.succeeded")
    if (
        command_index is None
        or artifact_index is None
        or succeeded_index is None
        or not (command_index < artifact_index < succeeded_index)
    ):
        raise RuntimeError(f"{label} event stream order was not closed")
    if skill_index is not None and not (skill_index < command_index):
        raise RuntimeError(f"{label} event stream order was not closed")


def event_sequence_label(event_sequences: list[int]) -> str:
    if not event_sequences:
        return "No sequence"
    if event_sequences[0] == event_sequences[-1]:
        return f"#{event_sequences[0]} monotonic"
    return f"#{event_sequences[0]}-#{event_sequences[-1]} monotonic"


def event_closure_label(event_types: list[str]) -> str:
    plan_indexes = [
        index
        for index, event_type in enumerate(event_types)
        if event_type in {"plan.created", "model.plan.created"}
    ]
    plan_index = min(plan_indexes) if plan_indexes else None

    def first_index(event_type: str) -> int | None:
        try:
            return event_types.index(event_type)
        except ValueError:
            return None

    command_index = first_index("sandbox.command.executed")
    skill_index = first_index("skill.workflow_invoked")
    browser_index = first_index("browser.action.performed")
    artifact_index = first_index("sandbox.artifact.promoted")
    succeeded_index = first_index("run.succeeded")
    if command_index is None:
        return "Waiting for command"
    if artifact_index is None:
        return "Waiting for artifact"
    if succeeded_index is None:
        return "Waiting for success"
    if not (command_index < artifact_index < succeeded_index):
        return "Closure out of order"
    if plan_index is not None:
        if not (plan_index < command_index):
            return "Closure out of order"
    if skill_index is not None and not (skill_index < command_index):
        return "Closure out of order"
    if browser_index is not None and not (browser_index < succeeded_index):
        return "Closure out of order"
    stages = [
        (plan_index, "plan"),
        (skill_index, "skill"),
        (command_index, "command"),
        (browser_index, "browser"),
        (artifact_index, "artifact"),
        (succeeded_index, "succeeded"),
    ]
    return " -> ".join(
        label for _, label in sorted(stage for stage in stages if stage[0] is not None)
    )


def parse_sse_event_block(block: str) -> tuple[str | None, Any]:
    event_line_type: str | None = None
    data_lines: list[str] = []
    for line in block.splitlines():
        if line.startswith("event: "):
            event_line_type = line.removeprefix("event: ").strip()
        if line.startswith("data: "):
            data_lines.append(line.removeprefix("data: ").strip())
    if not data_lines:
        return event_line_type, None
    payload = json.loads("\n".join(data_lines))
    if isinstance(payload, dict) and payload.get("type"):
        return str(payload["type"]), payload
    return event_line_type, payload


def contains_raw_sandbox_output(value: Any) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in {"stdout", "stderr"}:
                return True
            if contains_raw_sandbox_output(nested):
                return True
    if isinstance(value, list):
        return any(contains_raw_sandbox_output(item) for item in value)
    return False


def list_run_storage_objects(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    run_id: str,
    headers: dict[str, str],
) -> list[dict[str, Any]]:
    response = request_text(
        client,
        "GET",
        config.api_base_url,
        f"/api/runs/{run_id}/storage-objects",
        headers=headers,
    )
    assert_status(response, {200}, "run storage object check failed")
    parsed = response.json_value()
    if isinstance(parsed, list):
        storage_objects = parsed
    elif isinstance(parsed, dict) and isinstance(parsed.get("items"), list):
        storage_objects = parsed["items"]
    else:
        raise RuntimeError("run storage object check returned an unexpected response shape")
    return [
        storage_object
        for storage_object in storage_objects
        if isinstance(storage_object, dict)
    ]


def find_storage_object_for_artifacts(
    artifacts: list[dict[str, Any]],
    storage_objects: list[dict[str, Any]],
) -> dict[str, Any] | None:
    for artifact in artifacts:
        storage_object = find_storage_object_for_artifact(artifact, storage_objects)
        if storage_object is not None:
            return storage_object
    return None


def find_storage_object_for_artifact(
    artifact: dict[str, Any],
    storage_objects: list[dict[str, Any]],
) -> dict[str, Any] | None:
    artifact_uri = str(artifact.get("uri") or "")
    for storage_object in storage_objects:
        bucket = storage_object.get("bucket")
        key = storage_object.get("key")
        if bucket and key and artifact_uri == f"s3://{bucket}/{key}":
            return storage_object
    artifact_name = str(artifact.get("name") or "")
    for storage_object in storage_objects:
        if (
            storage_object.get("purpose") == "artifacts"
            and storage_object.get("filename") == artifact_name
        ):
            return storage_object
    return None


def download_model_artifact_storage_objects(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    artifacts: list[dict[str, Any]],
    storage_objects: list[dict[str, Any]],
    headers: dict[str, str],
) -> dict[str, bytes]:
    downloads: dict[str, bytes] = {}
    for artifact in artifacts:
        artifact_name = str(artifact.get("name") or "")
        storage_object = find_storage_object_for_artifact(artifact, storage_objects)
        if storage_object is None:
            raise RuntimeError(
                "configured model run artifact did not resolve to a storage object: "
                f"{artifact_name}"
            )
        storage_object_id = str(storage_object.get("id") or "")
        try:
            downloads[storage_object_id] = download_storage_object_bytes(
                client,
                config,
                storage_object_id,
                headers,
            )
        except RuntimeError as exc:
            if "storage object content was empty" in str(exc):
                raise RuntimeError(
                    "configured model run artifact storage object content was empty: "
                    f"{artifact_name}"
                ) from exc
            raise
    return downloads


def download_storage_object_content(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    storage_object_id: str,
    headers: dict[str, str],
) -> str:
    response = request_text(
        client,
        "GET",
        config.api_base_url,
        f"/api/storage/objects/{quote(storage_object_id, safe='')}/content",
        headers=headers,
    )
    assert_status(response, {200}, "run artifact content download failed")
    if not response.body:
        raise RuntimeError("configured model run artifact content was empty")
    return response.body


def verify_sandbox(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    workspace_id: str,
    run_id: str,
    headers: dict[str, str],
) -> dict[str, Any]:
    session_id = ""
    result: dict[str, Any] = {}
    try:
        session = request_json(
            client,
            "POST",
            config.api_base_url,
            "/api/sandbox/sessions",
            payload={
                "workspace_id": workspace_id,
                "run_id": run_id,
                "image": "python:3.12",
                "network_mode": "disabled",
            },
            headers=headers,
        )
        assert_status(session, {201}, "sandbox session creation failed")
        session_id = str(session.json_body()["id"])
        command = request_json(
            client,
            "POST",
            config.api_base_url,
            f"/api/sandbox/sessions/{session_id}/commands",
            payload={"command": config.sandbox_command},
            headers=headers,
        )
        assert_status(command, {200}, "sandbox command failed")
        command_body = command.json_body()
        if command_body.get("exit_code") != 0:
            raise RuntimeError("sandbox command returned a non-zero exit code")
        output_uri = command_body.get("output_uri")
        if not output_uri:
            raise RuntimeError("sandbox command did not return an output URI")
        command_output = verify_sandbox_command_output(
            client,
            config,
            run_id,
            str(output_uri),
            headers,
        )
        browser_screenshot = verify_api_browser_screenshot(
            client,
            config,
            session_id,
            run_id,
            headers,
        )
        result.update(
            {
                "session_id": session_id,
                "exit_code": command_body["exit_code"],
                "output_uri": output_uri,
                "sandbox_output_storage_object_id": command_output["storage_object_id"],
                "sandbox_output_download_bytes": command_output["download_bytes"],
                "browser_screenshot_uri": browser_screenshot["screenshot_uri"],
                "browser_screenshot_storage_object_id": browser_screenshot[
                    "storage_object_id"
                ],
                "browser_screenshot_download_bytes": browser_screenshot[
                    "download_bytes"
                ],
                "sandbox_session_destroyed": False,
                "sandbox_destroy_status_confirmed": False,
                "sandbox_post_destroy_command_blocked": False,
            }
        )
        return result
    finally:
        if session_id:
            destroy = request_json(
                client,
                "DELETE",
                config.api_base_url,
                f"/api/sandbox/sessions/{session_id}",
                headers=headers,
            )
            assert_status(destroy, {200}, "sandbox session destroy failed")
            if destroy.json_body().get("status") != "destroyed":
                raise RuntimeError(
                    "sandbox session destroy did not return destroyed status"
                )
            result["sandbox_session_destroyed"] = True
            result["sandbox_destroy_status_confirmed"] = True
            post_destroy_command = request_json(
                client,
                "POST",
                config.api_base_url,
                f"/api/sandbox/sessions/{session_id}/commands",
                payload={"command": "echo post-destroy-probe"},
                headers=headers,
            )
            result["sandbox_post_destroy_command_blocked"] = (
                400 <= post_destroy_command.status_code < 500
            )
            if not result["sandbox_post_destroy_command_blocked"]:
                raise RuntimeError(
                    "sandbox command was not blocked after session destroy"
                )


def verify_sandbox_command_output(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    run_id: str,
    output_uri: str,
    headers: dict[str, str],
) -> dict[str, Any]:
    storage_object = find_storage_object_for_sandbox_command_output(
        list_run_storage_objects(client, config, run_id, headers),
        output_uri,
    )
    if storage_object is None:
        raise RuntimeError(
            "sandbox command output URI did not resolve to a storage object"
        )
    storage_object_id = str(storage_object["id"])
    content = download_storage_object_bytes(
        client,
        config,
        storage_object_id,
        headers,
    )
    return {
        "storage_object_id": storage_object_id,
        "download_bytes": len(content),
    }


def find_storage_object_for_sandbox_command_output(
    storage_objects: list[dict[str, Any]],
    output_uri: str,
) -> dict[str, Any] | None:
    for storage_object in storage_objects:
        if storage_object.get("purpose") != "sandbox-command-outputs":
            continue
        bucket = storage_object.get("bucket")
        key = storage_object.get("key")
        uri = f"s3://{bucket}/{key}" if bucket and key else ""
        if uri == output_uri:
            return storage_object
    return None


def verify_api_browser_screenshot(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    session_id: str,
    run_id: str,
    headers: dict[str, str],
) -> dict[str, Any]:
    screenshot = request_json(
        client,
        "POST",
        config.api_base_url,
        f"/api/browser/sessions/{quote(session_id, safe='')}/actions",
        payload={"action_type": "screenshot"},
        headers=headers,
    )
    assert_status(screenshot, {200}, "API browser screenshot failed")
    screenshot_uri = str(screenshot.json_body().get("screenshot_uri") or "")
    if not screenshot_uri:
        raise RuntimeError("API browser screenshot did not return a screenshot URI")
    storage_object = find_storage_object_for_browser_screenshot(
        list_run_storage_objects(client, config, run_id, headers),
        screenshot_uri,
        session_id,
    )
    if storage_object is None:
        raise RuntimeError("API browser screenshot did not resolve to a storage object")
    content = download_storage_object_bytes(
        client,
        config,
        str(storage_object["id"]),
        headers,
    )
    if not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError("API browser screenshot content was not a PNG")
    return {
        "screenshot_uri": screenshot_uri,
        "storage_object_id": str(storage_object["id"]),
        "download_bytes": len(content),
    }


def find_storage_object_for_browser_screenshot(
    storage_objects: list[dict[str, Any]],
    screenshot_uri: str,
    session_id: str,
) -> dict[str, Any] | None:
    expected_filename = f"{session_id}.png"
    for storage_object in storage_objects:
        if storage_object.get("purpose") != "browser":
            continue
        bucket = storage_object.get("bucket")
        key = storage_object.get("key")
        uri = f"s3://{bucket}/{key}" if bucket and key else ""
        if uri == screenshot_uri:
            return storage_object
        if storage_object.get("filename") == expected_filename:
            return storage_object
    return None


def download_storage_object_bytes(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    storage_object_id: str,
    headers: dict[str, str],
) -> bytes:
    response = request_text(
        client,
        "GET",
        config.api_base_url,
        f"/api/storage/objects/{quote(storage_object_id, safe='')}/content",
        headers=headers,
    )
    assert_status(response, {200}, "storage object content download failed")
    content = response.body_bytes or response.body.encode("utf-8")
    if not content:
        raise RuntimeError("storage object content was empty")
    return content


def browser_session_payload(
    config: LocalCloudPocVerificationConfig,
    tenant_id: str,
    workspace_id: str,
    run_id: str,
) -> dict[str, str]:
    return {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "run_id": run_id,
        "session_id": config.browser_session_id,
    }


def open_browser_session(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    tenant_id: str,
    workspace_id: str,
    run_id: str,
) -> None:
    opened = request_json(
        client,
        "POST",
        config.browser_base_url,
        "/sessions",
        payload=browser_session_payload(config, tenant_id, workspace_id, run_id),
        headers=browser_controller_headers(config),
    )
    assert_status(opened, {201}, "browser session creation failed")


def verify_browser_session_scope(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    tenant_id: str,
    workspace_id: str,
    run_id: str,
) -> dict[str, bool]:
    if config.browser_denied_tenant_id == tenant_id:
        raise RuntimeError("browser denied tenant id must differ from tenant id")

    allowed_response = request_json(
        client,
        "GET",
        config.browser_base_url,
        browser_session_list_path(tenant_id),
        headers=browser_controller_headers(config),
    )
    assert_status(
        allowed_response,
        {200},
        "browser allowed tenant session list failed",
    )
    allowed_sessions = parse_browser_session_list(allowed_response)
    if any(str(session.get("tenant_id") or "") != tenant_id for session in allowed_sessions):
        raise RuntimeError("browser allowed tenant session list crossed tenant scope")
    browser_session_listed = any(
        str(session.get("session_id") or "") == config.browser_session_id
        for session in allowed_sessions
    )
    if not browser_session_listed:
        raise RuntimeError("browser session was not listed for allowed tenant")

    denied_response = request_json(
        client,
        "GET",
        config.browser_base_url,
        browser_session_list_path(config.browser_denied_tenant_id),
        headers=browser_controller_headers(config),
    )
    assert_status(
        denied_response,
        {200},
        "browser denied tenant session list failed",
    )
    denied_sessions = parse_browser_session_list(denied_response)
    if any(
        str(session.get("tenant_id") or "") != config.browser_denied_tenant_id
        for session in denied_sessions
    ):
        raise RuntimeError("browser denied tenant session list crossed tenant scope")
    browser_tenant_session_scope_enforced = not any(
        str(session.get("session_id") or "") == config.browser_session_id
        for session in denied_sessions
    )
    if not browser_tenant_session_scope_enforced:
        raise RuntimeError("browser session list leaked into denied tenant scope")

    scoped_session_path = browser_scoped_session_path(
        config.browser_session_id,
        tenant_id,
        f"{workspace_id}_scope_probe",
        f"{run_id}_scope_probe",
    )
    read_scope_response = request_json(
        client,
        "GET",
        config.browser_base_url,
        scoped_session_path,
        headers=browser_controller_headers(config),
    )
    browser_session_read_scope_enforced = read_scope_response.status_code == 404
    if not browser_session_read_scope_enforced:
        raise RuntimeError("browser session read crossed workspace/run scope")

    delete_scope_response = request_json(
        client,
        "DELETE",
        config.browser_base_url,
        scoped_session_path,
        headers=browser_controller_headers(config),
    )
    browser_session_delete_scope_enforced = delete_scope_response.status_code == 404
    if not browser_session_delete_scope_enforced:
        raise RuntimeError("browser session delete crossed workspace/run scope")

    confirm_response = request_json(
        client,
        "GET",
        config.browser_base_url,
        browser_session_list_path(tenant_id),
        headers=browser_controller_headers(config),
    )
    assert_status(
        confirm_response,
        {200},
        "browser post-scope tenant session list failed",
    )
    confirmed_sessions = parse_browser_session_list(confirm_response)
    if not any(
        str(session.get("session_id") or "") == config.browser_session_id
        for session in confirmed_sessions
    ):
        raise RuntimeError("browser scope delete probe removed the active session")

    return {
        "browser_session_listed": browser_session_listed,
        "browser_tenant_session_scope_enforced": browser_tenant_session_scope_enforced,
        "browser_session_read_scope_enforced": browser_session_read_scope_enforced,
        "browser_session_delete_scope_enforced": browser_session_delete_scope_enforced,
    }


def browser_session_list_path(tenant_id: str) -> str:
    return f"/sessions?tenant_id={quote(tenant_id, safe='')}"


def browser_scoped_session_path(
    session_id: str,
    tenant_id: str,
    workspace_id: str,
    run_id: str,
) -> str:
    return (
        f"/sessions/{quote(session_id, safe='')}"
        f"?tenant_id={quote(tenant_id, safe='')}"
        f"&workspace_id={quote(workspace_id, safe='')}"
        f"&run_id={quote(run_id, safe='')}"
    )


def parse_browser_session_list(
    response: LocalCloudPocHttpResponse,
) -> list[dict[str, Any]]:
    sessions = response.json_body().get("sessions")
    if not isinstance(sessions, list):
        raise RuntimeError("browser session list returned an unexpected response shape")
    return [session for session in sessions if isinstance(session, dict)]


def verify_browser(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    tenant_id: str,
    workspace_id: str,
    run_id: str,
) -> str:
    session = browser_session_payload(config, tenant_id, workspace_id, run_id)
    html = (
        "<!doctype html><title>Taroai Browser Smoke</title>"
        f'<main id="result">{config.browser_smoke_text}</main>'
    )
    navigate = request_json(
        client,
        "POST",
        config.browser_base_url,
        "/actions",
        payload=session
        | {"action_type": "navigate", "url": "data:text/html," + quote(html)},
        headers=browser_controller_headers(config),
    )
    assert_status(navigate, {201}, "browser navigation failed")
    extract = request_json(
        client,
        "POST",
        config.browser_base_url,
        "/actions",
        payload=session | {"action_type": "extract", "selector": "#result"},
        headers=browser_controller_headers(config),
    )
    assert_status(extract, {201}, "browser extraction failed")
    text = str(extract.json_body().get("text") or "")
    if text != config.browser_smoke_text:
        raise RuntimeError("browser extraction returned unexpected text")
    return text


def delete_browser_session(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    tenant_id: str,
    workspace_id: str,
    run_id: str,
) -> None:
    session_id = quote(config.browser_session_id, safe="")
    tenant_query = quote(tenant_id, safe="")
    workspace_query = quote(workspace_id, safe="")
    run_query = quote(run_id, safe="")
    path = (
        f"/sessions/{session_id}?tenant_id={tenant_query}"
        f"&workspace_id={workspace_query}&run_id={run_query}"
    )
    response = request_json(
        client,
        "DELETE",
        config.browser_base_url,
        path,
        headers=browser_controller_headers(config),
    )
    assert_status(response, {200, 204}, "browser session deletion failed")
    deleted_session = response.json_body()
    if (
        response.status_code != 200
        or deleted_session.get("session_id") != config.browser_session_id
        or deleted_session.get("tenant_id") != tenant_id
        or deleted_session.get("workspace_id") != workspace_id
        or deleted_session.get("run_id") != run_id
    ):
        raise RuntimeError(
            "browser session deletion response did not include deleted session"
        )
    fetched = request_json(
        client,
        "GET",
        config.browser_base_url,
        path,
        headers=browser_controller_headers(config),
    )
    assert_status(fetched, {404}, "browser session deletion verification failed")


def verify_browser_workspace(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    tenant_id: str,
    user_id: str,
    workspace_id: str,
    run_id: str,
    headers: dict[str, str],
    expected_run_id: str | None = None,
    expected_sandbox_session_id: str | None = None,
    expected_artifact_storage_object_id: str | None = None,
    expected_terminal_storage_object_id: str | None = None,
    expected_terminal_output_uri: str | None = None,
    expected_browser_storage_object_id: str | None = None,
    expected_execution_model_route: str | None = None,
    expected_event_count: int | None = None,
    expected_event_sequence_label: str | None = None,
    expected_event_closure_label: str | None = None,
    expected_trace_span_count: int | None = None,
    expected_trace_event_count: int | None = None,
    expected_trace_billing_count: int | None = None,
    expected_trace_audit_count: int | None = None,
) -> LocalCloudPocBrowserWorkspaceVerification:
    if config.browser_workspace_url is None:
        return LocalCloudPocBrowserWorkspaceVerification()
    session = browser_session_payload(config, tenant_id, workspace_id, run_id)
    navigate = request_json(
        client,
        "POST",
        config.browser_base_url,
        "/actions",
        payload=session
        | {
            "action_type": "navigate",
            "url": browser_workspace_navigation_url(
                config,
                tenant_id=tenant_id,
                user_id=user_id,
                workspace_id=workspace_id,
            ),
        },
        headers=browser_controller_headers(config),
    )
    assert_status(navigate, {201}, "browser workspace navigation failed")
    extract = request_json(
        client,
        "POST",
        config.browser_base_url,
        "/actions",
        payload=session
        | {"action_type": "extract", "selector": '[data-testid="chat-column"]'},
        headers=browser_controller_headers(config),
    )
    assert_status(extract, {201}, "browser workspace extraction failed")
    text = str(extract.json_body().get("text") or "")
    require_text_fragments(
        text,
        {
            "chat heading": "How can I help",
            "composer hint": "Press Enter to send, Shift+Enter for a new line.",
        },
        "browser workspace extraction",
    )
    bootstrap = {
        "status": None,
        "tenant_id": None,
        "user_id": None,
        "workspace_id": None,
        "token_cleared": False,
    }
    auth_status = None
    readiness = {}
    submit_text = None
    execution_model_route = None
    evidence_summary = None
    delivery_summary = None
    delivery_chain = {
        "status": None,
        "run_id": None,
        "sandbox_session_id": None,
        "artifact_storage_object_id": None,
        "terminal_storage_object_id": None,
        "browser_storage_object_id": None,
    }
    event_integrity = {
        "status": None,
        "count": None,
        "sequence": None,
        "closure": None,
    }
    trace_ui = {
        "status": None,
        "span_count": None,
        "event_count": None,
        "billing_count": None,
        "audit_count": None,
        "error": None,
    }
    browser_capture = {
        "storage_object_id": None,
        "preview_storage_object_id": None,
    }
    artifact_preview_text = None
    artifact_preview_storage_object_id = None
    artifact_download = {
        "storage_object_id": None,
        "download_status": None,
        "downloaded_storage_object_id": None,
    }
    terminal_text = None
    terminal_output_storage_object_id = None
    feedback_status = None
    feedback_api = {
        "seen": False,
        "rating": None,
    }
    missing_skill_feedback = {
        "status": None,
        "api_count": 0,
    }
    candidate_status = None
    eval_candidate_api_count = 0
    eval_candidate_review_api_count = 0
    pack_candidate_status = None
    pack_candidate_api_count = 0
    pack_candidate_review_api_count = 0
    draft_status = None
    draft_api_status = None
    draft_api_applied = False
    solution_pack_install_status = None
    solution_pack_install_api_seen = False
    solution_pack_install_skill_count = 0
    skill_invoke_status = None
    skill_run_status = None
    skill_run_api = {
        "run_id": None,
        "run_status": None,
        "artifact_count": 0,
        "artifact_download_bytes": 0,
        "required_text_found": False,
        "invocation_event_seen": False,
        "invocation_event_matches_skill": False,
        "sandbox_command_event_seen": False,
        "artifact_promoted_event_seen": False,
        "event_payload_safe": False,
        "event_count": 0,
        "event_sequence_label": None,
        "command_output_uri": None,
        "runtime_state_status": None,
        "runtime_sandbox_session_id": None,
        "runtime_required_artifact_path_found": False,
        "trace_span_count": 0,
        "trace_event_count": 0,
        "trace_billing_meter_count": 0,
        "trace_audit_event_count": 0,
        "trace_runtime_tool_call_seen": False,
        "trace_billing_tool_call_seen": False,
        "trace_audit_tool_executed_seen": False,
        "trace_payload_safe": False,
        "storage_object_id": None,
    }
    skill_evidence_summary = None
    skill_delivery_summary = None
    skill_artifact_preview_text = None
    skill_trace_ui = {
        "status": None,
        "span_count": None,
        "event_count": None,
        "billing_count": None,
        "audit_count": None,
        "error": None,
    }
    skill_history_ui = {
        "status": None,
        "text": None,
    }
    skill_history_selection_ui = {
        "trace_status": None,
        "delivery_summary": None,
        "delivery_chain_status": None,
        "delivery_chain_run_id": None,
        "delivery_chain_sandbox_session": None,
        "delivery_chain_artifact_storage": None,
        "delivery_chain_terminal_storage": None,
        "event_integrity_status": None,
        "event_integrity_count": None,
        "event_integrity_sequence": None,
        "event_integrity_closure": None,
        "terminal_text": None,
        "terminal_output_storage_object_id": None,
        "artifact_preview_text": None,
        "previewed_storage_object_id": None,
        "runtime_state_status": None,
        "runtime_sandbox_session": None,
        "runtime_artifact_count": None,
        "execution_summary": None,
        "execution_model_route": None,
        "execution_sandbox": None,
        "execution_artifact": None,
        "download_storage_object_id": None,
        "download_status": None,
        "downloaded_storage_object_id": None,
        "feedback_status": None,
        "feedback_api_seen": False,
        "feedback_rating": None,
    }
    if config.browser_workspace_api_base_url is not None:
        bootstrap = verify_browser_workspace_bootstrap(
            client,
            config,
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            workspace_id=workspace_id,
        )
        auth_status = verify_browser_workspace_login(
            client,
            config,
            session,
            tenant_id,
            workspace_id,
        )
        readiness = verify_browser_workspace_readiness(client, config, session)
        if config.browser_workspace_submit_message is not None:
            submit_text = verify_browser_workspace_submit(client, config, session)
            if config.require_model_execution:
                execution_model_route = verify_browser_workspace_execution_model_route(
                    client,
                    config,
                    session,
                    expected_execution_model_route,
                )
            evidence_summary = verify_browser_workspace_evidence_summary(
                client,
                config,
                session,
            )
            if config.require_model_execution:
                delivery_summary = verify_browser_workspace_delivery_summary(
                    client,
                    config,
                    session,
                )
                delivery_chain = verify_browser_workspace_delivery_chain(
                    client,
                    config,
                    session,
                    expected_run_id=expected_run_id,
                    expected_sandbox_session_id=expected_sandbox_session_id,
                    expected_artifact_storage_object_id=(
                        expected_artifact_storage_object_id
                    ),
                    expected_terminal_storage_object_id=(
                        expected_terminal_storage_object_id
                    ),
                    expected_browser_storage_object_id=(
                        expected_browser_storage_object_id
                    ),
                )
                event_integrity = verify_browser_workspace_event_integrity(
                    client,
                    config,
                    session,
                    expected_event_count,
                    expected_event_sequence_label,
                    expected_event_closure_label,
                )
                trace_ui = verify_browser_workspace_trace_summary(
                    client,
                    config,
                    session,
                    expected_span_count=expected_trace_span_count or 0,
                    expected_event_count=expected_trace_event_count or 0,
                    expected_billing_count=expected_trace_billing_count or 0,
                    expected_audit_count=expected_trace_audit_count or 0,
                )
                browser_capture = verify_browser_workspace_browser_capture(
                    client,
                    config,
                    session,
                    expected_browser_storage_object_id,
                )
                terminal_text = verify_browser_workspace_terminal_summary(
                    client,
                    config,
                    session,
                )
                if (
                    expected_terminal_output_uri is not None
                    and expected_terminal_output_uri not in terminal_text
                ):
                    raise RuntimeError(
                        "browser workspace terminal output URI did not match "
                        "API evidence"
                    )
                terminal_output_storage_object_id = (
                    verify_browser_workspace_terminal_output_object(
                        client,
                        config,
                        session,
                    )
                )
                if (
                    expected_terminal_storage_object_id is not None
                    and terminal_output_storage_object_id
                    != expected_terminal_storage_object_id
                ):
                    raise RuntimeError(
                        "browser workspace terminal output storage object "
                        "did not match API evidence"
                    )
                artifact_preview_text = verify_browser_workspace_artifact_preview(
                    client,
                    config,
                    session,
                )
                artifact_preview_storage_object_id = (
                    verify_browser_workspace_artifact_preview_storage_object(
                        client,
                        config,
                        session,
                        expected_artifact_storage_object_id,
                    )
                )
                artifact_download = verify_browser_workspace_artifact_download(
                    client,
                    config,
                    session,
                    expected_artifact_storage_object_id,
                )
                feedback_api = verify_browser_workspace_run_feedback(
                    client,
                    config,
                    session,
                    expected_run_id,
                    headers,
                )
                feedback_status = feedback_api["status"]
                eval_candidate = verify_browser_workspace_candidate_generation(
                    client,
                    config,
                    session,
                    expected_run_id,
                    headers,
                )
                candidate_status = eval_candidate["status"]
                eval_candidate_api_count = eval_candidate["api_count"]
                eval_review = verify_browser_workspace_eval_candidate_review(
                    client,
                    config,
                    session,
                    expected_run_id,
                    headers,
                )
                candidate_status = eval_review["status"]
                eval_candidate_review_api_count = eval_review["api_count"]
                missing_skill_feedback = verify_browser_workspace_missing_skill_feedback(
                    client,
                    config,
                    session,
                    headers,
                )
                pack_candidate = verify_browser_workspace_pack_candidate_generation(
                    client,
                    config,
                    session,
                    headers,
                )
                pack_candidate_api_count = pack_candidate["api_count"]
                pack_review = verify_browser_workspace_pack_candidate_review(
                    client,
                    config,
                    session,
                    headers,
                )
                pack_candidate_status = pack_review["status"]
                pack_candidate_review_api_count = pack_review["api_count"]
                draft_result = verify_browser_workspace_solution_pack_draft_lifecycle(
                    client,
                    config,
                    session,
                    str(pack_review["publication_draft_id"]),
                    headers,
                )
                draft_status = draft_result["status"]
                draft_api_status = draft_result["api_status"]
                draft_api_applied = draft_result["production_change_applied"]
                solution_pack_install = verify_browser_workspace_solution_pack_install(
                    client,
                    config,
                    session,
                    workspace_id,
                    headers,
                )
                solution_pack_install_status = solution_pack_install["status"]
                solution_pack_install_api_seen = solution_pack_install["api_seen"]
                solution_pack_install_skill_count = solution_pack_install["skill_count"]
                skill_invoke_status = verify_browser_workspace_skill_ready(
                    client,
                    config,
                    session,
                )
                skill_run_status = verify_browser_workspace_skill_invoke(
                    client,
                    config,
                    session,
                )
                skill_run_api = verify_browser_workspace_skill_run_artifact(
                    client,
                    config,
                    skill_run_status,
                    workspace_id,
                    headers,
                )
                skill_evidence_summary = verify_browser_workspace_evidence_summary(
                    client,
                    config,
                    session,
                )
                skill_delivery_summary = verify_browser_workspace_delivery_summary(
                    client,
                    config,
                    session,
                )
                skill_artifact_preview_text = verify_browser_workspace_artifact_preview(
                    client,
                    config,
                    session,
                )
                skill_trace_ui = verify_browser_workspace_trace_summary(
                    client,
                    config,
                    session,
                    expected_span_count=skill_run_api["trace_span_count"],
                    expected_event_count=skill_run_api["trace_event_count"],
                    expected_billing_count=(
                        skill_run_api["trace_billing_meter_count"]
                    ),
                    expected_audit_count=skill_run_api["trace_audit_event_count"],
                )
                skill_history_ui = (
                    verify_browser_workspace_run_history_contains_skill_run(
                        client,
                        config,
                        session,
                        skill_run_api["run_id"],
                    )
                )
                skill_history_selection_ui = (
                    verify_browser_workspace_select_skill_run_from_history(
                        client,
                        config,
                        session,
                        skill_run_api["run_id"],
                        skill_run_api["storage_object_id"],
                        skill_run_api["command_output_storage_object_id"],
                        skill_run_api["command_output_uri"],
                        skill_run_api["runtime_sandbox_session_id"],
                        skill_run_api["event_count"],
                        skill_run_api["event_sequence_label"],
                        skill_run_api["event_closure_label"],
                        skill_run_api["execution_model_route_label"],
                        headers,
                    )
                )
    return LocalCloudPocBrowserWorkspaceVerification(
        text=text,
        bootstrap_status=bootstrap["status"],
        bootstrap_tenant_id=bootstrap["tenant_id"],
        bootstrap_user_id=bootstrap["user_id"],
        bootstrap_workspace_id=bootstrap["workspace_id"],
        bootstrap_token_cleared=bool(bootstrap["token_cleared"]),
        auth_status=auth_status,
        readiness_status=readiness.get("status"),
        readiness_model=readiness.get("model"),
        readiness_sandbox=readiness.get("sandbox"),
        submit_text=submit_text,
        execution_model_route=execution_model_route,
        evidence_summary=evidence_summary,
        delivery_summary=delivery_summary,
        delivery_chain_status=delivery_chain["status"],
        delivery_chain_run_id=delivery_chain["run_id"],
        delivery_chain_sandbox_session_id=delivery_chain["sandbox_session_id"],
        delivery_chain_artifact_storage_object_id=(
            delivery_chain["artifact_storage_object_id"]
        ),
        delivery_chain_terminal_storage_object_id=(
            delivery_chain["terminal_storage_object_id"]
        ),
        delivery_chain_browser_storage_object_id=(
            delivery_chain["browser_storage_object_id"]
        ),
        event_integrity_status=event_integrity["status"],
        event_integrity_count=event_integrity["count"],
        event_integrity_sequence=event_integrity["sequence"],
        event_integrity_closure=event_integrity["closure"],
        trace_status_text=trace_ui["status"],
        trace_span_count_text=trace_ui["span_count"],
        trace_event_count_text=trace_ui["event_count"],
        trace_billing_count_text=trace_ui["billing_count"],
        trace_audit_count_text=trace_ui["audit_count"],
        trace_error_text=trace_ui["error"],
        browser_storage_object_id=browser_capture["storage_object_id"],
        browser_preview_storage_object_id=browser_capture[
            "preview_storage_object_id"
        ],
        artifact_preview_text=artifact_preview_text,
        artifact_preview_storage_object_id=artifact_preview_storage_object_id,
        artifact_download_storage_object_id=artifact_download["storage_object_id"],
        artifact_download_status=artifact_download["download_status"],
        artifact_downloaded_storage_object_id=artifact_download[
            "downloaded_storage_object_id"
        ],
        terminal_text=terminal_text,
        terminal_output_storage_object_id=terminal_output_storage_object_id,
        feedback_status=feedback_status,
        feedback_api_seen=feedback_api["seen"],
        feedback_rating=feedback_api["rating"],
        missing_skill_feedback_status=missing_skill_feedback["status"],
        missing_skill_feedback_api_count=missing_skill_feedback["api_count"],
        candidate_status=candidate_status,
        eval_candidate_api_count=eval_candidate_api_count,
        eval_candidate_review_api_count=eval_candidate_review_api_count,
        pack_candidate_status=pack_candidate_status,
        pack_candidate_api_count=pack_candidate_api_count,
        pack_candidate_review_api_count=pack_candidate_review_api_count,
        draft_status=draft_status,
        draft_api_status=draft_api_status,
        draft_api_applied=draft_api_applied,
        solution_pack_install_status=solution_pack_install_status,
        solution_pack_install_api_seen=solution_pack_install_api_seen,
        solution_pack_install_skill_count=solution_pack_install_skill_count,
        skill_invoke_status=skill_invoke_status,
        skill_run_status=skill_run_status,
        skill_run_id=skill_run_api["run_id"],
        skill_run_api_status=skill_run_api["run_status"],
        skill_run_artifact_count=skill_run_api["artifact_count"],
        skill_run_artifact_download_bytes=skill_run_api["artifact_download_bytes"],
        skill_run_required_text_found=skill_run_api["required_text_found"],
        skill_invocation_event_seen=skill_run_api["invocation_event_seen"],
        skill_invocation_event_matches_skill=(
            skill_run_api["invocation_event_matches_skill"]
        ),
        skill_run_sandbox_command_event_seen=(
            skill_run_api["sandbox_command_event_seen"]
        ),
        skill_run_artifact_promoted_event_seen=(
            skill_run_api["artifact_promoted_event_seen"]
        ),
        skill_run_event_payload_safe=skill_run_api["event_payload_safe"],
        skill_runtime_state_status=skill_run_api["runtime_state_status"],
        skill_runtime_sandbox_session_id=(
            skill_run_api["runtime_sandbox_session_id"]
        ),
        skill_runtime_required_artifact_path_found=(
            skill_run_api["runtime_required_artifact_path_found"]
        ),
        skill_trace_span_count=skill_run_api["trace_span_count"],
        skill_trace_event_count=skill_run_api["trace_event_count"],
        skill_trace_billing_meter_count=skill_run_api["trace_billing_meter_count"],
        skill_trace_audit_event_count=skill_run_api["trace_audit_event_count"],
        skill_trace_runtime_tool_call_seen=(
            skill_run_api["trace_runtime_tool_call_seen"]
        ),
        skill_trace_billing_tool_call_seen=(
            skill_run_api["trace_billing_tool_call_seen"]
        ),
        skill_trace_audit_tool_executed_seen=(
            skill_run_api["trace_audit_tool_executed_seen"]
        ),
        skill_trace_payload_safe=skill_run_api["trace_payload_safe"],
        skill_trace_status_text=skill_trace_ui["status"],
        skill_trace_span_count_text=skill_trace_ui["span_count"],
        skill_trace_event_count_text=skill_trace_ui["event_count"],
        skill_trace_billing_count_text=skill_trace_ui["billing_count"],
        skill_trace_audit_count_text=skill_trace_ui["audit_count"],
        skill_trace_error_text=skill_trace_ui["error"],
        skill_run_history_status=skill_history_ui["status"],
        skill_run_history_text=skill_history_ui["text"],
        skill_history_selection_trace_status=(
            skill_history_selection_ui["trace_status"]
        ),
        skill_history_selection_delivery_summary=(
            skill_history_selection_ui["delivery_summary"]
        ),
        skill_history_selection_delivery_chain_status=(
            skill_history_selection_ui["delivery_chain_status"]
        ),
        skill_history_selection_delivery_chain_run_id=(
            skill_history_selection_ui["delivery_chain_run_id"]
        ),
        skill_history_selection_delivery_chain_sandbox_session=(
            skill_history_selection_ui["delivery_chain_sandbox_session"]
        ),
        skill_history_selection_delivery_chain_artifact_storage=(
            skill_history_selection_ui["delivery_chain_artifact_storage"]
        ),
        skill_history_selection_delivery_chain_terminal_storage=(
            skill_history_selection_ui["delivery_chain_terminal_storage"]
        ),
        skill_history_selection_event_integrity_status=(
            skill_history_selection_ui["event_integrity_status"]
        ),
        skill_history_selection_event_integrity_count=(
            skill_history_selection_ui["event_integrity_count"]
        ),
        skill_history_selection_event_integrity_sequence=(
            skill_history_selection_ui["event_integrity_sequence"]
        ),
        skill_history_selection_event_integrity_closure=(
            skill_history_selection_ui["event_integrity_closure"]
        ),
        skill_history_selection_terminal_text=(
            skill_history_selection_ui["terminal_text"]
        ),
        skill_history_selection_terminal_output_storage_object_id=(
            skill_history_selection_ui["terminal_output_storage_object_id"]
        ),
        skill_history_selection_artifact_preview_text=(
            skill_history_selection_ui["artifact_preview_text"]
        ),
        skill_history_selection_previewed_storage_object_id=(
            skill_history_selection_ui["previewed_storage_object_id"]
        ),
        skill_history_selection_runtime_state_status=(
            skill_history_selection_ui["runtime_state_status"]
        ),
        skill_history_selection_runtime_sandbox_session=(
            skill_history_selection_ui["runtime_sandbox_session"]
        ),
        skill_history_selection_runtime_artifact_count=(
            skill_history_selection_ui["runtime_artifact_count"]
        ),
        skill_history_selection_execution_summary=(
            skill_history_selection_ui["execution_summary"]
        ),
        skill_history_selection_execution_model_route=(
            skill_history_selection_ui["execution_model_route"]
        ),
        skill_history_selection_execution_sandbox=(
            skill_history_selection_ui["execution_sandbox"]
        ),
        skill_history_selection_execution_artifact=(
            skill_history_selection_ui["execution_artifact"]
        ),
        skill_history_selection_download_storage_object_id=(
            skill_history_selection_ui["download_storage_object_id"]
        ),
        skill_history_selection_download_status=(
            skill_history_selection_ui["download_status"]
        ),
        skill_history_selection_downloaded_storage_object_id=(
            skill_history_selection_ui["downloaded_storage_object_id"]
        ),
        skill_history_selection_feedback_status=(
            skill_history_selection_ui["feedback_status"]
        ),
        skill_history_selection_feedback_api_seen=(
            skill_history_selection_ui["feedback_api_seen"]
        ),
        skill_history_selection_feedback_rating=(
            skill_history_selection_ui["feedback_rating"]
        ),
        skill_evidence_summary=skill_evidence_summary,
        skill_delivery_summary=skill_delivery_summary,
        skill_artifact_preview_text=skill_artifact_preview_text,
    )


def browser_workspace_navigation_url(
    config: LocalCloudPocVerificationConfig,
    tenant_id: str,
    user_id: str,
    workspace_id: str,
) -> str:
    if config.browser_workspace_url is None:
        return ""
    sensitive_params = {"accessToken", "access_token", "token", "password"}
    connection_params = {
        "tenantId": tenant_id,
        "userId": user_id,
        "workspaceId": workspace_id,
        "email": config.owner_email,
    }
    if config.browser_workspace_api_base_url is not None:
        connection_params["apiBase"] = config.browser_workspace_api_base_url

    parsed = urlparse(config.browser_workspace_url)
    replaced_params = set(connection_params) | sensitive_params
    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key not in replaced_params
    ]
    query_pairs.extend(
        (key, value)
        for key, value in connection_params.items()
        if value
    )
    return parsed._replace(query=urlencode(query_pairs)).geturl()


def verify_browser_workspace_bootstrap(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    session: dict[str, str],
    tenant_id: str,
    user_id: str,
    workspace_id: str,
) -> dict[str, str | bool | None]:
    type_inputs = [
        ("#api-base", config.browser_workspace_api_base_url or ""),
        ("#tenant-slug", config.tenant_slug),
        ("#owner-display-name", config.owner_display_name),
        ("#login-email", config.owner_email),
        ("#login-password", config.owner_password),
        ("#bootstrap-token", config.bootstrap_token),
    ]
    for selector, text in type_inputs:
        response = request_json(
            client,
            "POST",
            config.browser_base_url,
            "/actions",
            payload=session
            | {
                "action_type": "type",
                "selector": selector,
                "text": text,
            },
            headers=browser_controller_headers(config),
        )
        assert_status(
            response,
            {201},
            f"browser workspace bootstrap input failed for {selector}",
        )

    clicked = request_json(
        client,
        "POST",
        config.browser_base_url,
        "/actions",
        payload=session
        | {"action_type": "click", "selector": "#bootstrap-login-button"},
        headers=browser_controller_headers(config),
    )
    assert_status(clicked, {201}, "browser workspace bootstrap click failed")

    latest_status = ""
    attempts = max(config.run_status_poll_attempts, 1)
    for attempt in range(attempts):
        extracted = request_json(
            client,
            "POST",
            config.browser_base_url,
            "/actions",
            payload=session
            | {
                "action_type": "extract",
                "selector": "[data-bootstrap-status]",
            },
            headers=browser_controller_headers(config),
        )
        assert_status(
            extracted,
            {201},
            "browser workspace bootstrap status extraction failed",
        )
        latest_status = str(extracted.json_body().get("text") or "").strip()
        if latest_status == "Tenant ready":
            context = verify_browser_workspace_bootstrap_context(
                client,
                config,
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                workspace_id=workspace_id,
            )
            token_cleared = verify_browser_workspace_bootstrap_token_cleared(
                client,
                config,
                session,
            )
            return {
                "status": latest_status,
                "token_cleared": token_cleared,
                **context,
            }
        if attempt + 1 < attempts:
            time.sleep(config.browser_workspace_auth_poll_interval_seconds)
    raise RuntimeError(
        "browser workspace bootstrap did not reach Tenant ready status"
    )


def verify_browser_workspace_bootstrap_context(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    session: dict[str, str],
    tenant_id: str,
    user_id: str,
    workspace_id: str,
) -> dict[str, str | None]:
    expected = {
        "tenant_id": ("#tenant-id", tenant_id),
        "user_id": ("#user-id", user_id),
        "workspace_id": ("#workspace-id", workspace_id),
    }
    observed: dict[str, str | None] = {}
    for key, (selector, expected_value) in expected.items():
        extracted = request_json(
            client,
            "POST",
            config.browser_base_url,
            "/actions",
            payload=session
            | {
                "action_type": "extract",
                "selector": selector,
            },
            headers=browser_controller_headers(config),
        )
        assert_status(
            extracted,
            {201},
            f"browser workspace bootstrap context extraction failed for {selector}",
        )
        actual = str(extracted.json_body().get("text") or "").strip()
        if actual != expected_value:
            raise RuntimeError(
                "browser workspace bootstrap context mismatch for "
                f"{selector}: expected {expected_value}, got {actual or '<empty>'}"
            )
        observed[key] = actual
    return observed


def verify_browser_workspace_bootstrap_token_cleared(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    session: dict[str, str],
) -> bool:
    extracted = request_json(
        client,
        "POST",
        config.browser_base_url,
        "/actions",
        payload=session
        | {
            "action_type": "extract",
            "selector": "#bootstrap-token",
        },
        headers=browser_controller_headers(config),
    )
    assert_status(
        extracted,
        {201},
        "browser workspace bootstrap token extraction failed",
    )
    token_value = str(extracted.json_body().get("text") or "")
    if token_value:
        raise RuntimeError("browser workspace bootstrap token input was not cleared")
    return True


def verify_browser_workspace_login(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    session: dict[str, str],
    tenant_id: str,
    workspace_id: str,
) -> str:
    type_inputs = [
        ("#api-base", config.browser_workspace_api_base_url or ""),
        ("#tenant-id", tenant_id),
        ("#workspace-id", workspace_id),
        ("#login-email", config.owner_email),
        ("#login-password", config.owner_password),
    ]
    for selector, text in type_inputs:
        response = request_json(
            client,
            "POST",
            config.browser_base_url,
            "/actions",
            payload=session
            | {
                "action_type": "type",
                "selector": selector,
                "text": text,
            },
            headers=browser_controller_headers(config),
        )
        assert_status(response, {201}, f"browser workspace input failed for {selector}")

    clicked = request_json(
        client,
        "POST",
        config.browser_base_url,
        "/actions",
        payload=session | {"action_type": "click", "selector": "#login-button"},
        headers=browser_controller_headers(config),
    )
    assert_status(clicked, {201}, "browser workspace login click failed")

    latest_status = ""
    attempts = max(config.run_status_poll_attempts, 1)
    for attempt in range(attempts):
        extracted = request_json(
            client,
            "POST",
            config.browser_base_url,
            "/actions",
            payload=session
            | {
                "action_type": "extract",
                "selector": "[data-auth-status]",
            },
            headers=browser_controller_headers(config),
        )
        assert_status(extracted, {201}, "browser workspace auth status extraction failed")
        latest_status = str(extracted.json_body().get("text") or "").strip()
        if latest_status == "Bearer":
            return latest_status
        if attempt + 1 < attempts:
            time.sleep(config.browser_workspace_auth_poll_interval_seconds)
    raise RuntimeError(
        "browser workspace login did not reach Bearer auth status"
        f" (last status: {latest_status})"
    )


def verify_browser_workspace_readiness(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    session: dict[str, str],
) -> dict[str, str]:
    latest = {"status": "", "model": "", "sandbox": ""}
    attempts = max(config.browser_workspace_submit_poll_attempts, 1)
    for attempt in range(attempts):
        latest = {
            "status": extract_browser_workspace_text(
                client,
                config,
                session,
                "[data-readiness-status]",
                "readiness status",
            ),
            "model": extract_browser_workspace_text(
                client,
                config,
                session,
                "[data-readiness-model]",
                "readiness model",
            ),
            "sandbox": extract_browser_workspace_text(
                client,
                config,
                session,
                "[data-readiness-sandbox]",
                "readiness sandbox",
            ),
        }
        if not browser_workspace_readiness_loaded(latest):
            if attempt + 1 < attempts:
                time.sleep(config.browser_workspace_auth_poll_interval_seconds)
            continue
        if not config.require_model_execution:
            return latest
        if browser_workspace_ready_for_model_execution(latest):
            return latest
        if attempt + 1 < attempts:
            time.sleep(config.browser_workspace_auth_poll_interval_seconds)
    if config.require_model_execution:
        raise RuntimeError(
            "browser workspace readiness did not reach Preflight ready"
            f" (last status: {latest['status']};"
            f" model: {latest['model']}; sandbox: {latest['sandbox']})"
        )
    raise RuntimeError(
        "browser workspace readiness did not finish loading"
        f" (last status: {latest['status']})"
    )


def browser_workspace_readiness_loaded(readiness: dict[str, str]) -> bool:
    values = [value.lower() for value in readiness.values()]
    return all(readiness.values()) and not any(
        "checking" in value or "unchecked" in value for value in values
    )


def browser_workspace_ready_for_model_execution(readiness: dict[str, str]) -> bool:
    return (
        readiness["status"] == "Preflight ready"
        and readiness["model"] == "Model ready"
        and readiness["sandbox"].startswith(
            ("Sandbox ready:", "Sandbox PoC:", "Sandbox isolated:")
        )
    )


def extract_browser_workspace_text(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    session: dict[str, str],
    selector: str,
    label: str,
) -> str:
    extracted = request_json(
        client,
        "POST",
        config.browser_base_url,
        "/actions",
        payload=session | {"action_type": "extract", "selector": selector},
        headers=browser_controller_headers(config),
    )
    assert_status(extracted, {201}, f"browser workspace {label} extraction failed")
    return str(extracted.json_body().get("text") or "").strip()


def verify_browser_workspace_submit(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    session: dict[str, str],
) -> str:
    typed = request_json(
        client,
        "POST",
        config.browser_base_url,
        "/actions",
        payload=session
        | {
            "action_type": "type",
            "selector": "#composer-input",
            "text": config.browser_workspace_submit_message or "",
        },
        headers=browser_controller_headers(config),
    )
    assert_status(typed, {201}, "browser workspace composer input failed")

    clicked = request_json(
        client,
        "POST",
        config.browser_base_url,
        "/actions",
        payload=session | {"action_type": "click", "selector": "#send-button"},
        headers=browser_controller_headers(config),
    )
    assert_status(clicked, {201}, "browser workspace send click failed")

    latest_text = ""
    attempts = max(config.browser_workspace_submit_poll_attempts, 1)
    for attempt in range(attempts):
        extracted_texts = []
        for selector in [
            "[data-testid='conversation-log']",
            "[data-status-pill]",
            "[data-artifact-list]",
        ]:
            extracted = request_json(
                client,
                "POST",
                config.browser_base_url,
                "/actions",
                payload=session
                | {
                    "action_type": "extract",
                    "selector": selector,
                },
                headers=browser_controller_headers(config),
            )
            assert_status(
                extracted,
                {201},
                f"browser workspace extraction failed for {selector}",
            )
            selector_text = str(extracted.json_body().get("text") or "").strip()
            if selector_text:
                extracted_texts.append(selector_text)
            latest_text = "\n".join(extracted_texts)
            if config.browser_workspace_submit_expected_text in latest_text:
                return latest_text
        if attempt + 1 < attempts:
            time.sleep(config.browser_workspace_submit_poll_interval_seconds)
    raise RuntimeError(
        "browser workspace submit did not surface expected text"
        f" (expected: {config.browser_workspace_submit_expected_text})"
    )


def verify_browser_workspace_execution_model_route(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    session: dict[str, str],
    expected_execution_model_route: str | None = None,
) -> str:
    route = extract_browser_workspace_text(
        client,
        config,
        session,
        "[data-execution-model-route]",
        "execution model route",
    )
    blocked_values = {
        "",
        "No model route",
        "Model route pending",
    }
    if config.require_model_execution and route in blocked_values:
        raise RuntimeError(
            "browser workspace execution model route did not load"
        )
    if (
        config.require_model_execution
        and expected_execution_model_route is not None
        and route != expected_execution_model_route
    ):
        raise RuntimeError(
            "browser workspace execution model route did not match API evidence"
        )
    return route


def verify_browser_workspace_evidence_summary(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    session: dict[str, str],
) -> str:
    summary = extract_browser_workspace_text(
        client,
        config,
        session,
        "[data-evidence-summary]",
        "evidence summary",
    )
    if config.require_model_execution and summary != "Artifact delivery proven":
        raise RuntimeError(
            "browser workspace evidence did not prove artifact delivery"
            f" (last summary: {summary})"
        )
    return summary


def verify_browser_workspace_delivery_summary(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    session: dict[str, str],
) -> str:
    summary = extract_browser_workspace_text(
        client,
        config,
        session,
        "[data-delivery-summary]",
        "delivery summary",
    )
    if not summary.startswith("Ready to download"):
        raise RuntimeError(
            "browser workspace delivery summary did not prove downloadable artifact"
            f" (last summary: {summary})"
        )
    return summary


def verify_browser_workspace_delivery_chain(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    session: dict[str, str],
    expected_run_id: str | None = None,
    expected_sandbox_session_id: str | None = None,
    expected_artifact_storage_object_id: str | None = None,
    expected_terminal_storage_object_id: str | None = None,
    expected_browser_storage_object_id: str | None = None,
    require_terminal_storage: bool = True,
) -> dict[str, str | None]:
    chain = {
        "status": extract_browser_workspace_text(
            client,
            config,
            session,
            "[data-delivery-chain-status]",
            "delivery chain status",
        ),
        "run_id": extract_browser_workspace_text(
            client,
            config,
            session,
            "[data-delivery-chain-run]",
            "delivery chain run",
        ),
        "sandbox_session_id": extract_browser_workspace_text(
            client,
            config,
            session,
            "[data-delivery-chain-sandbox]",
            "delivery chain sandbox",
        ),
        "artifact_storage_object_id": extract_browser_workspace_text(
            client,
            config,
            session,
            "[data-delivery-chain-artifact-storage]",
            "delivery chain artifact storage",
        ),
        "terminal_storage_object_id": extract_browser_workspace_text(
            client,
            config,
            session,
            "[data-delivery-chain-terminal-storage]",
            "delivery chain terminal storage",
        ),
        "browser_storage_object_id": extract_browser_workspace_text(
            client,
            config,
            session,
            "[data-delivery-chain-browser-storage]",
            "delivery chain browser storage",
        ),
    }
    if config.require_model_execution and chain["status"] != "Delivery chain complete":
        raise RuntimeError(
            "browser workspace delivery chain did not complete"
            f" (last status: {chain['status']})"
        )
    required_labels = {
        "run": chain["run_id"],
        "sandbox": chain["sandbox_session_id"],
        "artifact storage": chain["artifact_storage_object_id"],
    }
    if require_terminal_storage:
        required_labels["terminal storage"] = chain["terminal_storage_object_id"]
    missing = [
        label
        for label, value in required_labels.items()
        if not value or value == "--"
    ]
    if config.require_model_execution and missing:
        raise RuntimeError(
            "browser workspace delivery chain missing " + ", ".join(missing)
        )
    expected_matches = {
        "run": (chain["run_id"], expected_run_id),
        "sandbox": (chain["sandbox_session_id"], expected_sandbox_session_id),
        "artifact storage": (
            chain["artifact_storage_object_id"],
            expected_artifact_storage_object_id,
        ),
        "terminal storage": (
            chain["terminal_storage_object_id"],
            expected_terminal_storage_object_id,
        ),
        "browser storage": (
            chain["browser_storage_object_id"],
            expected_browser_storage_object_id,
        ),
    }
    for label, (actual, expected) in expected_matches.items():
        if not config.require_model_execution or expected is None:
            continue
        if actual != expected:
            raise RuntimeError(
                f"browser workspace delivery chain {label} did not match API evidence"
            )
    return chain


def verify_browser_workspace_event_integrity(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    session: dict[str, str],
    expected_event_count: int | None = None,
    expected_event_sequence_label: str | None = None,
    expected_event_closure_label: str | None = None,
) -> dict[str, str | None]:
    integrity = {
        "status": extract_browser_workspace_text(
            client,
            config,
            session,
            "[data-event-integrity-status]",
            "event integrity status",
        ),
        "count": extract_browser_workspace_text(
            client,
            config,
            session,
            "[data-event-integrity-count]",
            "event integrity count",
        ),
        "sequence": extract_browser_workspace_text(
            client,
            config,
            session,
            "[data-event-integrity-sequence]",
            "event integrity sequence",
        ),
        "closure": extract_browser_workspace_text(
            client,
            config,
            session,
            "[data-event-integrity-closure]",
            "event integrity closure",
        ),
    }
    if config.require_model_execution:
        if integrity["status"] != "Event stream verified":
            raise RuntimeError(
                "browser workspace event integrity was not verified"
                f" (last status: {integrity['status']})"
            )
        expected_count_label = (
            f"{expected_event_count} events"
            if expected_event_count is not None
            else None
        )
        if (
            expected_count_label is not None
            and integrity["count"] != expected_count_label
        ):
            raise RuntimeError(
                "browser workspace event integrity count did not match API evidence"
            )
        if "not monotonic" in str(integrity["sequence"]).lower():
            raise RuntimeError(
                "browser workspace event integrity sequence was not monotonic"
            )
        if (
            expected_event_sequence_label is not None
            and integrity["sequence"] != expected_event_sequence_label
        ):
            raise RuntimeError(
                "browser workspace event integrity sequence did not match "
                "API evidence"
            )
        expected_closure_label = (
            expected_event_closure_label or "command -> artifact -> succeeded"
        )
        if integrity["closure"] != expected_closure_label:
            raise RuntimeError(
                "browser workspace event integrity closure did not match "
                "runtime path"
            )
    return integrity


def verify_browser_workspace_artifact_preview(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    session: dict[str, str],
) -> str:
    latest_text = ""
    attempts = max(config.browser_workspace_submit_poll_attempts, 1)
    for attempt in range(attempts):
        latest_text = extract_browser_workspace_text(
            client,
            config,
            session,
            "[data-artifact-preview-content]",
            "artifact preview content",
        )
        if config.model_artifact_required_text in latest_text:
            return latest_text
        if attempt + 1 < attempts:
            time.sleep(config.browser_workspace_submit_poll_interval_seconds)
    raise RuntimeError(
        "browser workspace artifact preview did not include required artifact text"
        f" (expected: {config.model_artifact_required_text})"
    )


def verify_browser_workspace_browser_capture(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    session: dict[str, str],
    expected_storage_object_id: str | None,
) -> dict[str, str | None]:
    if expected_storage_object_id is None:
        return {
            "storage_object_id": None,
            "preview_storage_object_id": None,
        }
    storage_object_id = extract_browser_workspace_text(
        client,
        config,
        session,
        "[data-browser-storage-object]",
        "browser storage object",
    )
    if storage_object_id != expected_storage_object_id:
        raise RuntimeError(
            "browser workspace browser storage object did not match API evidence"
        )
    preview_storage_object_id = extract_browser_workspace_text(
        client,
        config,
        session,
        "[data-browser-preview-storage-object]",
        "browser preview storage object",
    )
    if preview_storage_object_id != expected_storage_object_id:
        raise RuntimeError(
            "browser workspace browser preview storage object did not match "
            "API evidence"
        )
    return {
        "storage_object_id": storage_object_id,
        "preview_storage_object_id": preview_storage_object_id,
    }


def verify_browser_workspace_artifact_preview_storage_object(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    session: dict[str, str],
    expected_storage_object_id: str | None,
) -> str:
    storage_object_id = extract_browser_workspace_text(
        client,
        config,
        session,
        "[data-artifact-preview-storage-object]",
        "artifact preview storage object",
    )
    if expected_storage_object_id is not None and (
        storage_object_id != expected_storage_object_id
    ):
        raise RuntimeError(
            "browser workspace artifact preview storage object did not match "
            "API evidence"
        )
    return storage_object_id


def verify_browser_workspace_artifact_download(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    session: dict[str, str],
    expected_storage_object_id: str | None,
) -> dict[str, str | None]:
    if not expected_storage_object_id:
        raise RuntimeError(
            "browser workspace artifact download missing API storage evidence"
        )
    download_selector = f'[data-storage-object-id="{expected_storage_object_id}"]'
    download_clicked = request_json(
        client,
        "POST",
        config.browser_base_url,
        "/actions",
        payload=session | {"action_type": "click", "selector": download_selector},
        headers=browser_controller_headers(config),
    )
    assert_status(
        download_clicked,
        {201},
        "browser workspace artifact download failed",
    )
    download_status = extract_browser_workspace_text(
        client,
        config,
        session,
        "[data-artifact-download-status]",
        "artifact download status",
    )
    expected_download_status = f"Downloaded {config.model_artifact_required_name}"
    if download_status != expected_download_status:
        raise RuntimeError(
            "browser workspace artifact download status did not confirm completion"
            f" (expected: {expected_download_status}; got: {download_status})"
        )
    downloaded_storage_object_id = extract_browser_workspace_text(
        client,
        config,
        session,
        "[data-artifact-downloaded-storage-object]",
        "downloaded storage object",
    )
    if downloaded_storage_object_id != expected_storage_object_id:
        raise RuntimeError(
            "browser workspace artifact download did not expose the downloaded "
            "storage object id"
            f" (expected: {expected_storage_object_id}; "
            f"got: {downloaded_storage_object_id})"
        )
    return {
        "storage_object_id": expected_storage_object_id,
        "download_status": download_status,
        "downloaded_storage_object_id": downloaded_storage_object_id,
    }


def verify_browser_workspace_trace_summary(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    session: dict[str, str],
    expected_span_count: int,
    expected_event_count: int,
    expected_billing_count: int,
    expected_audit_count: int,
) -> dict[str, str]:
    summary = {
        "status": extract_browser_workspace_text(
            client,
            config,
            session,
            "[data-trace-status]",
            "trace status",
        ),
        "span_count": extract_browser_workspace_text(
            client,
            config,
            session,
            "[data-trace-span-count]",
            "trace span count",
        ),
        "event_count": extract_browser_workspace_text(
            client,
            config,
            session,
            "[data-trace-event-count]",
            "trace event count",
        ),
        "billing_count": extract_browser_workspace_text(
            client,
            config,
            session,
            "[data-trace-billing-count]",
            "trace billing count",
        ),
        "audit_count": extract_browser_workspace_text(
            client,
            config,
            session,
            "[data-trace-audit-count]",
            "trace audit count",
        ),
        "error": extract_browser_workspace_text(
            client,
            config,
            session,
            "[data-trace-error-classification]",
            "trace error classification",
        ),
    }
    expected = {
        "status": "Loaded",
        "span_count": str(expected_span_count),
        "event_count": str(expected_event_count),
        "billing_count": str(expected_billing_count),
        "audit_count": str(expected_audit_count),
        "error": "No error",
    }
    mismatches = [
        f"{key} expected {expected_value}, got {summary[key]}"
        for key, expected_value in expected.items()
        if summary[key] != expected_value
    ]
    if mismatches:
        raise RuntimeError(
            "browser workspace trace summary did not match run trace: "
            + "; ".join(mismatches)
        )
    return summary


def verify_browser_workspace_run_history_contains_skill_run(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    session: dict[str, str],
    run_id: str,
) -> dict[str, str]:
    if not run_id:
        raise RuntimeError("browser workspace run history check needs a run id")
    clicked = request_json(
        client,
        "POST",
        config.browser_base_url,
        "/actions",
        payload=session
        | {"action_type": "click", "selector": "[data-run-history-refresh]"},
        headers=browser_controller_headers(config),
    )
    assert_status(clicked, {201}, "browser workspace run history refresh failed")

    latest_status = ""
    latest_text = ""
    attempts = max(config.browser_workspace_submit_poll_attempts, 1)
    for attempt in range(attempts):
        latest_status = extract_browser_workspace_text(
            client,
            config,
            session,
            "[data-run-history-status]",
            "run history status",
        )
        latest_text = extract_browser_workspace_text(
            client,
            config,
            session,
            "[data-run-history-list]",
            "run history list",
        )
        if run_id in latest_text:
            return {"status": latest_status, "text": latest_text}
        if attempt + 1 < attempts:
            time.sleep(config.browser_workspace_submit_poll_interval_seconds)
    raise RuntimeError(
        "browser workspace run history did not include skill run"
        f" (run_id: {run_id}; status: {latest_status}; text: {latest_text})"
    )


def verify_browser_workspace_select_skill_run_from_history(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    session: dict[str, str],
    run_id: str,
    storage_object_id: str,
    command_output_storage_object_id: str,
    command_output_uri: str | None,
    expected_runtime_sandbox_session_id: str | None,
    expected_event_count: int | None,
    expected_event_sequence_label: str | None,
    expected_event_closure_label: str | None,
    expected_execution_model_route: str | None,
    headers: dict[str, str],
) -> dict[str, Any]:
    if not run_id:
        raise RuntimeError("browser workspace history selection needs a run id")
    if not storage_object_id:
        raise RuntimeError(
            "browser workspace history selection needs an artifact storage object id"
        )
    if not command_output_storage_object_id:
        raise RuntimeError(
            "browser workspace history selection needs a terminal storage object id"
        )
    selector = f'[data-run-history-id="{run_id}"]'
    clicked = request_json(
        client,
        "POST",
        config.browser_base_url,
        "/actions",
        payload=session | {"action_type": "click", "selector": selector},
        headers=browser_controller_headers(config),
    )
    assert_status(clicked, {201}, "browser workspace run history selection failed")

    latest_trace_status = ""
    attempts = max(config.browser_workspace_submit_poll_attempts, 1)
    for attempt in range(attempts):
        latest_trace_status = extract_browser_workspace_text(
            client,
            config,
            session,
            "[data-trace-status]",
            "selected history run trace status",
        )
        if latest_trace_status == "Loaded":
            break
        if attempt + 1 < attempts:
            time.sleep(config.browser_workspace_submit_poll_interval_seconds)
    else:
        raise RuntimeError(
            "browser workspace selected history run trace did not load"
            f" (run_id: {run_id}; last status: {latest_trace_status})"
        )

    runtime_state_status = extract_browser_workspace_text(
        client,
        config,
        session,
        "[data-runtime-state-status]",
        "selected history runtime state status",
    )
    runtime_sandbox_session = extract_browser_workspace_text(
        client,
        config,
        session,
        "[data-runtime-sandbox-session]",
        "selected history runtime sandbox session",
    )
    runtime_artifact_count = extract_browser_workspace_text(
        client,
        config,
        session,
        "[data-runtime-artifact-count]",
        "selected history runtime artifact count",
    )
    execution_summary = extract_browser_workspace_text(
        client,
        config,
        session,
        "[data-execution-summary]",
        "selected history execution summary",
    )
    execution_model_route = extract_browser_workspace_text(
        client,
        config,
        session,
        "[data-execution-model-route]",
        "selected history execution model route",
    )
    if (
        expected_execution_model_route is not None
        and execution_model_route != expected_execution_model_route
    ):
        raise RuntimeError(
            "browser workspace selected history execution model route did not match "
            "API evidence"
            f" (expected: {expected_execution_model_route}; "
            f"got: {execution_model_route})"
        )
    execution_sandbox = extract_browser_workspace_text(
        client,
        config,
        session,
        "[data-execution-sandbox]",
        "selected history execution sandbox",
    )
    execution_artifact = extract_browser_workspace_text(
        client,
        config,
        session,
        "[data-execution-artifact]",
        "selected history execution artifact",
    )
    expected = {
        "runtime_state_status": "succeeded",
        "execution_summary": "Artifact ready",
        "execution_sandbox": "Promoted",
    }
    observed = {
        "runtime_state_status": runtime_state_status,
        "execution_summary": execution_summary,
        "execution_sandbox": execution_sandbox,
    }
    mismatches = [
        f"{key} expected {expected_value}, got {observed[key]}"
        for key, expected_value in expected.items()
        if observed[key] != expected_value
    ]
    if not runtime_sandbox_session or runtime_sandbox_session == "--":
        mismatches.append("runtime_sandbox_session was not visible")
    if "promoted artifact" not in runtime_artifact_count:
        mismatches.append(
            "runtime_artifact_count did not show promoted sandbox artifacts"
        )
    if not execution_artifact.endswith("ready"):
        mismatches.append("execution_artifact did not show ready artifacts")
    if (
        expected_runtime_sandbox_session_id is not None
        and runtime_sandbox_session != expected_runtime_sandbox_session_id
    ):
        mismatches.append(
            "browser workspace selected history runtime sandbox did not match API evidence"
        )
    if mismatches:
        raise RuntimeError(
            "browser workspace selected history run did not show runtime closure: "
            + "; ".join(mismatches)
        )
    delivery_chain = verify_browser_workspace_delivery_chain(
        client,
        config,
        session,
        expected_run_id=run_id,
        expected_sandbox_session_id=expected_runtime_sandbox_session_id,
        expected_artifact_storage_object_id=storage_object_id,
        expected_terminal_storage_object_id=command_output_storage_object_id,
    )
    event_integrity = verify_browser_workspace_event_integrity(
        client,
        config,
        session,
        expected_event_count,
        expected_event_sequence_label,
        expected_event_closure_label,
    )
    terminal_text = verify_browser_workspace_terminal_summary(client, config, session)
    if command_output_uri is not None and command_output_uri not in terminal_text:
        raise RuntimeError(
            "browser workspace selected history terminal output URI did not "
            "match API evidence"
        )
    terminal_output_storage_object_id = (
        verify_browser_workspace_terminal_output_object(client, config, session)
    )
    if terminal_output_storage_object_id != command_output_storage_object_id:
        raise RuntimeError(
            "browser workspace selected history terminal output storage object "
            "did not match API evidence"
        )
    download_selector = f'[data-storage-object-id="{storage_object_id}"]'
    download_clicked = request_json(
        client,
        "POST",
        config.browser_base_url,
        "/actions",
        payload=session | {"action_type": "click", "selector": download_selector},
        headers=browser_controller_headers(config),
    )
    assert_status(
        download_clicked,
        {201},
        "browser workspace selected history artifact download failed",
    )
    download_status = extract_browser_workspace_text(
        client,
        config,
        session,
        "[data-artifact-download-status]",
        "selected history artifact download status",
    )
    expected_download_status = f"Downloaded {config.model_artifact_required_name}"
    if download_status != expected_download_status:
        raise RuntimeError(
            "browser workspace selected history artifact download status did not "
            f"confirm completion (expected: {expected_download_status}; "
            f"got: {download_status})"
        )
    downloaded_storage_object_id = extract_browser_workspace_text(
        client,
        config,
        session,
        "[data-artifact-downloaded-storage-object]",
        "selected history downloaded storage object",
    )
    if downloaded_storage_object_id != storage_object_id:
        raise RuntimeError(
            "browser workspace selected history artifact download did not expose "
            "the downloaded storage object id"
            f" (expected: {storage_object_id}; got: {downloaded_storage_object_id})"
        )
    feedback_clicked = request_json(
        client,
        "POST",
        config.browser_base_url,
        "/actions",
        payload=session | {"action_type": "click", "selector": "#run-feedback-positive"},
        headers=browser_controller_headers(config),
    )
    assert_status(
        feedback_clicked,
        {201},
        "browser workspace selected history feedback click failed",
    )
    feedback_status = extract_browser_workspace_text(
        client,
        config,
        session,
        "[data-run-feedback-status]",
        "selected history run feedback status",
    )
    if feedback_status != "Feedback recorded":
        raise RuntimeError(
            "browser workspace selected history feedback was not recorded"
            f" (last status: {feedback_status})"
        )
    feedback_api = inspect_selected_history_feedback(
        client,
        config,
        run_id,
        headers,
    )

    artifact_preview_text = verify_browser_workspace_artifact_preview(
        client,
        config,
        session,
    )
    previewed_storage_object_id = extract_browser_workspace_text(
        client,
        config,
        session,
        "[data-artifact-preview-storage-object]",
        "selected history artifact preview storage object",
    )
    if previewed_storage_object_id != storage_object_id:
        raise RuntimeError(
            "browser workspace selected history artifact preview did not expose "
            "the previewed storage object id"
            f" (expected: {storage_object_id}; got: {previewed_storage_object_id})"
        )

    return {
        "trace_status": latest_trace_status,
        "delivery_summary": verify_browser_workspace_delivery_summary(
            client,
            config,
            session,
        ),
        "delivery_chain_status": delivery_chain["status"],
        "delivery_chain_run_id": delivery_chain["run_id"],
        "delivery_chain_sandbox_session": delivery_chain["sandbox_session_id"],
        "delivery_chain_artifact_storage": (
            delivery_chain["artifact_storage_object_id"]
        ),
        "delivery_chain_terminal_storage": (
            delivery_chain["terminal_storage_object_id"]
        ),
        "event_integrity_status": event_integrity["status"],
        "event_integrity_count": event_integrity["count"],
        "event_integrity_sequence": event_integrity["sequence"],
        "event_integrity_closure": event_integrity["closure"],
        "terminal_text": terminal_text,
        "terminal_output_storage_object_id": terminal_output_storage_object_id,
        "artifact_preview_text": artifact_preview_text,
        "previewed_storage_object_id": previewed_storage_object_id,
        "runtime_state_status": runtime_state_status,
        "runtime_sandbox_session": runtime_sandbox_session,
        "runtime_artifact_count": runtime_artifact_count,
        "execution_summary": execution_summary,
        "execution_model_route": execution_model_route,
        "execution_sandbox": execution_sandbox,
        "execution_artifact": execution_artifact,
        "download_storage_object_id": storage_object_id,
        "download_status": download_status,
        "downloaded_storage_object_id": downloaded_storage_object_id,
        "feedback_status": feedback_status,
        "feedback_api_seen": feedback_api["seen"],
        "feedback_rating": feedback_api["rating"],
    }


def inspect_run_feedback(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    run_id: str,
    expected_rating: int,
    label: str,
    missing_target_label: str,
    headers: dict[str, str],
) -> dict[str, int | bool]:
    response = request_text(
        client,
        "GET",
        config.api_base_url,
        "/api/customer-success/feedback",
        headers=headers,
    )
    assert_status(response, {200}, f"{label} feedback API check failed")
    records = response.json_value()
    if not isinstance(records, list):
        raise RuntimeError(f"{label} feedback API did not return a feedback list")
    for record in records:
        if not isinstance(record, dict):
            continue
        if (
            record.get("run_id") == run_id
            and record.get("target_id") == run_id
            and record.get("target_type") == "run"
            and record.get("feedback_type") == "thumbs_rating"
        ):
            rating = record.get("rating")
            if rating != expected_rating:
                raise RuntimeError(
                    f"{label} feedback API returned unexpected rating"
                    f" (run_id: {run_id}; rating: {rating})"
                )
            return {"seen": True, "rating": rating}
    raise RuntimeError(
        f"{label} feedback API did not include {missing_target_label} feedback"
        f" (run_id: {run_id})"
    )


def inspect_selected_history_feedback(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    run_id: str,
    headers: dict[str, str],
) -> dict[str, int | bool]:
    return inspect_run_feedback(
        client,
        config,
        run_id,
        1,
        "selected history",
        "selected skill run",
        headers,
    )


def verify_browser_workspace_run_feedback(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    session: dict[str, str],
    expected_run_id: str | None,
    headers: dict[str, str],
) -> dict[str, int | str | bool | None]:
    clicked = request_json(
        client,
        "POST",
        config.browser_base_url,
        "/actions",
        payload=session | {"action_type": "click", "selector": "#run-feedback-negative"},
        headers=browser_controller_headers(config),
    )
    assert_status(clicked, {201}, "browser workspace run feedback click failed")

    latest_status = ""
    attempts = max(config.browser_workspace_submit_poll_attempts, 1)
    for attempt in range(attempts):
        latest_status = extract_browser_workspace_text(
            client,
            config,
            session,
            "[data-run-feedback-status]",
            "run feedback status",
        )
        if latest_status == "Feedback recorded":
            if expected_run_id is None:
                return {"status": latest_status, "seen": False, "rating": None}
            feedback_api = inspect_run_feedback(
                client,
                config,
                expected_run_id,
                -1,
                "browser workspace run",
                "current run",
                headers,
            )
            return {
                "status": latest_status,
                "seen": feedback_api["seen"],
                "rating": feedback_api["rating"],
            }
        if attempt + 1 < attempts:
            time.sleep(config.browser_workspace_submit_poll_interval_seconds)
    raise RuntimeError(
        "browser workspace run feedback was not recorded"
        f" (last status: {latest_status})"
    )


def verify_browser_workspace_candidate_generation(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    session: dict[str, str],
    expected_run_id: str | None,
    headers: dict[str, str],
) -> dict[str, int | str]:
    admin_clicked = request_json(
        client,
        "POST",
        config.browser_base_url,
        "/actions",
        payload=session
        | {"action_type": "click", "selector": "[data-workbench-view-toggle='admin']"},
        headers=browser_controller_headers(config),
    )
    assert_status(admin_clicked, {201}, "browser workspace admin view click failed")

    create_clicked = request_json(
        client,
        "POST",
        config.browser_base_url,
        "/actions",
        payload=session | {"action_type": "click", "selector": "#cs-create-eval-candidates"},
        headers=browser_controller_headers(config),
    )
    assert_status(
        create_clicked,
        {201},
        "browser workspace eval candidate generation click failed",
    )

    latest_status = ""
    attempts = max(config.browser_workspace_submit_poll_attempts, 1)
    for attempt in range(attempts):
        latest_status = extract_browser_workspace_text(
            client,
            config,
            session,
            "[data-cs-candidate-action-status]",
            "candidate generation status",
        )
        if latest_status.startswith("Eval candidates generated"):
            api_check = inspect_evaluation_candidates(
                client,
                config,
                expected_run_id,
                "pending_review",
                None,
                headers,
            )
            return {"status": latest_status, "api_count": api_check["count"]}
        if attempt + 1 < attempts:
            time.sleep(config.browser_workspace_submit_poll_interval_seconds)
    raise RuntimeError(
        "browser workspace eval candidate generation did not complete"
        f" (last status: {latest_status})"
    )


def inspect_evaluation_candidates(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    expected_run_id: str | None,
    expected_status: str,
    expected_evaluation_case_id: str | None,
    headers: dict[str, str],
    missing_label: str = "generated candidate",
) -> dict[str, int]:
    response = request_text(
        client,
        "GET",
        config.api_base_url,
        "/api/customer-success/evaluation-candidates",
        headers=headers,
    )
    assert_status(response, {200}, "evaluation candidate API check failed")
    records = response.json_value()
    if not isinstance(records, list):
        raise RuntimeError("evaluation candidate API did not return a candidate list")

    count = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        if expected_run_id is not None and record.get("source_run_id") != expected_run_id:
            continue
        if record.get("status") != expected_status:
            continue
        if (
            expected_evaluation_case_id is not None
            and record.get("evaluation_case_id") != expected_evaluation_case_id
        ):
            continue
        count += 1

    if count == 0:
        raise RuntimeError(
            "browser workspace evaluation candidate API did not include "
            f"{missing_label}"
            f" (run_id: {expected_run_id}; status: {expected_status})"
        )
    return {"count": count}


def evaluation_case_id_from_status(status: str) -> str:
    marker = "case "
    if marker not in status:
        raise RuntimeError(
            "browser workspace eval candidate review did not expose an evaluation case"
            f" (status: {status})"
        )
    return status.split(marker, 1)[1].strip()


def verify_browser_workspace_eval_candidate_review(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    session: dict[str, str],
    expected_run_id: str | None,
    headers: dict[str, str],
) -> dict[str, int | str]:
    clicked = request_json(
        client,
        "POST",
        config.browser_base_url,
        "/actions",
        payload=session | {"action_type": "click", "selector": "#cs-accept-eval-candidate"},
        headers=browser_controller_headers(config),
    )
    assert_status(clicked, {201}, "browser workspace eval candidate accept click failed")

    latest_status = ""
    attempts = max(config.browser_workspace_submit_poll_attempts, 1)
    for attempt in range(attempts):
        latest_status = extract_browser_workspace_text(
            client,
            config,
            session,
            "[data-cs-candidate-action-status]",
            "eval candidate review status",
        )
        if latest_status.startswith("Eval candidate accepted"):
            evaluation_case_id = evaluation_case_id_from_status(latest_status)
            api_check = inspect_evaluation_candidates(
                client,
                config,
                expected_run_id,
                "accepted",
                evaluation_case_id,
                headers,
                missing_label="accepted candidate",
            )
            return {"status": latest_status, "api_count": api_check["count"]}
        if attempt + 1 < attempts:
            time.sleep(config.browser_workspace_submit_poll_interval_seconds)
    raise RuntimeError(
        "browser workspace eval candidate review did not complete"
        f" (last status: {latest_status})"
    )


def verify_browser_workspace_missing_skill_feedback(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    session: dict[str, str],
    headers: dict[str, str],
) -> dict[str, int | str | None]:
    latest_status = ""
    for _ in range(config.browser_workspace_missing_skill_feedback_count):
        for selector, text in [
            ("#cs-missing-skill-name", config.browser_workspace_missing_skill_name),
            (
                "#cs-missing-skill-comment",
                config.browser_workspace_missing_skill_comment,
            ),
            ("#cs-missing-skill-solution-pack", config.browser_workspace_solution_pack_id),
        ]:
            typed = request_json(
                client,
                "POST",
                config.browser_base_url,
                "/actions",
                payload=session
                | {
                    "action_type": "type",
                    "selector": selector,
                    "text": text,
                },
                headers=browser_controller_headers(config),
            )
            assert_status(
                typed,
                {201},
                f"browser workspace missing skill input failed for {selector}",
            )
        clicked = request_json(
            client,
            "POST",
            config.browser_base_url,
            "/actions",
            payload=session
            | {"action_type": "click", "selector": "#cs-submit-missing-skill"},
            headers=browser_controller_headers(config),
        )
        assert_status(clicked, {201}, "browser workspace missing skill submit failed")

        attempts = max(config.browser_workspace_submit_poll_attempts, 1)
        for attempt in range(attempts):
            latest_status = extract_browser_workspace_text(
                client,
                config,
                session,
                "[data-cs-missing-skill-status]",
                "missing skill feedback status",
            )
            if latest_status == "Skill request recorded":
                break
            if attempt + 1 < attempts:
                time.sleep(config.browser_workspace_submit_poll_interval_seconds)
        else:
            raise RuntimeError(
                "browser workspace missing skill feedback was not recorded"
                f" (last status: {latest_status})"
            )
    api_check = inspect_missing_skill_feedback(client, config, headers)
    return {"status": latest_status, "api_count": api_check["count"]}


def inspect_missing_skill_feedback(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    headers: dict[str, str],
) -> dict[str, int]:
    response = request_text(
        client,
        "GET",
        config.api_base_url,
        "/api/customer-success/feedback",
        headers=headers,
    )
    assert_status(response, {200}, "missing skill feedback API check failed")
    records = response.json_value()
    if not isinstance(records, list):
        raise RuntimeError("missing skill feedback API did not return a feedback list")

    count = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        if (
            record.get("feedback_type") == "missing_skill"
            and record.get("target_type") == "solution_pack"
            and record.get("target_id") == config.browser_workspace_solution_pack_id
            and record.get("solution_pack_id")
            == config.browser_workspace_solution_pack_id
            and record.get("missing_skill_name")
            == config.browser_workspace_missing_skill_name
        ):
            count += 1

    required = config.browser_workspace_missing_skill_feedback_count
    if count < required:
        raise RuntimeError(
            "browser workspace missing skill feedback API did not include enough records"
            f" (expected at least: {required}; got: {count})"
        )
    return {"count": count}


def verify_browser_workspace_pack_candidate_generation(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    session: dict[str, str],
    headers: dict[str, str],
) -> dict[str, int | str]:
    create_clicked = request_json(
        client,
        "POST",
        config.browser_base_url,
        "/actions",
        payload=session | {"action_type": "click", "selector": "#cs-create-pack-candidates"},
        headers=browser_controller_headers(config),
    )
    assert_status(
        create_clicked,
        {201},
        "browser workspace pack candidate generation click failed",
    )

    latest_status = ""
    attempts = max(config.browser_workspace_submit_poll_attempts, 1)
    for attempt in range(attempts):
        latest_status = extract_browser_workspace_text(
            client,
            config,
            session,
            "[data-cs-candidate-action-status]",
            "pack candidate generation status",
        )
        if latest_status.startswith("Pack candidates generated"):
            api_check = inspect_solution_pack_candidates(
                client,
                config,
                "pending_review",
                None,
                headers,
            )
            return {"status": latest_status, "api_count": api_check["count"]}
        if attempt + 1 < attempts:
            time.sleep(config.browser_workspace_submit_poll_interval_seconds)
    raise RuntimeError(
        "browser workspace pack candidate generation did not complete"
        f" (last status: {latest_status})"
    )


def inspect_solution_pack_candidates(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    expected_status: str,
    expected_publication_draft_id: str | None,
    headers: dict[str, str],
    missing_label: str = "generated candidate",
) -> dict[str, int]:
    response = request_text(
        client,
        "GET",
        config.api_base_url,
        "/api/customer-success/solution-pack-candidates",
        headers=headers,
    )
    assert_status(response, {200}, "solution pack candidate API check failed")
    records = response.json_value()
    if not isinstance(records, list):
        raise RuntimeError("solution pack candidate API did not return a candidate list")

    count = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        if record.get("solution_pack_id") != config.browser_workspace_solution_pack_id:
            continue
        if record.get("requested_skill_name") != config.browser_workspace_missing_skill_name:
            continue
        if record.get("status") != expected_status:
            continue
        if (
            expected_publication_draft_id is not None
            and record.get("publication_draft_id") != expected_publication_draft_id
        ):
            continue
        count += 1

    if count == 0:
        raise RuntimeError(
            "browser workspace solution pack candidate API did not include "
            f"{missing_label}"
            f" (solution_pack_id: {config.browser_workspace_solution_pack_id}; "
            f"skill: {config.browser_workspace_missing_skill_name}; "
            f"status: {expected_status})"
        )
    return {"count": count}


def publication_draft_id_from_status(status: str) -> str:
    marker = "draft "
    if marker not in status:
        raise RuntimeError(
            "browser workspace pack candidate review did not expose a publication draft"
            f" (status: {status})"
        )
    return status.split(marker, 1)[1].strip()


def verify_browser_workspace_pack_candidate_review(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    session: dict[str, str],
    headers: dict[str, str],
) -> dict[str, int | str]:
    clicked = request_json(
        client,
        "POST",
        config.browser_base_url,
        "/actions",
        payload=session | {"action_type": "click", "selector": "#cs-accept-pack-candidate"},
        headers=browser_controller_headers(config),
    )
    assert_status(clicked, {201}, "browser workspace pack candidate accept click failed")

    latest_status = ""
    attempts = max(config.browser_workspace_submit_poll_attempts, 1)
    for attempt in range(attempts):
        latest_status = extract_browser_workspace_text(
            client,
            config,
            session,
            "[data-cs-candidate-action-status]",
            "pack candidate review status",
        )
        if latest_status.startswith("Pack candidate accepted"):
            publication_draft_id = publication_draft_id_from_status(latest_status)
            api_check = inspect_solution_pack_candidates(
                client,
                config,
                "accepted",
                publication_draft_id,
                headers,
                missing_label="accepted candidate",
            )
            return {
                "status": latest_status,
                "api_count": api_check["count"],
                "publication_draft_id": publication_draft_id,
            }
        if attempt + 1 < attempts:
            time.sleep(config.browser_workspace_submit_poll_interval_seconds)
    raise RuntimeError(
        "browser workspace pack candidate review did not complete"
        f" (last status: {latest_status})"
    )


def verify_browser_workspace_solution_pack_draft_lifecycle(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    session: dict[str, str],
    publication_draft_id: str,
    headers: dict[str, str],
) -> dict[str, bool | str]:
    wait_for_browser_workspace_draft_status(
        client,
        config,
        session,
        "Status: draft",
        "solution pack draft ready status",
    )
    for selector, text in [
        ("#cs-draft-skill", config.browser_workspace_draft_skill_name),
        ("#cs-draft-summary", config.browser_workspace_draft_summary),
        ("#cs-draft-pack-version", config.browser_workspace_draft_pack_version),
        (
            "#cs-draft-skill-manifest",
            config.browser_workspace_draft_skill_manifest_json,
        ),
    ]:
        typed = request_json(
            client,
            "POST",
            config.browser_base_url,
            "/actions",
            payload=session
            | {
                "action_type": "type",
                "selector": selector,
                "text": text,
            },
            headers=browser_controller_headers(config),
        )
        assert_status(
            typed,
            {201},
            f"browser workspace solution pack draft input failed for {selector}",
        )

    draft_steps = [
        ("#cs-draft-save", "Draft saved", "save"),
        ("#cs-draft-submit", "Draft in review", "submit"),
        ("#cs-draft-approve", "Draft approved", "approve"),
        ("#cs-draft-apply", "Draft applied", "apply"),
    ]
    latest_status = ""
    for selector, expected_status, label in draft_steps:
        clicked = request_json(
            client,
            "POST",
            config.browser_base_url,
            "/actions",
            payload=session | {"action_type": "click", "selector": selector},
            headers=browser_controller_headers(config),
        )
        assert_status(clicked, {201}, f"browser workspace draft {label} click failed")
        latest_status = wait_for_browser_workspace_draft_status(
            client,
            config,
            session,
            expected_status,
            f"solution pack draft {label} status",
        )
    api_check = inspect_solution_pack_publication_drafts(
        client,
        config,
        publication_draft_id,
        "applied",
        True,
        headers,
        missing_label="applied draft",
    )
    return {
        "status": latest_status,
        "api_status": api_check["status"],
        "production_change_applied": api_check["production_change_applied"],
    }


def inspect_solution_pack_publication_drafts(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    expected_publication_draft_id: str,
    expected_status: str,
    expected_production_change_applied: bool,
    headers: dict[str, str],
    missing_label: str,
) -> dict[str, bool | str]:
    response = request_text(
        client,
        "GET",
        config.api_base_url,
        "/api/customer-success/solution-pack-drafts",
        headers=headers,
    )
    assert_status(response, {200}, "solution pack publication draft API check failed")
    records = response.json_value()
    if not isinstance(records, list):
        raise RuntimeError("solution pack publication draft API did not return a draft list")

    expected_skill_ids = set(draft_skill_ids(config))
    for record in records:
        if not isinstance(record, dict):
            continue
        if record.get("id") != expected_publication_draft_id:
            continue
        if record.get("solution_pack_id") != config.browser_workspace_solution_pack_id:
            continue
        if record.get("requested_skill_name") != config.browser_workspace_draft_skill_name:
            continue
        if record.get("proposed_change_summary") != config.browser_workspace_draft_summary:
            continue
        if record.get("proposed_pack_version") != config.browser_workspace_draft_pack_version:
            continue
        if record.get("status") != expected_status:
            continue
        if record.get("production_change_applied") is not expected_production_change_applied:
            continue
        if not expected_skill_ids.issubset(solution_pack_draft_skill_ids(record)):
            continue
        return {
            "status": str(record["status"]),
            "production_change_applied": bool(record["production_change_applied"]),
        }

    raise RuntimeError(
        "browser workspace solution pack draft API did not include "
        f"{missing_label}"
        f" (publication_draft_id: {expected_publication_draft_id}; "
        f"solution_pack_id: {config.browser_workspace_solution_pack_id}; "
        f"status: {expected_status}; "
        f"production_change_applied: {expected_production_change_applied})"
    )


def solution_pack_draft_skill_ids(record: dict) -> set[str]:
    skill_ids: set[str] = set()
    single_manifest = record.get("proposed_skill_manifest")
    if isinstance(single_manifest, dict) and isinstance(single_manifest.get("id"), str):
        skill_ids.add(single_manifest["id"])
    manifests = record.get("proposed_skill_manifests")
    if isinstance(manifests, list):
        for manifest in manifests:
            if isinstance(manifest, dict) and isinstance(manifest.get("id"), str):
                skill_ids.add(manifest["id"])
    return skill_ids


def verify_browser_workspace_solution_pack_install(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    session: dict[str, str],
    workspace_id: str,
    headers: dict[str, str],
) -> dict[str, bool | int | str]:
    for selector, error_context in [
        (
            "[data-solution-pack-refresh]",
            "browser workspace solution pack refresh click failed",
        ),
        (
            "[data-solution-pack-id]",
            "browser workspace solution pack select click failed",
        ),
        (
            "#install-solution-pack-button",
            "browser workspace solution pack install click failed",
        ),
    ]:
        clicked = request_json(
            client,
            "POST",
            config.browser_base_url,
            "/actions",
            payload=session | {"action_type": "click", "selector": selector},
            headers=browser_controller_headers(config),
        )
        assert_status(clicked, {201}, error_context)

    latest_status = ""
    attempts = max(config.browser_workspace_submit_poll_attempts, 1)
    for attempt in range(attempts):
        latest_status = extract_browser_workspace_text(
            client,
            config,
            session,
            "[data-solution-pack-install-status]",
            "solution pack install status",
        )
        if latest_status.startswith("Solution pack installed"):
            api_check = inspect_solution_pack_workspace_installation(
                client,
                config,
                workspace_id,
                headers,
            )
            return {
                "status": latest_status,
                "api_seen": api_check["seen"],
                "skill_count": api_check["skill_count"],
            }
        if attempt + 1 < attempts:
            time.sleep(config.browser_workspace_submit_poll_interval_seconds)
    raise RuntimeError(
        "browser workspace solution pack install did not complete"
        f" (last status: {latest_status})"
    )


def inspect_solution_pack_workspace_installation(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    workspace_id: str,
    headers: dict[str, str],
) -> dict[str, bool | int]:
    skill_ids = draft_skill_ids(config)
    if not skill_ids:
        raise RuntimeError("solution pack installation verification has no draft skill ids")
    workspace_path = quote(workspace_id, safe="")

    installations = request_list(
        client,
        "GET",
        config.api_base_url,
        "/api/solution-pack-installations",
        headers,
        "browser workspace solution pack installation evidence",
    )
    installation_visible = any(
        item.get("pack_id") == config.browser_workspace_solution_pack_id
        and item.get("version") == config.browser_workspace_draft_pack_version
        and item.get("status") == "installed"
        and workspace_id in list(item.get("workspace_ids") or [])
        and set(skill_ids).issubset(set(str(skill) for skill in item.get("installed_skill_ids") or []))
        for item in installations
    )

    workspace_skills = request_list(
        client,
        "GET",
        config.api_base_url,
        f"/api/workspaces/{workspace_path}/skills",
        headers,
        "browser workspace skill installation evidence",
    )
    installed_workspace_skill_ids = {
        str(item.get("skill_id"))
        for item in workspace_skills
        if item.get("status") == "installed" and item.get("invocation_ready") is True
    }
    if not installation_visible or not set(skill_ids).issubset(installed_workspace_skill_ids):
        raise RuntimeError(
            "browser workspace solution pack installation API did not include "
            "installed skill"
            f" (solution_pack_id: {config.browser_workspace_solution_pack_id}; "
            f"workspace_id: {workspace_id}; "
            f"version: {config.browser_workspace_draft_pack_version})"
        )
    return {"seen": True, "skill_count": len(skill_ids)}


def verify_browser_workspace_skill_ready(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    session: dict[str, str],
) -> str:
    clicked = request_json(
        client,
        "POST",
        config.browser_base_url,
        "/actions",
        payload=session | {"action_type": "click", "selector": "[data-skills-refresh]"},
        headers=browser_controller_headers(config),
    )
    assert_status(clicked, {201}, "browser workspace skills refresh click failed")

    latest_status = ""
    attempts = max(config.browser_workspace_submit_poll_attempts, 1)
    for attempt in range(attempts):
        latest_status = extract_browser_workspace_text(
            client,
            config,
            session,
            "[data-skill-invoke-status]",
            "skill invoke status",
        )
        if latest_status.startswith("Ready:"):
            return latest_status
        if attempt + 1 < attempts:
            time.sleep(config.browser_workspace_submit_poll_interval_seconds)
    raise RuntimeError(
        "browser workspace skill was not ready after solution pack install"
        f" (last status: {latest_status})"
    )


def verify_browser_workspace_skill_invoke(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    session: dict[str, str],
) -> str:
    clicked = request_json(
        client,
        "POST",
        config.browser_base_url,
        "/actions",
        payload=session | {"action_type": "click", "selector": "#invoke-skill-button"},
        headers=browser_controller_headers(config),
    )
    assert_status(clicked, {201}, "browser workspace skill invoke click failed")

    latest_status = ""
    attempts = max(config.browser_workspace_submit_poll_attempts, 1)
    for attempt in range(attempts):
        latest_status = extract_browser_workspace_text(
            client,
            config,
            session,
            "[data-skill-invoke-status]",
            "skill invoke run status",
        )
        if latest_status.startswith("Run "):
            return latest_status
        if attempt + 1 < attempts:
            time.sleep(config.browser_workspace_submit_poll_interval_seconds)
    raise RuntimeError(
        "browser workspace skill invoke did not create a run"
        f" (last status: {latest_status})"
    )


def wait_for_browser_workspace_draft_status(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    session: dict[str, str],
    expected_status: str,
    label: str,
) -> str:
    latest_status = ""
    attempts = max(config.browser_workspace_submit_poll_attempts, 1)
    for attempt in range(attempts):
        latest_status = extract_browser_workspace_text(
            client,
            config,
            session,
            "[data-cs-draft-status]",
            label,
        )
        if latest_status == expected_status:
            return latest_status
        if attempt + 1 < attempts:
            time.sleep(config.browser_workspace_submit_poll_interval_seconds)
    raise RuntimeError(
        f"browser workspace {label} did not reach {expected_status}"
        f" (last status: {latest_status})"
    )


def verify_browser_workspace_terminal_summary(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    session: dict[str, str],
) -> str:
    latest_text = ""
    attempts = max(config.browser_workspace_submit_poll_attempts, 1)
    for attempt in range(attempts):
        latest_text = extract_browser_workspace_text(
            client,
            config,
            session,
            "[data-terminal-output]",
            "terminal output",
        )
        if (
            "stdout " in latest_text
            and "stderr " in latest_text
            and " bytes" in latest_text
        ):
            return latest_text
        if attempt + 1 < attempts:
            time.sleep(config.browser_workspace_submit_poll_interval_seconds)
    raise RuntimeError(
        "browser workspace terminal did not surface safe sandbox command summary"
        f" (last terminal text: {latest_text})"
    )


def verify_browser_workspace_terminal_output_object(
    client: LocalCloudPocHttpClient,
    config: LocalCloudPocVerificationConfig,
    session: dict[str, str],
) -> str:
    latest_text = ""
    attempts = max(config.browser_workspace_submit_poll_attempts, 1)
    for attempt in range(attempts):
        latest_text = extract_browser_workspace_text(
            client,
            config,
            session,
            "[data-terminal-output-storage-object]",
            "terminal output storage object",
        )
        if latest_text and latest_text != "--":
            return latest_text
        if attempt + 1 < attempts:
            time.sleep(config.browser_workspace_submit_poll_interval_seconds)
    raise RuntimeError(
        "browser workspace terminal did not surface sandbox output storage object"
        f" (last storage object: {latest_text})"
    )


def request_json(
    client: LocalCloudPocHttpClient,
    method: str,
    base_url: str,
    path: str,
    payload: dict | None = None,
    headers: dict | None = None,
) -> LocalCloudPocHttpResponse:
    response = request_text(client, method, base_url, path, payload, headers)
    if response.body:
        response.json_body()
    return response


def request_text(
    client: LocalCloudPocHttpClient,
    method: str,
    base_url: str,
    path: str,
    payload: dict | None = None,
    headers: dict | None = None,
) -> LocalCloudPocHttpResponse:
    return client.request(method, f"{base_url.rstrip('/')}{path}", payload, headers)


def assert_status(
    response: LocalCloudPocHttpResponse,
    expected_statuses: set[int],
    message: str,
) -> None:
    if response.status_code not in expected_statuses:
        raise RuntimeError(
            f"{message}: HTTP {response.status_code}: {safe_response_body_excerpt(response)}"
        )


def safe_response_body_excerpt(response: LocalCloudPocHttpResponse) -> str:
    redacted, _ = redact_text_entry(
        "local-cloud-poc-response",
        response.body[:500],
    )
    return redacted


def safe_result_json(value: Any) -> str:
    rendered = json.dumps(value, sort_keys=True)
    redacted, _ = redact_text_entry("local-cloud-poc-result", rendered)
    return redacted


def write_safe_result_json(output_path: Path | str, value: Any) -> str:
    output = safe_result_json(value)
    atomic_write_text(Path(output_path), f"{output}\n", encoding="utf-8")
    return output


def main(argv: list[str] | None = None) -> int:
    config = parse_args(argv)
    result = verify_local_cloud_poc(config)
    payload = result.model_dump(mode="json")
    if config.output_path:
        write_safe_result_json(config.output_path, payload)
    else:
        print(safe_result_json(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
