import json
from pathlib import Path

import pytest

from taroai.observability.verification import (
    TraceCollectorVerificationConfig,
    parse_args,
    verify_trace_collector,
)


class RecordingTraceCollectorClient:
    def __init__(self):
        self.calls: list[dict] = []

    def post_json(
        self,
        url: str,
        payload: dict,
        headers: dict[str, str],
        timeout_seconds: int,
    ) -> None:
        self.calls.append(
            {
                "url": url,
                "payload": payload,
                "headers": headers,
                "timeout_seconds": timeout_seconds,
            }
        )


def test_trace_collector_verification_config_requires_http_endpoint():
    with pytest.raises(ValueError, match="trace collector verification requires an HTTP endpoint URL"):
        TraceCollectorVerificationConfig(endpoint_url="grpc://collector:4317")


def test_trace_collector_verification_cli_parses_endpoint_and_metadata():
    config = parse_args(
        [
            "--endpoint-url",
            "http://collector:4318/v1/traces",
            "--api-key",
            "collector-secret",
            "--timeout-seconds",
            "7",
            "--service-name",
            "taroai-worker",
            "--deployment-environment",
            "private",
        ]
    )

    assert config.endpoint_url == "http://collector:4318/v1/traces"
    assert config.api_key == "collector-secret"
    assert config.timeout_seconds == 7
    assert config.service_name == "taroai-worker"
    assert config.deployment_environment == "private"


def test_verify_trace_collector_script_wraps_python_cli():
    script = Path("scripts/verify-trace-collector.sh")

    text = script.read_text()

    assert "python -m taroai.observability.verification" in text
    assert "--endpoint-url" in text
    assert "--api-key" in text
    assert "TAROAI_TRACE_EXPORTER_ENDPOINT_URL" in text


def test_trace_collector_verification_exports_safe_otlp_payload_without_secret_leak():
    client = RecordingTraceCollectorClient()
    config = TraceCollectorVerificationConfig(
        endpoint_url="http://collector:4318/v1/traces",
        api_key="collector-secret",
        timeout_seconds=7,
        service_name="taroai-worker",
        deployment_environment="private",
    )

    result = verify_trace_collector(config, client=client)

    assert result.status == "exported"
    assert result.endpoint_url == "http://collector:4318/v1/traces"
    assert result.span_count == 1
    assert result.resource_span_count == 1
    assert result.scope_span_count == 1
    assert result.authorization_header_sent is True
    assert result.secret_value_exposed is False
    assert "collector-secret" not in json.dumps(result.model_dump(mode="json"))

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["url"] == "http://collector:4318/v1/traces"
    assert call["headers"]["Authorization"] == "Bearer collector-secret"
    assert call["timeout_seconds"] == 7
    payload_text = json.dumps(call["payload"])
    assert "taroai-worker" in payload_text
    assert "private" in payload_text
    assert "trace.collector.verify" in payload_text
    assert "collector-secret" not in payload_text
