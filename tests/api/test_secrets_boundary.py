from datetime import timedelta
from pathlib import Path

import pytest

from taroai.domain import utc_now
from taroai.secrets import (
    AwsSecretsManagerConfig,
    AwsSecretsManagerSecretService,
    InMemorySecretService,
    SecretAccessDeniedError,
    SecretLeaseExpiredError,
    SecretScope,
)
from taroai.secrets.verification import (
    SecretManagerVerificationConfig,
    main,
    parse_args,
    verify_secret_manager,
)


class RecordingSecretsManagerClient:
    def __init__(self):
        self.values = {}
        self.created = []
        self.put_values = []
        self.reads = []
        self.deleted = []

    def create_secret(self, **kwargs):
        self.created.append(kwargs)
        self.values[kwargs["Name"]] = kwargs["SecretString"]
        return {"ARN": f"arn:aws:secretsmanager:us-west-2:123456789012:secret:{kwargs['Name']}"}

    def get_secret_value(self, **kwargs):
        self.reads.append(kwargs)
        return {"SecretString": self.values[kwargs["SecretId"]]}

    def put_secret_value(self, **kwargs):
        self.put_values.append(kwargs)
        self.values[kwargs["SecretId"]] = kwargs["SecretString"]
        return {"ARN": f"arn:aws:secretsmanager:us-west-2:123456789012:secret:{kwargs['SecretId']}"}

    def delete_secret(self, **kwargs):
        self.deleted.append(kwargs)
        self.values.pop(kwargs["SecretId"], None)
        return {"Name": kwargs["SecretId"]}


def test_secret_service_issues_scoped_short_lived_leases_without_exposing_secret_values():
    service = InMemorySecretService()
    created_at = utc_now()
    secret = service.create_secret(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        name="salesforce-api-key",
        value="super-secret-api-key",
        scope=SecretScope(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            allowed_tool_names=["crm.lookup"],
            actions=["read"],
        ),
        created_at=created_at,
    )
    lease = service.create_lease(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        secret_id=secret.id,
        tool_name="crm.lookup",
        actions=["read"],
        ttl_seconds=60,
        now=created_at,
    )

    assert secret.name == "salesforce-api-key"
    assert "super-secret-api-key" not in str(secret.model_dump(mode="json"))
    assert "super-secret-api-key" not in str(lease.model_dump(mode="json"))
    assert service.resolve_lease_value(
        tenant_id="tenant_acme",
        lease_token=lease.lease_token,
        now=created_at + timedelta(seconds=30),
    ) == "super-secret-api-key"

    audit_metadata = lease.to_audit_metadata()
    assert audit_metadata["secret_ref_id"] == secret.id
    assert audit_metadata["tool_name"] == "crm.lookup"
    assert "super-secret-api-key" not in str(audit_metadata)
    assert lease.lease_token not in str(audit_metadata)

    with pytest.raises(SecretLeaseExpiredError):
        service.resolve_lease_value(
            tenant_id="tenant_acme",
            lease_token=lease.lease_token,
            now=created_at + timedelta(seconds=61),
        )


def test_secret_manager_verification_checks_scoped_read_without_exposing_value():
    service = InMemorySecretService()
    config = SecretManagerVerificationConfig(
        backend="memory",
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        run_id="run_verify",
        step_id="step_verify",
        session_id="sandbox_verify",
        tool_name="crm.lookup",
        action="read",
        secret_value="customer-secret-value",
    )

    result = verify_secret_manager(config, service=service)

    assert result.backend == "memory"
    assert result.reference_checked is True
    assert result.lease_created is True
    assert result.read_succeeded is True
    assert result.scoped_context_enforced is True
    assert result.output_redacted is True
    assert result.secret_value_exposed is False
    assert "customer-secret-value" not in result.model_dump_json()


def test_secret_manager_verification_uses_aws_backend_and_cleans_created_secret():
    client = RecordingSecretsManagerClient()
    service = AwsSecretsManagerSecretService(
        config=AwsSecretsManagerConfig(
            region_name="us-west-2",
            secret_name_prefix="taroai/verify",
        ),
        client=client,
    )
    config = SecretManagerVerificationConfig(
        backend="aws_secrets_manager",
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        run_id="run_verify",
        step_id="step_verify",
        session_id="sandbox_verify",
        tool_name="crm.lookup",
        action="read",
        secret_value="external-secret-value",
    )

    result = verify_secret_manager(config, service=service)

    assert result.backend == "aws_secrets_manager"
    assert result.read_succeeded is True
    assert client.created
    assert client.reads
    assert client.deleted
    assert client.deleted[0]["ForceDeleteWithoutRecovery"] is True
    assert "external-secret-value" not in result.model_dump_json()


