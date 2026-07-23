import json
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel, ConfigDict, Field, field_validator

from taroai.connectors.models import (
    ConnectorAuthMode,
    ConnectorCapability,
    ConnectorDefinition,
)
from taroai.secrets import (
    SecretAccessDeniedError,
    SecretLeaseExpiredError,
    SecretNotFoundError,
    SecretService,
)


class McpConnectorError(RuntimeError):
    pass


class McpCredentialExpiredError(McpConnectorError):
    pass


class McpConnectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1)
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    max_response_bytes: int = Field(default=1_048_576, ge=1)
    max_tools: int = Field(default=256, ge=1, le=1024)
    lease_ttl_seconds: int = Field(default=60, ge=1, le=300)
    auth_header: str = Field(
        default="authorization",
        pattern=r"^[A-Za-z0-9-]+$",
    )
    auth_scheme: str = Field(default="Bearer", max_length=40)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("MCP URL must use HTTP or HTTPS")
        if parsed.username or parsed.password or parsed.fragment:
            raise ValueError("MCP URL must not contain credentials or a fragment")
        return value

    @field_validator("auth_header")
    @classmethod
    def validate_auth_header(cls, value: str) -> str:
        normalized = value.lower()
        if normalized in {"host", "content-length", "connection", "transfer-encoding"}:
            raise ValueError("MCP auth_header is reserved")
        return normalized


