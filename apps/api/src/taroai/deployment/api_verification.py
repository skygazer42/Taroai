import argparse
import json
import os
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import ProxyHandler, Request, build_opener

from pydantic import BaseModel, ConfigDict, Field, model_validator

from taroai.deployment.install_evidence import (
    AuditWriteVerificationResult,
    EventStreamVerificationResult,
)
from taroai.support.redaction import redact_text_entry


class ApiVerificationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check: Literal["event_stream", "audit_write"]
    api_base_url: str = Field(
        default_factory=lambda: os.environ.get("TAROAI_API_BASE_URL", "http://localhost:8000"),
        min_length=1,
    )
    tenant_id: str = Field(default="tenant_verify", min_length=1)
    user_id: str = Field(default="user_verify", min_length=1)
    access_token: str = Field(default="", exclude=True, repr=False)
    denied_tenant_id: str | None = None
    denied_user_id: str | None = None
    denied_access_token: str = Field(default="", exclude=True, repr=False)
    workspace_id: str = Field(default="workspace_verify", min_length=1)
    existing_run_id: str | None = Field(default=None, min_length=1)
    run_message: str = Field(
        default="Private install validation API verification run.",
        min_length=1,
        exclude=True,
        repr=False,
    )
    run_mode: str = Field(default="workflow", min_length=1)
    timeout_seconds: int = Field(default=30, ge=1)

    @model_validator(mode="after")
    def validate_api_base_url(self) -> "ApiVerificationConfig":
        parsed = urlparse(self.api_base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("api_base_url must be an absolute HTTP URL")
        return self


class ApiVerificationHttpResponse(BaseModel):
    status_code: int = Field(ge=100)
    body: str = ""

    def json_value(self) -> Any:
        if not self.body:
            return None
        return json.loads(self.body)

    def json_body(self) -> dict[str, Any]:
        parsed = self.json_value()
        if not isinstance(parsed, dict):
            raise ValueError("expected a JSON object")
        return parsed


class ApiVerificationHttpClient:
    def __init__(self, timeout_seconds: int = 30):
        self.timeout_seconds = timeout_seconds
        self.opener = build_opener(ProxyHandler({}))

    def request(
        self,
        method: str,
        url: str,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> ApiVerificationHttpResponse:
        data = None
        request_headers = dict(headers or {})
        if payload is not None:
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request = Request(url, data=data, headers=request_headers, method=method)
        try:
            with self.opener.open(request, timeout=self.timeout_seconds) as response:
                return ApiVerificationHttpResponse(
                    status_code=response.status,
                    body=response.read().decode("utf-8", errors="replace"),
                )
        except HTTPError as error:
            return ApiVerificationHttpResponse(
                status_code=error.code,
                body=error.read().decode("utf-8", errors="replace"),
            )
        except URLError as error:
            raise RuntimeError(f"API verification request failed: {error}") from error


def parse_args(argv: list[str] | None = None) -> ApiVerificationConfig:
    parser = argparse.ArgumentParser(
        description="Verify authenticated API event-stream or audit-write behavior."
    )
    parser.add_argument("--check", choices=["event_stream", "audit_write"], required=True)
    parser.add_argument(
        "--api-base-url",
        default=os.environ.get("TAROAI_API_BASE_URL", "http://localhost:8000"),
    )
    parser.add_argument("--tenant-id", default=os.environ.get("TAROAI_VERIFY_TENANT_ID", "tenant_verify"))
    parser.add_argument("--user-id", default=os.environ.get("TAROAI_VERIFY_USER_ID", "user_verify"))
    parser.add_argument(
        "--access-token",
        default=os.environ.get("TAROAI_VERIFY_ACCESS_TOKEN", ""),
    )
    parser.add_argument(
        "--access-token-env-var",
        default="",
    )
    parser.add_argument(
        "--denied-tenant-id",
        default=os.environ.get("TAROAI_VERIFY_DENIED_TENANT_ID"),
    )
    parser.add_argument(
        "--denied-user-id",
        default=os.environ.get("TAROAI_VERIFY_DENIED_USER_ID"),
    )
    parser.add_argument(
        "--denied-access-token",
        default=os.environ.get("TAROAI_VERIFY_DENIED_ACCESS_TOKEN", ""),
    )
    parser.add_argument(
        "--denied-access-token-env-var",
        default="",
    )
    parser.add_argument(
        "--workspace-id",
        default=os.environ.get("TAROAI_VERIFY_WORKSPACE_ID", "workspace_verify"),
    )
    parser.add_argument(
        "--run-id",
        default=os.environ.get("TAROAI_VERIFY_RUN_ID"),
        help="Verify event/audit evidence against an existing run instead of creating one.",
    )
    parser.add_argument(
        "--run-message",
        default="Private install validation API verification run.",
    )
    parser.add_argument("--run-mode", default="workflow")
    parser.add_argument("--timeout-seconds", type=int, default=30)
    parsed = parser.parse_args(argv)
    access_token = parsed.access_token
    if parsed.access_token_env_var:
        access_token = os.environ.get(parsed.access_token_env_var, "")
    denied_access_token = parsed.denied_access_token
    if parsed.denied_access_token_env_var:
        denied_access_token = os.environ.get(parsed.denied_access_token_env_var, "")
    return ApiVerificationConfig(
        check=parsed.check,
        api_base_url=parsed.api_base_url,
        tenant_id=parsed.tenant_id,
        user_id=parsed.user_id,
        access_token=access_token,
        denied_tenant_id=parsed.denied_tenant_id,
        denied_user_id=parsed.denied_user_id,
        denied_access_token=denied_access_token,
        workspace_id=parsed.workspace_id,
        existing_run_id=parsed.run_id,
        run_message=parsed.run_message,
        run_mode=parsed.run_mode,
        timeout_seconds=parsed.timeout_seconds,
    )


def verify_event_stream(
    config: ApiVerificationConfig,
    client=None,
) -> EventStreamVerificationResult:
    http_client = client or build_api_verification_client(config)
    headers = authenticated_headers(config)
    run_id = verification_run_id(http_client, config, headers)
    response = http_client.request(
        "GET",
        api_url(config, f"/api/runs/{run_id}/events"),
        headers=headers,
    )
    stream_opened = response.status_code == 200
    events = parse_sse_events(response.body) if stream_opened else []
    first_sequence = first_event_sequence(events)
    event_id_received = first_sequence is not None
    after_sequence_replay_succeeded = False
    last_event_id_replay_succeeded = False
    if first_sequence is not None:
        replay_sequence = max(first_sequence - 1, 0)
        replay = http_client.request(
            "GET",
            api_url(
                config,
                f"/api/runs/{run_id}/events?{urlencode({'after_sequence': replay_sequence})}",
            ),
            headers=headers,
        )
        after_sequence_replay_succeeded = event_sequence_seen(
            replay.body,
            first_sequence,
        )
        last_event_headers = dict(headers)
        last_event_headers["Last-Event-ID"] = str(replay_sequence)
        last_event_replay = http_client.request(
            "GET",
            api_url(config, f"/api/runs/{run_id}/events"),
            headers=last_event_headers,
        )
        last_event_id_replay_succeeded = event_sequence_seen(
            last_event_replay.body,
            first_sequence,
        )
    tenant_scope_enforced = verify_denied_run_event_scope(
        http_client,
        config,
        run_id,
    )
    return EventStreamVerificationResult(
        api_base_url=config.api_base_url,
        run_id=run_id or None,
        first_event_sequence=first_sequence,
        stream_opened=stream_opened,
        event_id_received=event_id_received,
        after_sequence_replay_succeeded=after_sequence_replay_succeeded,
        last_event_id_replay_succeeded=last_event_id_replay_succeeded,
        tenant_scope_enforced=tenant_scope_enforced,
        safe_payload_confirmed=body_is_redacted(response.body, config),
    )


def verify_audit_write(
    config: ApiVerificationConfig,
    client=None,
) -> AuditWriteVerificationResult:
    http_client = client or build_api_verification_client(config)
    headers = authenticated_headers(config)
    run_id = verification_run_id(http_client, config, headers)
    response = http_client.request(
        "GET",
        api_url(
            config,
            f"/api/audit-events?{urlencode({'event_type': 'run.created', 'run_id': run_id})}",
        ),
        headers=headers,
    )
    read_back_succeeded = audit_event_seen(response, run_id)
    tenant_scope_enforced = verify_denied_audit_scope(
        http_client,
        config,
        run_id,
    )
    return AuditWriteVerificationResult(
        api_base_url=config.api_base_url,
        run_id=run_id or None,
        write_succeeded=bool(run_id),
        read_back_succeeded=read_back_succeeded,
        tenant_scope_enforced=tenant_scope_enforced,
        sensitive_metadata_redacted=body_is_redacted(response.body, config),
    )


def verification_run_id(
    client,
    config: ApiVerificationConfig,
    headers: dict[str, str],
) -> str:
    if config.existing_run_id:
        return config.existing_run_id
    return create_verification_run(client, config, headers)


def create_verification_run(
    client,
    config: ApiVerificationConfig,
    headers: dict[str, str],
) -> str:
    response = client.request(
        "POST",
        api_url(config, "/api/runs"),
        payload={
            "workspace_id": config.workspace_id,
            "message": config.run_message,
            "mode": config.run_mode,
        },
        headers=headers,
    )
    if response.status_code not in {200, 201}:
        return ""
    try:
        return str(response.json_body().get("run_id") or "")
    except Exception:
        return ""


def authenticated_headers(config: ApiVerificationConfig) -> dict[str, str]:
    if config.access_token:
        return {"Authorization": f"Bearer {config.access_token}"}
    return {
        "X-Tenant-ID": config.tenant_id,
        "X-User-ID": config.user_id,
    }


def denied_headers(config: ApiVerificationConfig) -> dict[str, str] | None:
    if config.denied_access_token:
        return {"Authorization": f"Bearer {config.denied_access_token}"}
    if config.denied_tenant_id and config.denied_user_id:
        return {
            "X-Tenant-ID": config.denied_tenant_id,
            "X-User-ID": config.denied_user_id,
        }
    return None


def verify_denied_run_event_scope(
    client,
    config: ApiVerificationConfig,
    run_id: str,
) -> bool:
    headers = denied_headers(config)
    if not headers or not run_id:
        return False
    response = client.request(
        "GET",
        api_url(config, f"/api/runs/{run_id}/events"),
        headers=headers,
    )
    if response.status_code in {401, 403, 404}:
        return True
    if response.status_code != 200:
        return False
    return not parse_sse_events(response.body)


def verify_denied_audit_scope(
    client,
    config: ApiVerificationConfig,
    run_id: str,
) -> bool:
    headers = denied_headers(config)
    if not headers or not run_id:
        return False
    response = client.request(
        "GET",
        api_url(
            config,
            f"/api/audit-events?{urlencode({'event_type': 'run.created', 'run_id': run_id})}",
        ),
        headers=headers,
    )
    if response.status_code in {401, 403, 404}:
        return True
    if response.status_code != 200:
        return False
    return not audit_event_seen(response, run_id)


def parse_sse_events(body: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        sequence: int | None = None
        event_type: str | None = None
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("id:"):
                try:
                    sequence = int(line.removeprefix("id:").strip())
                except ValueError:
                    sequence = None
            elif line.startswith("event:"):
                event_type = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data_lines.append(line.removeprefix("data:").strip())
        payload: Any = None
        if data_lines:
            try:
                payload = json.loads("\n".join(data_lines))
            except json.JSONDecodeError:
                payload = None
        events.append({"sequence": sequence, "event_type": event_type, "payload": payload})
    return events


def first_event_sequence(events: list[dict[str, Any]]) -> int | None:
    for event in events:
        sequence = event.get("sequence")
        if isinstance(sequence, int):
            return sequence
    return None


def event_sequence_seen(body: str, sequence: int) -> bool:
    return any(event.get("sequence") == sequence for event in parse_sse_events(body))


def audit_event_seen(response: ApiVerificationHttpResponse, run_id: str) -> bool:
    if response.status_code != 200:
        return False
    try:
        parsed = response.json_value()
    except Exception:
        return False
    if isinstance(parsed, dict) and isinstance(parsed.get("items"), list):
        records = parsed["items"]
    elif isinstance(parsed, list):
        records = parsed
    else:
        return False
    return any(
        isinstance(record, dict)
        and record.get("run_id") == run_id
        and record.get("event_type") == "run.created"
        for record in records
    )


def body_is_redacted(body: str, config: ApiVerificationConfig) -> bool:
    _, generic_findings = redact_text_entry("api-verification-response", body)
    if generic_findings:
        return False
    sensitive_values = [
        config.run_message,
        config.access_token,
        config.denied_access_token,
    ]
    return not any(value and value in body for value in sensitive_values)


def api_url(config: ApiVerificationConfig, path: str) -> str:
    return f"{config.api_base_url.rstrip('/')}/{path.lstrip('/')}"


def build_api_verification_client(config: ApiVerificationConfig) -> ApiVerificationHttpClient:
    return ApiVerificationHttpClient(timeout_seconds=config.timeout_seconds)


def event_stream_verification_passed(result: EventStreamVerificationResult) -> bool:
    return (
        result.stream_opened
        and result.event_id_received
        and result.after_sequence_replay_succeeded
        and result.last_event_id_replay_succeeded
        and result.tenant_scope_enforced
        and result.safe_payload_confirmed
    )


def audit_write_verification_passed(result: AuditWriteVerificationResult) -> bool:
    return (
        result.write_succeeded
        and result.read_back_succeeded
        and result.tenant_scope_enforced
        and result.sensitive_metadata_redacted
    )


def main(argv: list[str] | None = None) -> int:
    config = parse_args(argv)
    if config.check == "event_stream":
        result = verify_event_stream(config)
        print(json.dumps(result.model_dump(mode="json"), sort_keys=True))
        return 0 if event_stream_verification_passed(result) else 1
    result = verify_audit_write(config)
    print(json.dumps(result.model_dump(mode="json"), sort_keys=True))
    return 0 if audit_write_verification_passed(result) else 1


if __name__ == "__main__":
    raise SystemExit(main())
