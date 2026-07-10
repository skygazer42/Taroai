from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class EventStreamVerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_base_url: str | None = Field(default=None, min_length=1)
    run_id: str | None = Field(default=None, min_length=1)
    first_event_sequence: int | None = Field(default=None, ge=0)
    stream_opened: bool
    event_id_received: bool
    after_sequence_replay_succeeded: bool
    last_event_id_replay_succeeded: bool
    tenant_scope_enforced: bool
    safe_payload_confirmed: bool


class AuditWriteVerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_base_url: str | None = Field(default=None, min_length=1)
    run_id: str | None = Field(default=None, min_length=1)
    write_succeeded: bool
    read_back_succeeded: bool
    tenant_scope_enforced: bool
    sensitive_metadata_redacted: bool


class SandboxLifecycleVerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    session_created: bool
    command_executed: bool
    session_destroyed: bool
    output_redacted: bool
    session_destroy_confirmed: bool = False
    post_destroy_command_blocked: bool = False
    command_scope_enforced: bool = False
    file_scope_enforced: bool = False
    file_read_scope_enforced: bool = False
    snapshot_scope_enforced: bool = False
    artifact_path: str | None = Field(default=None, min_length=1)
    artifact_listed: bool = False
    artifact_downloaded: bool = False
    downloaded_artifact_content_length: int = Field(default=0, ge=0)
    capabilities_checked: bool = False
    network_isolation_declared: bool = False
    filesystem_isolation_declared: bool = False
    resource_limits_declared: bool = False
    destroy_supported_declared: bool = False
    session_ttl_enforced_declared: bool = False
    runtime_isolation_declared: bool = False
    image_policy_enforced_declared: bool = False
    allowed_image_count: int = Field(default=0, ge=0)
    max_session_ttl_seconds_declared: bool = False
    max_sessions_declared: bool = False
    max_sessions_per_tenant_declared: bool = False
    max_sessions_per_run_declared: bool = False
    session_listed: bool = False
    tenant_session_scope_enforced: bool = False
    auth_challenge_enforced: bool = False
    auth_tenant_session_list_challenge_enforced: bool = False
    auth_global_session_list_challenge_enforced: bool = False
    auth_capabilities_challenge_enforced: bool = False


class BrowserControllerVerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    capabilities_checked: bool = False
    session_ttl_enforced_declared: bool = False
    max_session_ttl_seconds_declared: bool = False
    max_sessions_declared: bool = False
    max_sessions_per_tenant_declared: bool = False
    max_sessions_per_run_declared: bool = False
    navigation_allowlist_enforced_declared: bool = False
    navigation_allowed_host_count: int = Field(default=0, ge=0)
    session_opened: bool
    action_executed: bool
    session_deleted: bool
    session_delete_confirmed: bool = False
    duplicate_session_rejected: bool = False
    action_scope_enforced: bool = False
    session_read_scope_enforced: bool = False
    session_delete_scope_enforced: bool = False
    session_listed: bool = False
    tenant_session_scope_enforced: bool = False
    screenshot_or_extract_verified: bool
    screenshot_uri: str | None = Field(default=None, min_length=1)
    screenshot_content_length: int = Field(default=0, ge=0)
    extract_text_length: int = Field(default=0, ge=0)
    auth_challenge_enforced: bool = False
    auth_tenant_session_list_challenge_enforced: bool = False
    auth_global_session_list_challenge_enforced: bool = False
    auth_capabilities_challenge_enforced: bool = False
    output_redacted: bool


class RestoreDrillVerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    drill_id: str = Field(min_length=1)
    backup_manifest_generated: bool
    restore_order_executed: bool
    database_restore_verified: bool
    object_storage_restore_verified: bool
    redis_restore_or_rebuild_verified: bool
    config_restore_verified: bool
    post_restore_validation_passed: bool
    rpo_minutes: int = Field(ge=0)
    rto_minutes: int = Field(ge=0)


class RestoreDrillVerificationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    drill_id: str = Field(min_length=1)
    backup_manifest_path: Path
    executed_restore_order: list[str] = Field(min_length=1)
    migration_plan_path: Path
    object_storage_verification_path: Path
    redis_queue_verification_path: Path | None = None
    config_restored: bool = False
    post_restore_checks_passed: bool = False
    rpo_minutes: int = Field(ge=0)
    rto_minutes: int = Field(ge=0)