class StreamableHttpMcpClient:
    def list_tools(
        self,
        config: McpConnectorConfig,
        headers: dict[str, str],
    ) -> list[dict[str, Any]]:
        try:
            return anyio.run(self._list_tools, config, headers)
        except McpConnectorError:
            raise
        except Exception as error:
            raise McpConnectorError("MCP tool discovery failed") from error

    def call_tool(
        self,
        config: McpConnectorConfig,
        name: str,
        arguments: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        try:
            return anyio.run(self._call_tool, config, name, arguments, headers)
        except McpConnectorError:
            raise
        except Exception as error:
            raise McpConnectorError("MCP tool call failed") from error

    @asynccontextmanager
    async def _session(
        self,
        config: McpConnectorConfig,
        headers: dict[str, str],
    ):
        async with httpx.AsyncClient(
            headers=headers,
            timeout=config.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        ) as http_client:
            async with streamable_http_client(
                config.url,
                http_client=http_client,
            ) as (read_stream, write_stream, _):
                async with ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timedelta(seconds=config.timeout_seconds),
                ) as session:
                    await session.initialize()
                    yield session

    async def _list_tools(
        self,
        config: McpConnectorConfig,
        headers: dict[str, str],
    ) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor = None
        async with self._session(config, headers) as session:
            while True:
                result = await session.list_tools(cursor=cursor)
                tools.extend(
                    tool.model_dump(mode="json", by_alias=True, exclude_none=True)
                    for tool in result.tools
                )
                if len(tools) > config.max_tools:
                    raise McpConnectorError("MCP server exposes too many tools")
                cursor = result.nextCursor
                if not cursor:
                    return tools

    async def _call_tool(
        self,
        config: McpConnectorConfig,
        name: str,
        arguments: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        async with self._session(config, headers) as session:
            result = await session.call_tool(
                name,
                arguments=arguments,
                read_timeout_seconds=timedelta(seconds=config.timeout_seconds),
            )
        return result.model_dump(mode="json", by_alias=True, exclude_none=True)


def discover_mcp_capabilities(
    connector: ConnectorDefinition,
    secret_service: SecretService | None,
    client: Any | None = None,
) -> list[ConnectorCapability]:
    config = _config(connector)
    tools = (client or StreamableHttpMcpClient()).list_tools(
        config,
        _auth_headers(connector, secret_service, config, "discover"),
    )
    _check_size(tools, config.max_response_bytes)
    existing = {capability.name: capability for capability in connector.capabilities}
    capabilities: list[ConnectorCapability] = []
    risk_rank = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    for tool in tools:
        name = tool.get("name")
        input_schema = tool.get("inputSchema")
        if not isinstance(name, str) or not name or not isinstance(input_schema, dict):
            raise McpConnectorError("MCP server returned an invalid tool definition")
        annotations = tool.get("annotations") or {}
        if not isinstance(annotations, dict):
            raise McpConnectorError("MCP server returned invalid tool annotations")
        read_only = annotations.get("readOnlyHint") is True
        destructive = annotations.get("destructiveHint") is True
        risk_level = "high" if destructive else "low" if read_only else "medium"
        approval_required = not read_only
        previous = existing.get(name)
        if previous is not None:
            risk_level = max(
                (risk_level, previous.risk_level),
                key=risk_rank.__getitem__,
            )
            approval_required = approval_required or previous.approval_required
        output_schema = tool.get("outputSchema")
        capabilities.append(
            ConnectorCapability(
                name=name,
                description=str(tool.get("description") or "")[:4000],
                input_schema=input_schema,
                output_schema=(
                    output_schema
                    if isinstance(output_schema, dict)
                    else {"type": "object"}
                ),
                required_scopes=(previous.required_scopes if previous else []),
                risk_level=risk_level,
                approval_required=approval_required,
                enabled=previous.enabled if previous else True,
            )
        )
    if not capabilities:
        raise McpConnectorError("MCP server exposes no tools")
    return capabilities


def call_mcp_tool(
    connector: ConnectorDefinition,
    capability_name: str,
    arguments: dict[str, Any],
    secret_service: SecretService | None,
    client: Any | None = None,
) -> tuple[dict[str, Any], int]:
    config = _config(connector)
    result = (client or StreamableHttpMcpClient()).call_tool(
        config,
        capability_name,
        arguments,
        _auth_headers(connector, secret_service, config, capability_name),
    )
    size = _check_size(result, config.max_response_bytes)
    if result.get("isError") is True:
        raise McpConnectorError("MCP tool returned an error")
    output = {"content": result.get("content", [])}
    if result.get("structuredContent") is not None:
        output["structured_content"] = result["structuredContent"]
    return output, size


def _config(connector: ConnectorDefinition) -> McpConnectorConfig:
    raw = connector.metadata.get("mcp")
    if not isinstance(raw, dict):
        raise McpConnectorError("MCP connector config is required")
    try:
        return McpConnectorConfig.model_validate(raw)
    except ValueError as error:
        raise McpConnectorError(str(error)) from error


def _auth_headers(
    connector: ConnectorDefinition,
    secret_service: SecretService | None,
    config: McpConnectorConfig,
    operation: str,
) -> dict[str, str]:
    if connector.auth_mode == ConnectorAuthMode.NONE:
        return {}
    if connector.auth_mode not in {
        ConnectorAuthMode.API_KEY,
        ConnectorAuthMode.OAUTH2,
        ConnectorAuthMode.MCP,
    }:
        raise McpConnectorError("connector auth mode is not supported for MCP")
    if connector.credential_ref is None or secret_service is None:
        raise McpCredentialExpiredError("MCP connector credential is not configured")
    try:
        lease = secret_service.create_lease(
            tenant_id=connector.tenant_id,
            workspace_id=connector.workspace_id,
            secret_id=connector.credential_ref.secret_ref_id,
            tool_name=f"connector.{connector.id}.{operation}",
            actions=connector.credential_ref.required_actions,
            ttl_seconds=config.lease_ttl_seconds,
        )
        value = secret_service.resolve_lease_value(
            tenant_id=connector.tenant_id,
            lease_token=lease.lease_token,
        )
    except (SecretLeaseExpiredError, SecretNotFoundError) as error:
        raise McpCredentialExpiredError(
            "MCP connector credential is not available"
        ) from error
    except SecretAccessDeniedError as error:
        raise McpConnectorError("MCP connector credential is not available") from error
    header_value = f"{config.auth_scheme} {value}".strip()
    return {config.auth_header: header_value}


def _check_size(value: Any, limit: int) -> int:
    size = len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    if size > limit:
        raise McpConnectorError("MCP response exceeds max_response_bytes")
    return size
