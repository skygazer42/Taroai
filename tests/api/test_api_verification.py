import json
from pathlib import Path

from taroai.deployment.api_verification import (
    ApiVerificationConfig,
    ApiVerificationHttpResponse,
    main,
    parse_args,
    verify_audit_write,
    verify_event_stream,
)
from taroai.deployment.install_evidence import (
    AuditWriteVerificationResult,
    EventStreamVerificationResult,
)


class RecordingApiVerificationClient:
    def __init__(self):
        self.calls = []

    def request(self, method, url, payload=None, headers=None):
        self.calls.append((method, url, payload, dict(headers or {})))
        tenant_id = (headers or {}).get("X-Tenant-ID")
        if (headers or {}).get("Authorization") == "Bearer denied-token":
            tenant_id = "tenant_denied"
        if tenant_id == "tenant_denied":
            return ApiVerificationHttpResponse(status_code=404, body='{"code":"not_found"}')
        if method == "POST" and url.endswith("/api/runs"):
            return ApiVerificationHttpResponse(
                status_code=201,
                body='{"run_id":"run_verify","status":"created"}',
            )
        if method == "GET" and "/api/runs/run_verify/events" in url:
            return ApiVerificationHttpResponse(
                status_code=200,
                body=(
                    "id: 1\n"
                    "event: run.created\n"
                    'data: {"id":"event_1","sequence":1,"type":"run.created",'
                    '"payload":{"status":"created","mode":"workflow"}}\n\n'
                ),
            )
        if method == "GET" and "/api/audit-events" in url:
            return ApiVerificationHttpResponse(
                status_code=200,
                body=json.dumps(
                    [
                        {
                            "id": "audit_1",
                            "tenant_id": "tenant_acme",
                            "workspace_id": "workspace_sales",
                            "user_id": "user_verify",
                            "run_id": "run_verify",
                            "event_type": "run.created",
                            "metadata": {"mode": "workflow", "agent_id": None},
                        }
                    ]
                ),
            )
        raise AssertionError(f"unexpected request: {method} {url}")


class SensitiveEventStreamApiVerificationClient(RecordingApiVerificationClient):
    def request(self, method, url, payload=None, headers=None):
        if method == "GET" and "/api/runs/run_verify/events" in url:
            return ApiVerificationHttpResponse(
                status_code=200,
                body=(
                    "id: 1\n"
                    "event: run.created\n"
                    'data: {"id":"event_1","sequence":1,"type":"run.created",'
                    '"payload":{"status":"created",'
                    '"authorization":"Bearer leaked-session-token-1234567890",'
                    '"callback":"https://agent:secret-value@api.customer.local/v1"}}\n\n'
                ),
            )
        return super().request(method, url, payload, headers)


class SensitiveAuditApiVerificationClient(RecordingApiVerificationClient):
    def request(self, method, url, payload=None, headers=None):
        if method == "GET" and "/api/audit-events" in url:
            return ApiVerificationHttpResponse(
                status_code=200,
                body=json.dumps(
                    [
                        {
                            "id": "audit_1",
                            "tenant_id": "tenant_acme",
                            "workspace_id": "workspace_sales",
                            "user_id": "user_verify",
                            "run_id": "run_verify",
                            "event_type": "run.created",
                            "metadata": {
                                "mode": "workflow",
                                "callback": (
                                    "https://agent:secret-value@api.customer.local/v1"
                                ),
                            },
                        }
                    ]
                ),
            )
        return super().request(method, url, payload, headers)


def test_event_stream_verifier_creates_run_and_checks_replay_without_payload_leak():
    client = RecordingApiVerificationClient()
    config = ApiVerificationConfig(
        check="event_stream",
        api_base_url="http://api.local",
        tenant_id="tenant_acme",
        user_id="user_verify",
        denied_tenant_id="tenant_denied",
        denied_user_id="user_denied",
        workspace_id="workspace_sales",
        run_message="customer-secret-message",
    )

    result = verify_event_stream(config, client=client)

    assert result == EventStreamVerificationResult(
        api_base_url="http://api.local",
        run_id="run_verify",
        first_event_sequence=1,
        stream_opened=True,
        event_id_received=True,
        after_sequence_replay_succeeded=True,
        last_event_id_replay_succeeded=True,
        tenant_scope_enforced=True,
        safe_payload_confirmed=True,
    )
    assert any("?after_sequence=0" in call[1] for call in client.calls)
    assert any(call[3].get("Last-Event-ID") == "0" for call in client.calls)
    assert "customer-secret-message" not in result.model_dump_json()


def test_event_stream_verifier_can_check_existing_run_without_creating_run():
    client = RecordingApiVerificationClient()
    config = ApiVerificationConfig(
        check="event_stream",
        api_base_url="http://api.local",
        tenant_id="tenant_acme",
        user_id="user_verify",
        denied_tenant_id="tenant_denied",
        denied_user_id="user_denied",
        workspace_id="workspace_sales",
        existing_run_id="run_verify",
    )

    result = verify_event_stream(config, client=client)

    assert result.run_id == "run_verify"
    assert result.stream_opened is True
    assert not any(call[0] == "POST" and call[1].endswith("/api/runs") for call in client.calls)


