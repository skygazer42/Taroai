import json
import re
import threading
from typing import Any, Literal
from urllib.parse import urlencode, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from taroai.connectors.models import ConnectorAuthMode, ConnectorDefinition, ConnectorType
from taroai.db import DatabaseConfig
from taroai.db.connection import connect_database
from taroai.secrets import (
    SecretAccessDeniedError,
    SecretLeaseExpiredError,
    SecretNotFoundError,
    SecretRef,
    SecretScope,
    SecretService,
)


class ConnectorDispatchError(RuntimeError):
    pass


_HTTP_CLIENT: httpx.Client | None = None
_HTTP_CLIENT_LOCK = threading.Lock()


def _shared_http_client() -> httpx.Client:
    """Process-wide pooled HTTP client for connector dispatch calls."""
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None:
        with _HTTP_CLIENT_LOCK:
            if _HTTP_CLIENT is None:
                _HTTP_CLIENT = httpx.Client(
                    timeout=httpx.Timeout(10.0),
                    follow_redirects=True,
                )
    return _HTTP_CLIENT


class ConnectorCredentialExpiredError(ConnectorDispatchError):
    """Signals that a connector credential must be replaced before retrying."""

    def __init__(self, connector_id: str) -> None:
        super().__init__("connector authorization has expired")
        self.connector_id = connector_id


class InternalApiAuthConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["api_key_header", "bearer", "oauth2_bearer"] = "bearer"
    header_name: str = Field(default="authorization", pattern=r"^[A-Za-z0-9-]+$")
    scheme: str = Field(default="Bearer", min_length=1)
    lease_ttl_seconds: int = Field(default=60, ge=1)

    @field_validator("header_name")
    @classmethod
    def validate_header_name(cls, value: str) -> str:
        normalized = value.lower()
        if normalized in {"host", "content-length", "connection", "transfer-encoding"}:
            raise ValueError("header_name is reserved")
        return normalized


class InternalApiConnectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(min_length=1)
    allowed_methods: list[str] = Field(default_factory=lambda: ["GET"])
    allowed_paths: list[str] = Field(default_factory=list)
    timeout_seconds: int = Field(default=10, ge=1)
    max_response_bytes: int = Field(default=1_048_576, ge=1)
    auth: InternalApiAuthConfig | None = None

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url must be an HTTP or HTTPS URL")
        if parsed.query or parsed.fragment:
            raise ValueError("base_url must not include query or fragment")
        return value.rstrip("/")

    @field_validator("allowed_methods")
    @classmethod
    def validate_allowed_methods(cls, value: list[str]) -> list[str]:
        normalized = [method.strip().upper() for method in value]
        if not normalized or any(not method for method in normalized):
            raise ValueError("allowed_methods must not be empty")
        return list(dict.fromkeys(normalized))

    @field_validator("allowed_paths")
    @classmethod
    def validate_allowed_paths(cls, value: list[str]) -> list[str]:
        for path in value:
            if not path.startswith("/"):
                raise ValueError("allowed_paths entries must start with /")
        return list(dict.fromkeys(value))


class DatabaseConnectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed_tables: list[str] = Field(default_factory=list)
    allowed_schemas: list[str] = Field(default_factory=list)
    read_only: bool = True
    max_rows: int = Field(default=100, ge=1)
    timeout_seconds: int = Field(default=10, ge=1)
    lease_ttl_seconds: int = Field(default=60, ge=1)

    @field_validator("allowed_tables")
    @classmethod
    def validate_allowed_tables(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("allowed_tables must not be empty")
        return [_validate_sql_identifier(item, "allowed_tables") for item in value]

    @field_validator("allowed_schemas")
    @classmethod
    def validate_allowed_schemas(cls, value: list[str]) -> list[str]:
        return [_validate_sql_identifier(item, "allowed_schemas") for item in value]


class ConnectorHttpRequest(BaseModel):
    method: str = Field(min_length=1)
    url: str = Field(min_length=1)
    headers: dict[str, str] = Field(default_factory=dict)
    body: bytes | None = None
    timeout_seconds: int = Field(default=10, ge=1)
    max_response_bytes: int = Field(default=1_048_576, ge=1)


class ConnectorHttpResponse(BaseModel):
    status_code: int = Field(ge=100, le=599)
    headers: dict[str, str] = Field(default_factory=dict)
    body: bytes = b""


class ConnectorDispatchResult(BaseModel):
    output: dict[str, Any] = Field(default_factory=dict)
    status_code: int = Field(ge=100, le=599)
    response_size_bytes: int = Field(ge=0)


class UrlLibConnectorHttpClient(BaseModel):
    """Pooled httpx connector HTTP client (class name kept for import compatibility)."""

    def send(self, request: ConnectorHttpRequest) -> ConnectorHttpResponse:
        try:
            with _shared_http_client().stream(
                request.method,
                request.url,
                content=request.body,
                headers=request.headers,
                timeout=request.timeout_seconds,
            ) as response:
                # 与 urlopen 的 read(max+1) 语义一致：最多读取 max+1 字节，
                # 让 _build_result 能检测响应超限。
                body = b""
                for chunk in response.iter_bytes():
                    body += chunk
                    if len(body) > request.max_response_bytes:
                        break
                return ConnectorHttpResponse(
                    status_code=response.status_code,
                    headers=dict(response.headers.items()),
                    body=body[: request.max_response_bytes + 1],
                )
        except httpx.HTTPError as error:
            raise ConnectorDispatchError(f"connector HTTP request failed: {error}") from error


class ConnectorDispatchService(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    http_client: Any | None = None
    mcp_client: Any | None = None
    secret_service: SecretService | None = None

    def discover_mcp_capabilities(
        self,
        connector: ConnectorDefinition,
    ) -> list[Any]:
        from taroai.connectors.mcp import (
            McpCredentialExpiredError,
            McpConnectorError,
            discover_mcp_capabilities,
        )

        try:
            return discover_mcp_capabilities(
                connector,
                self.secret_service,
                self.mcp_client,
            )
        except McpCredentialExpiredError as error:
            raise ConnectorCredentialExpiredError(connector.id) from error
        except McpConnectorError as error:
            raise ConnectorDispatchError(str(error)) from error

    def preflight(
        self,
        connector: ConnectorDefinition,
        tool_input: dict[str, Any],
        tool_name: str,
    ) -> None:
        """验证连接器及本次动作可执行，但不发出有副作用的请求。"""
        if connector.type in {
            ConnectorType.INTERNAL_API,
            ConnectorType.SAAS,
            ConnectorType.WEB,
        }:
            if "internal_api" not in connector.metadata:
                # 兼容仅用于策略评估的匿名连接器；真实 SaaS/Web 连接器必须配置适配器。
                if (
                    connector.type in {ConnectorType.SAAS, ConnectorType.WEB}
                    and connector.auth_mode == ConnectorAuthMode.NONE
                ):
                    return
                raise ConnectorDispatchError("connector dispatch config is required")
            config = self._internal_api_config(connector)
            self._build_request(config, connector, tool_input, tool_name)
            return
        if connector.type == ConnectorType.DATABASE:
            config = self._database_config(connector)
            sql = tool_input.get("sql")
            if not isinstance(sql, str):
                raise ConnectorDispatchError("sql is required")
            if not isinstance(tool_input.get("parameters", []), list):
                raise ConnectorDispatchError("parameters must be an array")
            self._validate_database_query(sql, config)
            self._database_url(connector, config, tool_name)
            return
        if connector.type == ConnectorType.MCP_SERVER:
            capability_name = tool_name.split(".", 2)[-1]
            capabilities = self.discover_mcp_capabilities(connector)
            if not any(item.name == capability_name for item in capabilities):
                raise ConnectorDispatchError(
                    f"MCP capability is not available: {capability_name}"
                )
            return
        raise ConnectorDispatchError("connector type is not executable")

    def dispatch(
        self,
        connector: ConnectorDefinition,
        tool_input: dict[str, Any],
        tool_name: str | None = None,
    ) -> ConnectorDispatchResult | None:
        resolved_tool_name = tool_name or f"connector.{connector.id}"
        if connector.type in {
            ConnectorType.INTERNAL_API,
            ConnectorType.SAAS,
            ConnectorType.WEB,
        } and "internal_api" in connector.metadata:
            config = self._internal_api_config(connector)
            request = self._build_request(
                config=config,
                connector=connector,
                tool_input=tool_input,
                tool_name=resolved_tool_name,
            )
            response = self._send(request)
            if (
                connector.auth_mode == ConnectorAuthMode.OAUTH2
                and response.status_code in {401, 403}
            ):
                raise ConnectorCredentialExpiredError(connector.id)
            return self._build_result(response, request.max_response_bytes)
        if connector.type == ConnectorType.DATABASE:
            return self._dispatch_database_connector(
                connector=connector,
                tool_input=tool_input,
                tool_name=resolved_tool_name,
            )
        if connector.type == ConnectorType.MCP_SERVER:
            from taroai.connectors.mcp import (
                McpConnectorError,
                McpCredentialExpiredError,
                call_mcp_tool,
            )

            parts = resolved_tool_name.split(".", 2)
            if len(parts) != 3:
                raise ConnectorDispatchError("MCP dispatch requires a capability tool name")
            try:
                output, response_size = call_mcp_tool(
                    connector,
                    parts[2],
                    tool_input,
                    self.secret_service,
                    self.mcp_client,
                )
            except McpCredentialExpiredError as error:
                raise ConnectorCredentialExpiredError(connector.id) from error
            except McpConnectorError as error:
                raise ConnectorDispatchError(str(error)) from error
            return ConnectorDispatchResult(
                output=output,
                status_code=200,
                response_size_bytes=response_size,
            )
        return None

    def _internal_api_config(
        self,
        connector: ConnectorDefinition,
    ) -> InternalApiConnectorConfig:
        raw_config = connector.metadata.get("internal_api")
        if not isinstance(raw_config, dict):
            raise ConnectorDispatchError("internal API connector config is required")
        try:
            return InternalApiConnectorConfig.model_validate(raw_config)
        except ValueError as error:
            raise ConnectorDispatchError(str(error)) from error

    def _database_config(
        self,
        connector: ConnectorDefinition,
    ) -> DatabaseConnectorConfig:
        raw_config = connector.metadata.get("database")
        if not isinstance(raw_config, dict):
            raise ConnectorDispatchError("database connector config is required")
        try:
            return DatabaseConnectorConfig.model_validate(raw_config)
        except ValueError as error:
            raise ConnectorDispatchError(str(error)) from error

    def _build_request(
        self,
        config: InternalApiConnectorConfig,
        connector: ConnectorDefinition,
        tool_input: dict[str, Any],
        tool_name: str,
    ) -> ConnectorHttpRequest:
        if "headers" in tool_input:
            raise ConnectorDispatchError("caller supplied headers are not allowed")
        if "body" in tool_input:
            raise ConnectorDispatchError("raw body is not allowed; use json")

        method = str(tool_input.get("method", "GET")).strip().upper()
        if method not in config.allowed_methods:
            raise ConnectorDispatchError("method is not allowed")

        path = tool_input.get("path")
        if not isinstance(path, str):
            raise ConnectorDispatchError("path is required")
        path = self._normalize_path(path)
        if not self._path_allowed(path, config.allowed_paths):
            raise ConnectorDispatchError("path is not allowed")

        query = tool_input.get("query", {})
        if not isinstance(query, dict):
            raise ConnectorDispatchError("query must be an object")

        body = None
        headers: dict[str, str] = {}
        if "json" in tool_input:
            body = json.dumps(tool_input["json"], separators=(",", ":")).encode("utf-8")
            headers["content-type"] = "application/json"
        headers.update(self._credential_headers(config, connector, tool_name))

        url = f"{config.base_url}{path}"
        query_string = urlencode(query, doseq=True)
        if query_string:
            url = f"{url}?{query_string}"

        return ConnectorHttpRequest(
            method=method,
            url=url,
            headers=headers,
            body=body,
            timeout_seconds=config.timeout_seconds,
            max_response_bytes=config.max_response_bytes,
        )

    def _credential_headers(
        self,
        config: InternalApiConnectorConfig,
        connector: ConnectorDefinition,
        tool_name: str,
    ) -> dict[str, str]:
        if connector.auth_mode == ConnectorAuthMode.NONE:
            return {}
        if connector.auth_mode not in {ConnectorAuthMode.API_KEY, ConnectorAuthMode.OAUTH2}:
            raise ConnectorDispatchError("connector auth mode is not supported for internal API dispatch")
        if config.auth is None:
            raise ConnectorDispatchError("internal API auth config is required")
        if connector.credential_ref is None:
            raise ConnectorDispatchError("connector credential reference is required")
        if self.secret_service is None:
            raise ConnectorDispatchError("secret service is not configured")
        if connector.auth_mode == ConnectorAuthMode.API_KEY and config.auth.mode == "oauth2_bearer":
            raise ConnectorDispatchError("OAuth2 bearer auth requires oauth2 connector auth mode")
        if connector.auth_mode == ConnectorAuthMode.OAUTH2 and config.auth.mode != "oauth2_bearer":
            raise ConnectorDispatchError("OAuth2 connector requires oauth2_bearer auth config")

        credential_ref = connector.credential_ref
        if credential_ref.secret_backend and credential_ref.secret_external_name:
            self.secret_service.register_secret_ref(
                SecretRef(
                    id=credential_ref.secret_ref_id,
                    tenant_id=connector.tenant_id,
                    workspace_id=connector.workspace_id,
                    name=f"{connector.display_name} credential",
                    scope=SecretScope(
                        tenant_id=connector.tenant_id,
                        workspace_id=connector.workspace_id,
                        allowed_tool_names=[tool_name],
                        actions=credential_ref.required_actions,
                    ),
                    backend=credential_ref.secret_backend,
                    external_name=credential_ref.secret_external_name,
                )
            )
        try:
            lease = self.secret_service.create_lease(
                tenant_id=connector.tenant_id,
                workspace_id=connector.workspace_id,
                secret_id=credential_ref.secret_ref_id,
                tool_name=tool_name,
                actions=credential_ref.required_actions,
                ttl_seconds=config.auth.lease_ttl_seconds,
            )
            secret_value = self.secret_service.resolve_lease_value(
                tenant_id=connector.tenant_id,
                lease_token=lease.lease_token,
            )
        except (SecretLeaseExpiredError, SecretNotFoundError) as error:
            raise ConnectorCredentialExpiredError(connector.id) from error
        except SecretAccessDeniedError as error:
            raise ConnectorDispatchError("connector credential is not available") from error

        if config.auth.mode == "api_key_header":
            return {config.auth.header_name: secret_value}
        return {config.auth.header_name: f"{config.auth.scheme} {secret_value}"}

    def _normalize_path(self, path: str) -> str:
        parsed = urlsplit(path)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            raise ConnectorDispatchError("path must be a relative HTTP path")
        if not parsed.path.startswith("/"):
            raise ConnectorDispatchError("path must start with /")
        if "/../" in parsed.path or parsed.path.endswith("/.."):
            raise ConnectorDispatchError("path must not traverse parent directories")
        return parsed.path

    def _path_allowed(self, path: str, allowed_paths: list[str]) -> bool:
        for allowed_path in allowed_paths:
            if allowed_path.endswith("/*") and path.startswith(allowed_path[:-1]):
                return True
            if path == allowed_path:
                return True
        return False

    def _send(self, request: ConnectorHttpRequest) -> ConnectorHttpResponse:
        client = self.http_client or UrlLibConnectorHttpClient()
        return client.send(request)

    def _build_result(
        self,
        response: ConnectorHttpResponse,
        max_response_bytes: int,
    ) -> ConnectorDispatchResult:
        response_size = len(response.body)
        if response_size > max_response_bytes:
            raise ConnectorDispatchError("connector response exceeds max_response_bytes")
        if response.status_code >= 400:
            raise ConnectorDispatchError(
                f"connector returned HTTP {response.status_code}"
            )
        content_type = self._content_type(response.headers)
        return ConnectorDispatchResult(
            output={
                "status_code": response.status_code,
                "content_type": content_type,
                "body": self._decode_body(response.body, content_type),
            },
            status_code=response.status_code,
            response_size_bytes=response_size,
        )

    def _content_type(self, headers: dict[str, str]) -> str:
        for key, value in headers.items():
            if key.lower() == "content-type":
                return value.split(";", 1)[0].strip().lower()
        return ""

    def _decode_body(self, body: bytes, content_type: str) -> Any:
        if not body:
            return None
        if content_type == "application/json":
            try:
                return json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ConnectorDispatchError("connector JSON response is invalid") from error
        return body.decode("utf-8", errors="replace")

    def _dispatch_database_connector(
        self,
        connector: ConnectorDefinition,
        tool_input: dict[str, Any],
        tool_name: str,
    ) -> ConnectorDispatchResult:
        config = self._database_config(connector)
        sql = tool_input.get("sql")
        if not isinstance(sql, str):
            raise ConnectorDispatchError("sql is required")
        parameters = tool_input.get("parameters", [])
        if not isinstance(parameters, list):
            raise ConnectorDispatchError("parameters must be an array")
        statement = self._validate_database_query(sql, config)
        database_url = self._database_url(connector, config, tool_name)
        try:
            with connect_database(DatabaseConfig(url=database_url)) as connection:
                cursor = connection.execute(statement, tuple(parameters))
                rows = cursor.fetchmany(config.max_rows)
                columns = [description[0] for description in (cursor.description or [])]
        except Exception as error:
            raise ConnectorDispatchError("database connector query failed") from error

        output_rows = [self._row_to_dict(row, columns) for row in rows]
        output = {
            "columns": columns,
            "rows": output_rows,
            "row_count": len(output_rows),
        }
        return ConnectorDispatchResult(
            output=output,
            status_code=200,
            response_size_bytes=len(json.dumps(output, separators=(",", ":")).encode("utf-8")),
        )

    def _database_url(
        self,
        connector: ConnectorDefinition,
        config: DatabaseConnectorConfig,
        tool_name: str,
    ) -> str:
        if connector.auth_mode != ConnectorAuthMode.DATABASE_PASSWORD:
            raise ConnectorDispatchError("database dispatch requires database_password auth mode")
        if connector.credential_ref is None:
            raise ConnectorDispatchError("connector credential reference is required")
        if self.secret_service is None:
            raise ConnectorDispatchError("secret service is not configured")
        try:
            lease = self.secret_service.create_lease(
                tenant_id=connector.tenant_id,
                workspace_id=connector.workspace_id,
                secret_id=connector.credential_ref.secret_ref_id,
                tool_name=tool_name,
                actions=connector.credential_ref.required_actions,
                ttl_seconds=config.lease_ttl_seconds,
            )
            return self.secret_service.resolve_lease_value(
                tenant_id=connector.tenant_id,
                lease_token=lease.lease_token,
            )
        except (SecretLeaseExpiredError, SecretNotFoundError) as error:
            raise ConnectorCredentialExpiredError(connector.id) from error
        except SecretAccessDeniedError as error:
            raise ConnectorDispatchError("connector credential is not available") from error

    def _validate_database_query(
        self,
        sql: str,
        config: DatabaseConnectorConfig,
    ) -> str:
        statement = sql.strip()
        if not statement:
            raise ConnectorDispatchError("sql is required")
        if ";" in statement:
            raise ConnectorDispatchError("only a single SELECT query is allowed")
        if not re.match(r"(?is)^select\s+", statement):
            raise ConnectorDispatchError("only SELECT queries are allowed")
        referenced_tables = self._referenced_tables(statement)
        if not referenced_tables:
            raise ConnectorDispatchError("query must reference at least one table")
        allowed_tables = {item.lower() for item in config.allowed_tables}
        allowed_schemas = {item.lower() for item in config.allowed_schemas}
        for schema, table in referenced_tables:
            if table.lower() not in allowed_tables:
                raise ConnectorDispatchError("table is not allowed")
            if schema is not None and allowed_schemas and schema.lower() not in allowed_schemas:
                raise ConnectorDispatchError("schema is not allowed")
            if schema is not None and not allowed_schemas:
                raise ConnectorDispatchError("schema is not allowed")
        return statement

    def _referenced_tables(self, statement: str) -> list[tuple[str | None, str]]:
        references: list[tuple[str | None, str]] = []
        for match in re.finditer(
            r'(?is)\b(?:from|join)\s+("?[A-Za-z_][A-Za-z0-9_]*"?)(?:\.("?[A-Za-z_][A-Za-z0-9_]*"?))?',
            statement,
        ):
            first = self._normalize_sql_name(match.group(1))
            second = self._normalize_sql_name(match.group(2)) if match.group(2) is not None else None
            if second is None:
                references.append((None, first))
            else:
                references.append((first, second))
        return references

    def _normalize_sql_name(self, value: str) -> str:
        return value.strip('"')

    def _row_to_dict(self, row: Any, columns: list[str]) -> dict[str, Any]:
        if hasattr(row, "keys"):
            return {key: row[key] for key in row.keys()}
        return {column: row[index] for index, column in enumerate(columns)}


def _validate_sql_identifier(value: str, field_name: str) -> str:
    item = value.strip()
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", item):
        raise ValueError(f"{field_name} entries must be SQL identifiers")
    return item
