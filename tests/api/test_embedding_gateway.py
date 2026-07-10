import json
from io import BytesIO
from urllib.error import HTTPError

import pytest
from pydantic import Field

from taroai.embeddings import (
    EmbeddingGatewayConfigurationError,
    EmbeddingGatewayRequest,
    EmbeddingGatewayResponseError,
    OpenAICompatibleEmbeddingGateway,
)
from taroai.secrets import InMemorySecretService, SecretScope


class RecordingOpenAICompatibleEmbeddingGateway(OpenAICompatibleEmbeddingGateway):
    authorization_headers: list[str] = Field(default_factory=list, exclude=True, repr=False)
    payloads: list[dict] = Field(default_factory=list, exclude=True, repr=False)

    def _post_embeddings(self, payload: dict, api_key: str) -> dict:
        self.payloads.append(payload)
        self.authorization_headers.append(f"Bearer {api_key}")
        return {
            "object": "list",
            "model": payload["model"],
            "data": [
                {"object": "embedding", "index": 0, "embedding": [0.1, 0.9]},
                {"object": "embedding", "index": 1, "embedding": [0.8, 0.2]},
            ],
            "usage": {"prompt_tokens": 8, "total_tokens": 8},
        }


def test_openai_compatible_embedding_gateway_uses_secret_ref_without_config_leakage():
    secret_service = InMemorySecretService()
    secret = secret_service.create_secret(
        tenant_id="tenant_acme",
        workspace_id=None,
        name="embedding-gateway-api-key",
        value="sk-enterprise-embedding-key",
        scope=SecretScope(
            tenant_id="tenant_acme",
            allowed_tool_names=["embedding_gateway"],
            actions=["invoke"],
        ),
    )
    gateway = RecordingOpenAICompatibleEmbeddingGateway(
        api_key_secret_ref_id=secret.id,
        secret_service=secret_service,
        default_model="text-embedding-3-small",
        dimensions=512,
    )

    response = gateway.embed(
        EmbeddingGatewayRequest(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            user_id="user_1",
            run_id="run_1",
            purpose="knowledge_index",
            input=["Procurement renewal guidance.", "General account overview."],
        )
    )

    leases = list(secret_service.leases.values())
    assert response.model == "text-embedding-3-small"
    assert response.embeddings[0].embedding == [0.1, 0.9]
    assert response.usage.total_tokens == 8
    assert gateway.authorization_headers == ["Bearer sk-enterprise-embedding-key"]
    assert gateway.payloads == [
        {
            "model": "text-embedding-3-small",
            "input": ["Procurement renewal guidance.", "General account overview."],
            "encoding_format": "float",
            "dimensions": 512,
        }
    ]
    assert len(leases) == 1
    assert leases[0].tenant_id == "tenant_acme"
    assert leases[0].workspace_id == "workspace_sales"
    assert leases[0].run_id == "run_1"
    assert leases[0].step_id == "embedding_gateway:knowledge_index"
    assert leases[0].tool_name == "embedding_gateway"
    assert "api_key" not in gateway.model_dump()
    assert "sk-enterprise-embedding-key" not in repr(gateway)


def test_openai_compatible_embedding_gateway_requires_model():
    gateway = OpenAICompatibleEmbeddingGateway(api_key="sk-test")

    with pytest.raises(EmbeddingGatewayConfigurationError, match="model is not configured"):
        gateway.embed(
            EmbeddingGatewayRequest(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                user_id="user_1",
                purpose="knowledge_query",
                input=["renewal risk"],
            )
        )


def test_openai_compatible_embedding_gateway_redacts_provider_error_body_credentials(monkeypatch):
    leaked_key = "sk-live-embedding-secret-1234567890"
    response_body = json.dumps(
        {
            "error": {
                "message": f"Incorrect API key provided: {leaked_key}.",
                "type": "invalid_request_error",
                "code": "invalid_api_key",
            }
        }
    ).encode("utf-8")

    def raise_provider_error(*args, **kwargs):
        raise HTTPError(
            url="https://model.example.com/v1/embeddings",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=BytesIO(response_body),
        )

    monkeypatch.setattr("taroai.embeddings.gateway.urlopen", raise_provider_error)
    gateway = OpenAICompatibleEmbeddingGateway(
        base_url="https://model.example.com/v1",
        api_key=leaked_key,
        default_model="text-embedding-3-small",
    )

    with pytest.raises(EmbeddingGatewayResponseError) as raised:
        gateway.embed(
            EmbeddingGatewayRequest(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                user_id="user_1",
                purpose="knowledge_query",
                input=["renewal risk"],
            )
        )

    message = str(raised.value)
    assert "embedding gateway returned HTTP 401" in message
    assert "invalid_api_key" in message
    assert "[REDACTED]" in message
    assert leaked_key not in message
