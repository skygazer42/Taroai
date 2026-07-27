import hashlib
import json
import threading
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from taroai.observability.models import RunTrace, TraceExportResult, TraceSpan


_HTTP_CLIENT: httpx.Client | None = None
_HTTP_CLIENT_LOCK = threading.Lock()


def _shared_http_client() -> httpx.Client:
    """Process-wide pooled HTTP client for trace export calls."""
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None:
        with _HTTP_CLIENT_LOCK:
            if _HTTP_CLIENT is None:
                _HTTP_CLIENT = httpx.Client(
                    timeout=httpx.Timeout(5.0),
                    follow_redirects=True,
                )
    return _HTTP_CLIENT


SPAN_KIND_MAP = {
    "internal": "SPAN_KIND_INTERNAL",
    "server": "SPAN_KIND_SERVER",
    "client": "SPAN_KIND_CLIENT",
    "producer": "SPAN_KIND_PRODUCER",
    "consumer": "SPAN_KIND_CONSUMER",
}


class TraceExportHttpClient(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def post_json(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        timeout_seconds: int,
    ) -> None:
        response = _shared_http_client().post(
            url,
            content=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={
                **headers,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=timeout_seconds,
        )
        # urllib raised HTTPError on non-2xx; raise_for_status preserves the
        # "HTTP error => export failed" behavior (caught by the exporter).
        response.raise_for_status()


class OtlpHttpTraceExporter(BaseModel):
    endpoint_url: str = Field(min_length=1)
    api_key: str = ""
    timeout_seconds: int = Field(default=5, ge=1)
    service_name: str = Field(default="taroai-api", min_length=1)
    deployment_environment: str = "local"
    client: Any = Field(default_factory=TraceExportHttpClient)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def export(self, trace: RunTrace) -> TraceExportResult:
        payload = self.to_payload(trace)
        try:
            self.client.post_json(
                url=self.endpoint_url,
                payload=payload,
                headers=self._headers(),
                timeout_seconds=self.timeout_seconds,
            )
        except Exception as error:
            return TraceExportResult(
                status="failed",
                trace_id=trace.run.id,
                span_count=len(trace.spans),
                destination=self.endpoint_url,
                error_type=error.__class__.__name__,
                message="Trace export failed",
            )
        return TraceExportResult(
            status="exported",
            trace_id=trace.run.id,
            span_count=len(trace.spans),
            destination=self.endpoint_url,
        )

    def to_payload(self, trace: RunTrace) -> dict[str, Any]:
        return {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            self._attribute("service.name", self.service_name),
                            self._attribute(
                                "deployment.environment",
                                self.deployment_environment,
                            ),
                        ]
                    },
                    "scopeSpans": [
                        {
                            "scope": {"name": "taroai.observability"},
                            "spans": [self._span_payload(span) for span in trace.spans],
                        }
                    ],
                }
            ]
        }

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {"Authorization": f"Bearer {self.api_key}"}

    def _span_payload(self, span: TraceSpan) -> dict[str, Any]:
        payload = {
            "traceId": self._trace_id(span.trace_id),
            "spanId": self._span_id(span.span_id),
            "name": span.name,
            "kind": SPAN_KIND_MAP[span.kind],
            "startTimeUnixNano": str(self._unix_nano(span.started_at)),
            "endTimeUnixNano": str(self._unix_nano(span.ended_at)),
            "attributes": [
                self._attribute(key, value)
                for key, value in span.attributes.items()
            ],
            "status": {
                "code": (
                    "STATUS_CODE_ERROR"
                    if span.status == "error"
                    else "STATUS_CODE_OK"
                )
            },
        }
        if span.parent_span_id is not None:
            payload["parentSpanId"] = self._span_id(span.parent_span_id)
        return payload

    def _attribute(self, key: str, value: Any) -> dict[str, Any]:
        return {"key": key, "value": self._attribute_value(value)}

    def _attribute_value(self, value: Any) -> dict[str, Any]:
        if isinstance(value, Enum):
            return {"stringValue": value.value}
        if isinstance(value, bool):
            return {"boolValue": value}
        if isinstance(value, int):
            return {"intValue": str(value)}
        if isinstance(value, float):
            return {"doubleValue": value}
        if isinstance(value, list):
            return {
                "arrayValue": {
                    "values": [self._attribute_value(item) for item in value]
                }
            }
        if isinstance(value, dict):
            return {
                "kvlistValue": {
                    "values": [
                        self._attribute(str(key), nested)
                        for key, nested in value.items()
                    ]
                }
            }
        if value is None:
            return {"stringValue": ""}
        return {"stringValue": str(value)}

    def _trace_id(self, value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]

    def _span_id(self, value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]

    def _unix_nano(self, value: datetime) -> int:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return int(value.timestamp() * 1_000_000_000)