def test_event_stream_verifier_detects_generic_sensitive_payload():
    client = SensitiveEventStreamApiVerificationClient()
    config = ApiVerificationConfig(
        check="event_stream",
        api_base_url="http://api.local",
        tenant_id="tenant_acme",
        user_id="user_verify",
        denied_tenant_id="tenant_denied",
        denied_user_id="user_denied",
        workspace_id="workspace_sales",
    )

    result = verify_event_stream(config, client=client)

    assert result.safe_payload_confirmed is False


def test_audit_write_verifier_checks_readback_scope_and_redaction():
    client = RecordingApiVerificationClient()
    config = ApiVerificationConfig(
        check="audit_write",
        api_base_url="http://api.local",
        tenant_id="tenant_acme",
        user_id="user_verify",
        denied_access_token="denied-token",
        workspace_id="workspace_sales",
        run_message="customer-secret-message",
    )

    result = verify_audit_write(config, client=client)

    assert result == AuditWriteVerificationResult(
        api_base_url="http://api.local",
        run_id="run_verify",
        write_succeeded=True,
        read_back_succeeded=True,
        tenant_scope_enforced=True,
        sensitive_metadata_redacted=True,
    )
    assert any("/api/audit-events" in call[1] for call in client.calls)
    assert "customer-secret-message" not in result.model_dump_json()


def test_audit_write_verifier_can_check_existing_run_without_creating_run():
    client = RecordingApiVerificationClient()
    config = ApiVerificationConfig(
        check="audit_write",
        api_base_url="http://api.local",
        tenant_id="tenant_acme",
        user_id="user_verify",
        denied_access_token="denied-token",
        workspace_id="workspace_sales",
        existing_run_id="run_verify",
    )

    result = verify_audit_write(config, client=client)

    assert result.run_id == "run_verify"
    assert result.write_succeeded is True
    assert result.read_back_succeeded is True
    assert not any(call[0] == "POST" and call[1].endswith("/api/runs") for call in client.calls)


def test_audit_write_verifier_detects_generic_sensitive_metadata():
    client = SensitiveAuditApiVerificationClient()
    config = ApiVerificationConfig(
        check="audit_write",
        api_base_url="http://api.local",
        tenant_id="tenant_acme",
        user_id="user_verify",
        denied_access_token="denied-token",
        workspace_id="workspace_sales",
    )

    result = verify_audit_write(config, client=client)

    assert result.sensitive_metadata_redacted is False


def test_api_verification_cli_parses_auth_without_dumping_tokens():
    config = parse_args(
        [
            "--check",
            "event_stream",
            "--api-base-url",
            "https://api.example.com",
            "--tenant-id",
            "tenant_acme",
            "--user-id",
            "user_verify",
            "--access-token",
            "primary-token",
            "--denied-access-token",
            "denied-token",
            "--workspace-id",
            "workspace_sales",
        ]
    )

    assert config.check == "event_stream"
    assert config.api_base_url == "https://api.example.com"
    assert config.access_token == "primary-token"
    assert config.denied_access_token == "denied-token"
    assert "primary-token" not in config.model_dump_json()
    assert "denied-token" not in config.model_dump_json()
    assert "primary-token" not in repr(config)
    assert "denied-token" not in repr(config)


def test_api_verification_main_prints_redacted_event_stream_json(capsys, monkeypatch):
    client = RecordingApiVerificationClient()

    def build_client(_config: ApiVerificationConfig):
        return client

    monkeypatch.setattr(
        "taroai.deployment.api_verification.build_api_verification_client",
        build_client,
    )

    exit_code = main(
        [
            "--check",
            "event_stream",
            "--api-base-url",
            "http://api.local",
            "--tenant-id",
            "tenant_acme",
            "--user-id",
            "user_verify",
            "--denied-tenant-id",
            "tenant_denied",
            "--denied-user-id",
            "user_denied",
            "--run-message",
            "customer-secret-message",
        ]
    )

    output = capsys.readouterr().out

    assert exit_code == 0
    assert "customer-secret-message" not in output
    assert '"stream_opened": true' in output
    assert '"run_id": "run_verify"' in output


def test_verify_event_stream_and_audit_write_scripts_wrap_python_cli():
    event_script = Path("scripts/verify-event-stream.sh").read_text()
    audit_script = Path("scripts/verify-audit-write.sh").read_text()

    assert "python -m taroai.deployment.api_verification" in event_script
    assert "--check event_stream" in event_script
    assert "--api-base-url" in event_script
    assert "python -m taroai.deployment.api_verification" in audit_script
    assert "--check audit_write" in audit_script
    assert "--api-base-url" in audit_script