def test_secret_manager_verification_cli_parses_backend_settings_without_dumping_value():
    config = parse_args(
        [
            "--backend",
            "aws_secrets_manager",
            "--region-name",
            "us-west-2",
            "--endpoint-url",
            "http://localhost:4566",
            "--secret-name-prefix",
            "taroai/verify",
            "--kms-key-id",
            "alias/taroai-secrets",
            "--tenant-id",
            "tenant_acme",
            "--workspace-id",
            "workspace_sales",
            "--secret-value",
            "customer-secret-value",
        ]
    )

    assert config.backend == "aws_secrets_manager"
    assert config.region_name == "us-west-2"
    assert config.endpoint_url == "http://localhost:4566"
    assert config.secret_name_prefix == "taroai/verify"
    assert config.kms_key_id == "alias/taroai-secrets"
    assert "customer-secret-value" not in config.model_dump_json()
    assert "customer-secret-value" not in repr(config)


def test_secret_manager_verification_main_prints_redacted_json(capsys, monkeypatch):
    service = InMemorySecretService()

    def build_service(_config: SecretManagerVerificationConfig):
        return service

    monkeypatch.setattr(
        "taroai.secrets.verification.build_secret_service",
        build_service,
    )

    exit_code = main(
        [
            "--backend",
            "memory",
            "--tenant-id",
            "tenant_acme",
            "--workspace-id",
            "workspace_sales",
            "--secret-value",
            "customer-secret-value",
        ]
    )

    output = capsys.readouterr().out

    assert exit_code == 0
    assert "customer-secret-value" not in output
    assert '"read_succeeded": true' in output


def test_verify_secret_manager_script_wraps_python_cli():
    script = Path("scripts/verify-secret-manager.sh")

    text = script.read_text()

    assert "python -m taroai.secrets.verification" in text
    assert "--backend" in text
    assert "--secret-value" in text
    assert "TAROAI_SECRET_SERVICE_BACKEND" in text


def test_secret_service_denies_cross_scope_leases_before_exposing_values():
    service = InMemorySecretService()
    secret = service.create_secret(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        name="salesforce-api-key",
        value="super-secret-api-key",
        scope=SecretScope(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            allowed_tool_names=["crm.lookup"],
            actions=["read"],
        ),
    )

    with pytest.raises(SecretAccessDeniedError):
        service.create_lease(
            tenant_id="tenant_acme",
            workspace_id="workspace_support",
            secret_id=secret.id,
            tool_name="crm.lookup",
            actions=["read"],
            ttl_seconds=60,
        )

    with pytest.raises(SecretAccessDeniedError):
        service.create_lease(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            secret_id=secret.id,
            tool_name="crm.update",
            actions=["write"],
            ttl_seconds=60,
        )

    assert "super-secret-api-key" not in str(service.model_dump(mode="json"))


def test_aws_secret_service_stores_values_in_external_backend_without_model_leakage():
    client = RecordingSecretsManagerClient()
    created_at = utc_now()
    service = AwsSecretsManagerSecretService(
        config=AwsSecretsManagerConfig(
            region_name="us-west-2",
            secret_name_prefix="taroai/poc",
            kms_key_id="alias/taroai-secrets",
        ),
        client=client,
    )
    secret = service.create_secret(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        name="salesforce-api-key",
        value="external-secret-value",
        scope=SecretScope(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            allowed_tool_names=["crm.lookup"],
            actions=["read"],
        ),
        created_at=created_at,
    )

    assert secret.backend == "aws_secrets_manager"
    assert secret.external_name.startswith("taroai/poc/tenant_acme/workspace_sales/secret_")
    assert client.created[0]["Name"] == secret.external_name
    assert client.created[0]["SecretString"] == "external-secret-value"
    assert client.created[0]["KmsKeyId"] == "alias/taroai-secrets"
    assert "external-secret-value" not in str(service.model_dump(mode="json"))
    assert "external-secret-value" not in str(secret.model_dump(mode="json"))

    lease = service.create_lease(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        secret_id=secret.id,
        tool_name="crm.lookup",
        actions=["read"],
        ttl_seconds=60,
        now=created_at,
    )

    assert service.resolve_lease_value(
        tenant_id="tenant_acme",
        lease_token=lease.lease_token,
        now=created_at + timedelta(seconds=10),
    ) == "external-secret-value"
    assert client.reads[0]["SecretId"] == secret.external_name

    service.rotate_secret_value(
        tenant_id="tenant_acme",
        secret_id=secret.id,
        value="rotated-external-secret-value",
    )

    assert client.put_values[0]["SecretId"] == secret.external_name
    assert client.put_values[0]["SecretString"] == "rotated-external-secret-value"
    assert service.resolve_lease_value(
        tenant_id="tenant_acme",
        lease_token=lease.lease_token,
        now=created_at + timedelta(seconds=20),
    ) == "rotated-external-secret-value"
    assert "rotated-external-secret-value" not in str(service.model_dump(mode="json"))
