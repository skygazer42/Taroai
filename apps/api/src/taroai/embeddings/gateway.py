import json
import threading
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from taroai.embeddings.models import (
    EmbeddingGatewayConfigurationError,
    EmbeddingGatewayRequest,
    EmbeddingGatewayResponse,
    EmbeddingGatewayResponseError,
    EmbeddingUsage,
    EmbeddingVector,
)
from taroai.provider_errors import redact_provider_error_detail


_HTTP_CLIENT: httpx.Client | None = None
_HTTP_CLIENT_LOCK = threading.Lock()


def _shared_http_client() -> httpx.Client:
    """Process-wide pooled HTTP client for embedding provider calls."""
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None:
        with _HTTP_CLIENT_LOCK:
            if _HTTP_CLIENT is None:
                _HTTP_CLIENT = httpx.Client(
                    timeout=httpx.Timeout(30.0),
                    follow_redirects=True,
                )
    return _HTTP_CLIENT


class EmbeddingGateway(BaseModel):
    def embed(self, request: EmbeddingGatewayRequest) -> EmbeddingGatewayResponse:
        raise NotImplementedError


class OpenAICompatibleEmbeddingGateway(EmbeddingGateway):
    base_url: str = "https://api.openai.com/v1"
    api_key: str = Field(default="", exclude=True, repr=False)
    api_key_secret_ref_id: str | None = Field(default=None, repr=False)
    secret_service: Any | None = Field(default=None, exclude=True, repr=False)
    secret_lease_ttl_seconds: int = Field(default=60, ge=1)
    default_model: str | None = None
    dimensions: int | None = Field(default=None, ge=1)
    timeout_seconds: int = Field(default=30, ge=1)

    model_config = ConfigDict(extra="forbid")

    def embed(self, request: EmbeddingGatewayRequest) -> EmbeddingGatewayResponse:
        model = request.model or self.default_model
        if not model:
            raise EmbeddingGatewayConfigurationError("embedding gateway model is not configured")

        api_key = self._resolve_api_key(request)
        payload = self._build_embedding_payload(request=request, model=model)
        response_body = self._post_embeddings(payload, api_key)
        return self._parse_embedding_response(response_body)

    def _build_embedding_payload(
        self,
        request: EmbeddingGatewayRequest,
        model: str,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "input": request.input,
            "encoding_format": "float",
        }
        dimensions = request.dimensions if request.dimensions is not None else self.dimensions
        if dimensions is not None:
            payload["dimensions"] = dimensions
        return payload

    def _resolve_api_key(self, request: EmbeddingGatewayRequest) -> str:
        if self.api_key_secret_ref_id is None:
            if not self.api_key:
                raise EmbeddingGatewayConfigurationError(
                    "embedding gateway api key is not configured"
                )
            return self.api_key
        if self.secret_service is None:
            raise EmbeddingGatewayConfigurationError(
                "embedding gateway secret service is required for api key secret ref"
            )
        step_id = f"embedding_gateway:{request.purpose}"
        try:
            lease = self.secret_service.create_lease(
                tenant_id=request.tenant_id,
                workspace_id=request.workspace_id,
                secret_id=self.api_key_secret_ref_id,
                tool_name="embedding_gateway",
                actions=["invoke"],
                ttl_seconds=self.secret_lease_ttl_seconds,
                run_id=request.run_id,
                step_id=step_id,
            )
            return self.secret_service.resolve_lease_value(
                tenant_id=request.tenant_id,
                workspace_id=request.workspace_id,
                run_id=request.run_id,
                step_id=step_id,
                lease_token=lease.lease_token,
                tool_name="embedding_gateway",
                action="invoke",
                require_bound_context=True,
            )
        except Exception as error:
            raise EmbeddingGatewayConfigurationError(
                "embedding gateway api key secret could not be resolved"
            ) from error

    def _post_embeddings(self, payload: dict[str, Any], api_key: str) -> dict[str, Any]:
        endpoint = f"{self.base_url.rstrip('/')}/embeddings"
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        try:
            response = _shared_http_client().post(
                endpoint,
                content=body,
                headers=headers,
                timeout=self.timeout_seconds,
            )
            if response.status_code >= 400:
                detail = response.text
                safe_detail = redact_provider_error_detail(detail, api_key=api_key)
                raise EmbeddingGatewayResponseError(
                    f"embedding gateway returned HTTP {response.status_code}: {safe_detail}"
                )
            return json.loads(response.content.decode("utf-8"))
        except httpx.HTTPError as error:
            raise EmbeddingGatewayResponseError(
                f"embedding gateway request failed: {error}"
            ) from error
        except json.JSONDecodeError as error:
            raise EmbeddingGatewayResponseError(
                "embedding gateway returned invalid JSON"
            ) from error

    def _parse_embedding_response(self, body: dict[str, Any]) -> EmbeddingGatewayResponse:
        data = body.get("data")
        if not isinstance(data, list) or not data:
            raise EmbeddingGatewayResponseError("embedding gateway response did not include data")
        embeddings = []
        for item in data:
            if not isinstance(item, dict):
                raise EmbeddingGatewayResponseError(
                    "embedding gateway response data item must be an object"
                )
            vector = item.get("embedding")
            if not isinstance(vector, list) or not vector:
                raise EmbeddingGatewayResponseError(
                    "embedding gateway response data item did not include embedding"
                )
            embeddings.append(
                EmbeddingVector(
                    index=int(item.get("index") or 0),
                    embedding=[float(value) for value in vector],
                )
            )
        return EmbeddingGatewayResponse(
            model=body.get("model"),
            embeddings=sorted(embeddings, key=lambda item: item.index),
            usage=self._parse_usage(body.get("usage")),
        )

    def _parse_usage(self, usage: Any) -> EmbeddingUsage | None:
        if not isinstance(usage, dict):
            return None
        prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or prompt_tokens)
        return EmbeddingUsage(
            prompt_tokens=prompt_tokens,
            total_tokens=total_tokens,
        )
