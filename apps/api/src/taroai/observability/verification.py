import argparse
import json
import os
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator

from taroai.domain import Run, RunMode, RunStatus, utc_now
from taroai.observability.exporter import OtlpHttpTraceExporter, TraceExportHttpClient
from taroai.observability.models import RunTrace, TraceSpan


class TraceCollectorVerificationConfig(BaseModel):
    endpoint_url: str = Field(min_length=1)
    api_key: str = Field(default="", repr=False)
    timeout_seconds: int = Field(default=5, ge=1)
    service_name: str = Field(default="taroai-api", min_length=1)
    deployment_environment: str = Field(default="local", min_length=1)

    @model_validator(mode="after")
    def validate_endpoint_url(self) -> "TraceCollectorVerificationConfig":
        scheme = urlparse(self.endpoint_url).scheme
        if scheme not in {"http", "https"}:
            raise ValueError("trace collector verification requires an HTTP endpoint URL")
        return self


class TraceCollectorVerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["exported"]
    endpoint_url: str
    trace_id: str
    span_count: int
    resource_span_count: int
    scope_span_count: int
    authorization_header_sent: bool
    secret_value_exposed: bool = False


def parse_args(argv: list[str] | None = None) -> TraceCollectorVerificationConfig:
    parser = argparse.ArgumentParser(
        description="Verify OTLP HTTP trace collector export behavior."
    )
    parser.add_argument(
        "--endpoint-url",
        default=os.environ.get("TAROAI_TRACE_EXPORTER_ENDPOINT_URL", ""),
        required=not bool(os.environ.get("TAROAI_TRACE_EXPORTER_ENDPOINT_URL")),
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("TAROAI_TRACE_EXPORTER_API_KEY", ""),
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=int(os.environ.get("TAROAI_TRACE_EXPORTER_TIMEOUT_SECONDS", "5")),
    )
    parser.add_argument(
        "--service-name",
        default=os.environ.get("TAROAI_TRACE_EXPORTER_SERVICE_NAME", "taroai-api"),
    )
    parser.add_argument(
        "--deployment-environment",
        default=os.environ.get("TAROAI_ENVIRONMENT", "local"),
    )
    parsed = parser.parse_args(argv)
    return TraceCollectorVerificationConfig(
        endpoint_url=parsed.endpoint_url,
        api_key=parsed.api_key,
        timeout_seconds=parsed.timeout_seconds,
        service_name=parsed.service_name,
        deployment_environment=parsed.deployment_environment,
    )


def verify_trace_collector(
    config: TraceCollectorVerificationConfig,
    client: Any | None = None,
) -> TraceCollectorVerificationResult:
    resolved_client = client or TraceExportHttpClient()
    exporter = OtlpHttpTraceExporter(
        endpoint_url=config.endpoint_url,
        api_key=config.api_key,
        timeout_seconds=config.timeout_seconds,
        service_name=config.service_name,
        deployment_environment=config.deployment_environment,
        client=resolved_client,
    )
    trace = build_verification_trace()
    payload = exporter.to_payload(trace)
    export_result = exporter.export(trace)
    if export_result.status != "exported":
        message = export_result.message or "Trace collector verification failed"
        raise RuntimeError(message)
    resource_spans = payload.get("resourceSpans", [])
    scope_span_count = sum(
        len(resource_span.get("scopeSpans", [])) for resource_span in resource_spans
    )
    return TraceCollectorVerificationResult(
        status="exported",
        endpoint_url=config.endpoint_url,
        trace_id=trace.run.id,
        span_count=len(trace.spans),
        resource_span_count=len(resource_spans),
        scope_span_count=scope_span_count,
        authorization_header_sent=bool(config.api_key),
        secret_value_exposed=secret_value_exposed(config.api_key, payload),
    )


def build_verification_trace() -> RunTrace:
    now = utc_now()
    run = Run(
        id="trace_collector_verify",
        tenant_id="tenant_trace_verify",
        workspace_id="workspace_trace_verify",
        user_id="user_trace_verify",
        agent_id="agent_trace_verify",
        message="Verify trace collector export",
        attachments=[],
        mode=RunMode.CHAT,
        status=RunStatus.SUCCEEDED,
        created_at=now,
        updated_at=now,
    )
    span = TraceSpan(
        trace_id=run.id,
        span_id="span_trace_collector_verify",
        name="trace.collector.verify",
        kind="client",
        status="ok",
        started_at=now,
        ended_at=now,
        attributes={
            "component": "deployment_verification",
            "verification.kind": "trace_collector",
        },
    )
    return RunTrace(
        run=run,
        events=[],
        spans=[span],
        trace_events=[],
        guardrail_findings=[],
        billing_meters=[],
        audit_events=[],
    )


def secret_value_exposed(api_key: str, payload: dict[str, Any]) -> bool:
    if not api_key:
        return False
    return api_key in json.dumps(payload, separators=(",", ":"), sort_keys=True)


def main(argv: list[str] | None = None) -> int:
    config = parse_args(argv)
    result = verify_trace_collector(config)
    print(json.dumps(result.model_dump(mode="json"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
