import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from taroai.agent_engines.models import AgentEngineConnection, AgentEngineSession


class AgentEngineTransportError(RuntimeError):
    pass


class AgentEngineAdapter:
    def capabilities(self, connection: AgentEngineConnection) -> dict[str, Any]:
        raise NotImplementedError

    def create_session(self, connection: AgentEngineConnection, payload: dict[str, Any], token: str | None = None) -> dict[str, Any]:
        raise NotImplementedError

    def operation(self, connection: AgentEngineConnection, session: AgentEngineSession, operation: str, payload: dict[str, Any] | None = None, token: str | None = None, method: str = "POST") -> dict[str, Any]:
        raise NotImplementedError


class RemoteAgentEngineAdapter(AgentEngineAdapter):
    timeout_seconds: int = 30

    def capabilities(self, connection: AgentEngineConnection) -> dict[str, Any]:
        return self._request(connection, "GET", "/v1/capabilities", None, None)

    def create_session(self, connection: AgentEngineConnection, payload: dict[str, Any], token: str | None = None) -> dict[str, Any]:
        return self._request(connection, "POST", "/v1/sessions", payload, token)

    def operation(self, connection: AgentEngineConnection, session: AgentEngineSession, operation: str, payload: dict[str, Any] | None = None, token: str | None = None, method: str = "POST") -> dict[str, Any]:
        external_id = session.external_session_id or session.id
        return self._request(connection, method, f"/v1/sessions/{external_id}/{operation}".rstrip("/"), payload, token)

    def _request(self, connection: AgentEngineConnection, method: str, path: str, payload: dict[str, Any] | None, token: str | None) -> dict[str, Any]:
        if not connection.endpoint_url:
            raise AgentEngineTransportError("Remote Agent Engine endpoint is missing")
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Accept": "application/json", "X-Taroai-Engine-Type": connection.engine_type.value}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = Request(f"{connection.endpoint_url}{path}", data=body, headers=headers, method=method)
        try:
            with build_opener().open(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise AgentEngineTransportError(f"Engine returned HTTP {error.code}: {detail[:1000]}") from error
        except (URLError, TimeoutError, OSError) as error:
            raise AgentEngineTransportError(f"Engine transport failed: {error}") from error
        if not raw:
            return {}
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AgentEngineTransportError("Engine returned invalid JSON") from error
        if not isinstance(value, dict):
            raise AgentEngineTransportError("Engine response must be a JSON object")
        return value
