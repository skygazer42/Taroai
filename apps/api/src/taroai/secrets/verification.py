import argparse
import json
import os
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from taroai.secrets.models import SecretRef, SecretScope
from taroai.secrets.service import (
    AwsSecretsManagerConfig,
    AwsSecretsManagerSecretService,
    InMemorySecretService,
    SecretAccessDeniedError,
)


class SecretManagerVerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: str = Field(min_length=1)
    reference_checked: bool
    lease_created: bool
    read_succeeded: bool
    scoped_context_enforced: bool
    output_redacted: bool
    secret_value_exposed: bool = False


class SecretManagerVerificationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: Literal["memory", "aws_secrets_manager"] = Field(
        default_factory=lambda: os.environ.get(
            "TAROAI_SECRET_SERVICE_BACKEND",
            "memory",
        ),
    )
    region_name: str = Field(
        default_factory=lambda: os.environ.get(
            "TAROAI_SECRET_SERVICE_REGION",
            "us-east-1",
        ),
        min_length=1,
    )
    endpoint_url: str | None = Field(
        default_factory=lambda: os.environ.get("TAROAI_SECRET_SERVICE_ENDPOINT_URL") or None
    )
    secret_name_prefix: str = Field(
        default_factory=lambda: os.environ.get(
            "TAROAI_SECRET_SERVICE_NAME_PREFIX",
            "taroai",
        ),
        min_length=1,
    )
    kms_key_id: str | None = Field(
        default_factory=lambda: os.environ.get("TAROAI_SECRET_SERVICE_KMS_KEY_ID") or None
    )
    tenant_id: str = Field(default="tenant_secret_verify", min_length=1)
    workspace_id: str = Field(default="workspace_secret_verify", min_length=1)
    run_id: str = Field(default_factory=lambda: f"run_secret_verify_{uuid4().hex[:12]}")
    step_id: str = Field(default="step_secret_verify", min_length=1)
    session_id: str = Field(default="session_secret_verify", min_length=1)
    tool_name: str = Field(default="secret.verify", min_length=1)
    action: str = Field(default="read", min_length=1)
    lease_ttl_seconds: int = Field(default=60, ge=1)
    secret_name: str = Field(default="taroai-secret-manager-verification", min_length=1)
    secret_value: str = Field(
        default_factory=lambda: os.environ.get(
            "TAROAI_SECRET_MANAGER_VERIFICATION_VALUE",
            f"secret-manager-verification-{uuid4().hex}",
        ),
        min_length=1,
        exclude=True,
        repr=False,
    )


def parse_args(argv: list[str] | None = None) -> SecretManagerVerificationConfig:
    parser = argparse.ArgumentParser(
        description="Verify configured secret manager scoped lease behavior."
    )
    parser.add_argument(
        "--backend",
        default=os.environ.get("TAROAI_SECRET_SERVICE_BACKEND", "memory"),
        choices=["memory", "aws_secrets_manager"],
    )
    parser.add_argument(
        "--region-name",
        default=os.environ.get("TAROAI_SECRET_SERVICE_REGION", "us-east-1"),
    )
    parser.add_argument(
        "--endpoint-url",
        default=os.environ.get("TAROAI_SECRET_SERVICE_ENDPOINT_URL", ""),
    )
    parser.add_argument(
        "--secret-name-prefix",
        default=os.environ.get("TAROAI_SECRET_SERVICE_NAME_PREFIX", "taroai"),
    )
    parser.add_argument(
        "--kms-key-id",
        default=os.environ.get("TAROAI_SECRET_SERVICE_KMS_KEY_ID", ""),
    )
    parser.add_argument("--tenant-id", default="tenant_secret_verify")
    parser.add_argument("--workspace-id", default="workspace_secret_verify")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--step-id", default="step_secret_verify")
    parser.add_argument("--session-id", default="session_secret_verify")
    parser.add_argument("--tool-name", default="secret.verify")
    parser.add_argument("--action", default="read")
    parser.add_argument("--lease-ttl-seconds", type=int, default=60)
    parser.add_argument("--secret-name", default="taroai-secret-manager-verification")
    parser.add_argument(
        "--secret-value",
        default=None,
        help=(
            "Verification secret value. Prefer --secret-value-env-var or "
            "TAROAI_SECRET_MANAGER_VERIFICATION_VALUE."
        ),
    )
    parser.add_argument(
        "--secret-value-env-var",
        default=os.environ.get("TAROAI_SECRET_MANAGER_VERIFICATION_VALUE_ENV_VAR", ""),
    )
    parsed = parser.parse_args(argv)
    config_data = {
        "backend": parsed.backend,
        "region_name": parsed.region_name,
        "endpoint_url": parsed.endpoint_url or None,
        "secret_name_prefix": parsed.secret_name_prefix,
        "kms_key_id": parsed.kms_key_id or None,
        "tenant_id": parsed.tenant_id,
        "workspace_id": parsed.workspace_id,
        "step_id": parsed.step_id,
        "session_id": parsed.session_id,
        "tool_name": parsed.tool_name,
        "action": parsed.action,
        "lease_ttl_seconds": parsed.lease_ttl_seconds,
        "secret_name": parsed.secret_name,
        "secret_value": resolve_secret_value(
            explicit_value=parsed.secret_value,
            env_var_name=parsed.secret_value_env_var,
        ),
    }
    if parsed.run_id is not None:
        config_data["run_id"] = parsed.run_id
    return SecretManagerVerificationConfig(**config_data)


def resolve_secret_value(
    explicit_value: str | None,
    env_var_name: str | None,
) -> str:
    if explicit_value is not None and explicit_value:
        return explicit_value
    if env_var_name is not None and env_var_name.strip():
        value = os.environ.get(env_var_name.strip(), "")
        if value:
            return value
    env_value = os.environ.get("TAROAI_SECRET_MANAGER_VERIFICATION_VALUE", "")
    if env_value:
        return env_value
    return f"secret-manager-verification-{uuid4().hex}"


def verify_secret_manager(
    config: SecretManagerVerificationConfig,
    service=None,
) -> SecretManagerVerificationResult:
    secret_service = service or build_secret_service(config)
    secret: SecretRef | None = None
    try:
        secret = secret_service.create_secret(
            tenant_id=config.tenant_id,
            workspace_id=config.workspace_id,
            name=config.secret_name,
            value=config.secret_value,
            scope=SecretScope(
                tenant_id=config.tenant_id,
                workspace_id=config.workspace_id,
                allowed_tool_names=[config.tool_name],
                actions=[config.action],
            ),
        )
        lease = secret_service.create_lease(
            tenant_id=config.tenant_id,
            workspace_id=config.workspace_id,
            secret_id=secret.id,
            tool_name=config.tool_name,
            actions=[config.action],
            ttl_seconds=config.lease_ttl_seconds,
            run_id=config.run_id,
            step_id=config.step_id,
            session_id=config.session_id,
        )
        value = secret_service.resolve_lease_value(
            tenant_id=config.tenant_id,
            workspace_id=config.workspace_id,
            run_id=config.run_id,
            step_id=config.step_id,
            session_id=config.session_id,
            lease_token=lease.lease_token,
            tool_name=config.tool_name,
            action=config.action,
            require_bound_context=True,
        )
        scoped_context_enforced = verify_scope_denial(
            secret_service,
            config,
            lease.lease_token,
        )
        result = SecretManagerVerificationResult(
            backend=secret.backend,
            reference_checked=bool(secret.id),
            lease_created=bool(lease.id),
            read_succeeded=value == config.secret_value,
            scoped_context_enforced=scoped_context_enforced,
            output_redacted=True,
            secret_value_exposed=False,
        )
        secret_value_exposed = config.secret_value in result.model_dump_json()
        return result.model_copy(
            update={
                "output_redacted": not secret_value_exposed,
                "secret_value_exposed": secret_value_exposed,
            }
        )
    finally:
        if secret is not None:
            cleanup_created_secret(secret_service, secret)


def verify_scope_denial(
    secret_service,
    config: SecretManagerVerificationConfig,
    lease_token: str,
) -> bool:
    try:
        secret_service.resolve_lease_value(
            tenant_id=config.tenant_id,
            workspace_id=f"{config.workspace_id}_denied",
            run_id=config.run_id,
            step_id=config.step_id,
            session_id=config.session_id,
            lease_token=lease_token,
            tool_name=config.tool_name,
            action=config.action,
            require_bound_context=True,
        )
    except SecretAccessDeniedError:
        return True
    return False


def cleanup_created_secret(secret_service, secret: SecretRef) -> None:
    if isinstance(secret_service, InMemorySecretService):
        secret_service.secrets.pop(secret.id, None)
        secret_service._secret_values.pop(secret.id, None)
        return
    if isinstance(secret_service, AwsSecretsManagerSecretService):
        if secret.external_name is None:
            return
        try:
            secret_service._client().delete_secret(
                SecretId=secret.external_name,
                ForceDeleteWithoutRecovery=True,
            )
        except Exception:
            return
        secret_service.secrets.pop(secret.id, None)


def build_secret_service(
    config: SecretManagerVerificationConfig,
):
    if config.backend == "aws_secrets_manager":
        return AwsSecretsManagerSecretService(
            config=AwsSecretsManagerConfig(
                region_name=config.region_name,
                endpoint_url=config.endpoint_url,
                secret_name_prefix=config.secret_name_prefix,
                kms_key_id=config.kms_key_id,
            )
        )
    return InMemorySecretService()


def secret_manager_verification_passed(
    result: SecretManagerVerificationResult,
) -> bool:
    return (
        result.reference_checked
        and result.lease_created
        and result.read_succeeded
        and result.scoped_context_enforced
        and result.output_redacted
        and not result.secret_value_exposed
    )


def main(argv: list[str] | None = None) -> int:
    config = parse_args(argv)
    result = verify_secret_manager(config)
    print(json.dumps(result.model_dump(mode="json"), sort_keys=True))
    return 0 if secret_manager_verification_passed(result) else 1


if __name__ == "__main__":
    raise SystemExit(main())
