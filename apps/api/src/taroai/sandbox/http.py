import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import ProxyHandler, Request, build_opener

from pydantic import ConfigDict, Field, PrivateAttr

from taroai.errors import NotFoundError
from taroai.sandbox.adapter import SandboxAdapter, SandboxProviderUnavailableError
from taroai.sandbox.models import (
    SandboxCommand,
    SandboxCommandResult,
    SandboxControllerCapabilities,
    SandboxCreateRequest,
    SandboxFileRef,
    SandboxFileWrite,
    SandboxSnapshot,
    SandboxSession,
    SandboxSessionStatus,
)


class HttpSandboxAdapter(SandboxAdapter):
    base_url: str = Field(default="", min_length=0)
    api_key: str = ""
    timeout_seconds: int = Field(default=30, ge=1)
    enforce_capabilities: bool = True

    _capabilities_cache: SandboxControllerCapabilities | None = PrivateAttr(default=None)

    model_config = ConfigDict(extra="forbid")

    def get_capabilities(self) -> SandboxControllerCapabilities:
        if self._capabilities_cache is not None:
            return self._capabilities_cache
        response_body = self._request(
            "GET",
            "/capabilities",
            None,
            expected_statuses={200},
        )
        capabilities = SandboxControllerCapabilities.model_validate(response_body)
        self._validate_provider_context(capabilities.provider)
        self._capabilities_cache = capabilities
        return capabilities

    def create(self, request: SandboxCreateRequest) -> SandboxSession:
        self._require_capabilities_for_create(request)
        response_body = self._request(
            "POST",
            "/sessions",
            request.model_dump(mode="json"),
            expected_statuses={200, 201},
        )
        session = SandboxSession.model_validate(response_body)
        self._validate_session_context(
            session,
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
            run_id=request.run_id,
        )
        self._validate_provider_context(session.provider)
        if session.status != SandboxSessionStatus.ACTIVE:
            raise SandboxProviderUnavailableError(
                "sandbox provider did not create an active session"
            )
        return session

    def _require_capabilities_for_create(
        self,
        request: SandboxCreateRequest,
    ) -> None:
        if not self.enforce_capabilities:
            return
        capabilities = self.get_capabilities()
        missing = []
        if not capabilities.network_isolation:
            missing.append("network_isolation")
        if not capabilities.filesystem_isolation:
            missing.append("filesystem_isolation")
        if not capabilities.resource_limits:
            missing.append("resource_limits")
        if not capabilities.destroy_supported:
            missing.append("destroy_supported")
        if not capabilities.session_ttl_enforced:
            missing.append("session_ttl_enforced")
        if not capabilities.runtime_isolation:
            missing.append("runtime_isolation")
        if not capabilities.image_policy_enforced:
            missing.append("image_policy_enforced")
        if (
            capabilities.allowed_image_count is None
            or capabilities.allowed_image_count <= 0
        ):
            missing.append("allowed_image_count")
        if capabilities.max_session_ttl_seconds is None:
            missing.append("max_session_ttl_seconds")
        if capabilities.max_sessions is None:
            missing.append("max_sessions")
        if capabilities.max_sessions_per_tenant is None:
            missing.append("max_sessions_per_tenant")
        if capabilities.max_sessions_per_run is None:
            missing.append("max_sessions_per_run")
        if missing:
            raise SandboxProviderUnavailableError(
                "sandbox controller capabilities are insufficient: "
                + ", ".join(missing)
            )
        if request.timeout_seconds > capabilities.max_session_ttl_seconds:
            raise SandboxProviderUnavailableError(
                "sandbox session timeout exceeds controller limit"
            )
        all_active_sessions = [
            session
            for session in self.list_sessions()
            if session.status == SandboxSessionStatus.ACTIVE
        ]
        if len(all_active_sessions) >= capabilities.max_sessions:
            raise SandboxProviderUnavailableError(
                "sandbox controller session capacity is full"
            )
        active_sessions = [
            session
            for session in all_active_sessions
            if session.tenant_id == request.tenant_id
        ]
        if len(active_sessions) >= capabilities.max_sessions_per_tenant:
            raise SandboxProviderUnavailableError(
                "sandbox controller tenant session capacity is full"
            )
        run_session_count = len(
            [
                session
                for session in active_sessions
                if session.run_id == request.run_id
            ]
        )
        if run_session_count >= capabilities.max_sessions_per_run:
            raise SandboxProviderUnavailableError(
                "sandbox controller run session capacity is full"
            )

    def execute(self, command: SandboxCommand) -> SandboxCommandResult:
        response_body = self._request(
            "POST",
            "/commands",
            command.model_dump(mode="json"),
            expected_statuses={200, 201},
            not_found_message=f"Sandbox session not found: {command.session_id}",
            timeout_seconds=max(self.timeout_seconds, command.timeout_seconds + 5),
        )
        result = SandboxCommandResult.model_validate(response_body)
        self._validate_command_context(result, command)
        return result

    def upload_file(self, file_write: SandboxFileWrite) -> SandboxFileRef:
        response_body = self._request(
            "POST",
            "/files",
            file_write.model_dump(mode="json"),
            expected_statuses={200, 201},
            not_found_message=f"Sandbox session not found: {file_write.session_id}",
        )
        file_ref = SandboxFileRef.model_validate(response_body)
        self._validate_file_context(file_ref, file_write)
        return file_ref

    def download_file(self, tenant_id: str, session_id: str, path: str) -> SandboxFileRef:
        session = self.get_session(tenant_id, session_id)
        query = urlencode(
            {
                "tenant_id": tenant_id,
                "session_id": session_id,
                "workspace_id": session.workspace_id,
                "run_id": session.run_id,
                "path": path,
            }
        )
        response_body = self._request(
            "GET",
            f"/files?{query}",
            None,
            expected_statuses={200},
            not_found_message=f"Sandbox file not found: {path}",
        )
        file_ref = SandboxFileRef.model_validate(response_body)
        self._validate_file_identity(
            file_ref,
            tenant_id=tenant_id,
            session_id=session_id,
            path=path,
        )
        return file_ref

    def list_files(self, tenant_id: str, session_id: str) -> list[SandboxFileRef]:
        session = self.get_session(tenant_id, session_id)
        query = urlencode(
            {
                "tenant_id": tenant_id,
                "session_id": session_id,
                "workspace_id": session.workspace_id,
                "run_id": session.run_id,
            }
        )
        response_body = self._request(
            "GET",
            f"/files?{query}",
            None,
            expected_statuses={200},
            not_found_message=f"Sandbox session not found: {session_id}",
        )
        files = response_body.get("files")
        if not isinstance(files, list):
            raise SandboxProviderUnavailableError(
                "sandbox provider file list response must include files"
            )
        validated_files = [SandboxFileRef.model_validate(file_ref) for file_ref in files]
        for file_ref in validated_files:
            self._validate_file_identity(
                file_ref,
                tenant_id=tenant_id,
                session_id=session_id,
            )
        return validated_files

    def snapshot(self, tenant_id: str, session_id: str) -> SandboxSnapshot:
        session = self.get_session(tenant_id, session_id)
        response_body = self._request(
            "POST",
            "/snapshots",
            {
                "tenant_id": tenant_id,
                "workspace_id": session.workspace_id,
                "run_id": session.run_id,
                "session_id": session_id,
            },
            expected_statuses={200, 201},
            not_found_message=f"Sandbox session not found: {session_id}",
        )
        snapshot = SandboxSnapshot.model_validate(response_body)
        self._validate_snapshot_context(
            snapshot,
            tenant_id=tenant_id,
            workspace_id=session.workspace_id,
            run_id=session.run_id,
            session_id=session_id,
        )
        return snapshot

    def destroy(self, tenant_id: str, session_id: str) -> SandboxSession:
        query = urlencode({"tenant_id": tenant_id})
        response_body = self._request(
            "DELETE",
            f"/sessions/{quote(session_id, safe='')}?{query}",
            None,
            expected_statuses={200},
            not_found_message=f"Sandbox session not found: {session_id}",
        )
        session = SandboxSession.model_validate(response_body)
        self._validate_session_context(
            session,
            tenant_id=tenant_id,
            session_id=session_id,
        )
        if session.status != SandboxSessionStatus.DESTROYED:
            raise SandboxProviderUnavailableError(
                "sandbox provider did not destroy session"
            )
        if not self._destroy_confirmed(tenant_id, session_id):
            raise SandboxProviderUnavailableError(
                "sandbox provider did not confirm destroyed session"
            )
        return session

    def _destroy_confirmed(self, tenant_id: str, session_id: str) -> bool:
        sessions = self.list_sessions(tenant_id)
        return not any(
            session.id == session_id and session.status == SandboxSessionStatus.ACTIVE
            for session in sessions
        )

    def get_session(self, tenant_id: str, session_id: str) -> SandboxSession:
        query = urlencode({"tenant_id": tenant_id})
        response_body = self._request(
            "GET",
            f"/sessions/{quote(session_id, safe='')}?{query}",
            None,
            expected_statuses={200},
            not_found_message=f"Sandbox session not found: {session_id}",
        )
        session = SandboxSession.model_validate(response_body)
        self._validate_session_context(
            session,
            tenant_id=tenant_id,
            session_id=session_id,
        )
        return session

    def list_sessions(self, tenant_id: str | None = None) -> list[SandboxSession]:
        query = urlencode({"tenant_id": tenant_id}) if tenant_id is not None else ""
        path = f"/sessions?{query}" if query else "/sessions"
        response_body = self._request(
            "GET",
            path,
            None,
            expected_statuses={200},
        )
        sessions = response_body.get("sessions")
        if not isinstance(sessions, list):
            raise SandboxProviderUnavailableError(
                "sandbox provider session list response must include sessions"
            )
        validated_sessions = [
            SandboxSession.model_validate(session) for session in sessions
        ]
        if tenant_id is not None:
            for session in validated_sessions:
                self._validate_session_context(session, tenant_id=tenant_id)
        return validated_sessions

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        expected_statuses: set[int],
        not_found_message: str | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        if not self.base_url.strip():
            raise SandboxProviderUnavailableError(
                "sandbox provider endpoint is not configured"
            )
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = Request(
            f"{self.base_url.rstrip('/')}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            opener = build_opener(ProxyHandler({}))
            with opener.open(
                request, timeout=timeout_seconds or self.timeout_seconds
            ) as response:
                status_code = response.status
                response_body = self._load_json(response.read())
        except HTTPError as error:
            detail = error.read().decode("utf-8")
            if error.code == 404 and not_found_message is not None:
                raise NotFoundError(not_found_message) from error
            raise SandboxProviderUnavailableError(
                f"sandbox provider returned HTTP {error.code}: {detail}"
            ) from error
        except (URLError, TimeoutError) as error:
            raise SandboxProviderUnavailableError(
                f"sandbox provider request failed: {error}"
            ) from error

        if status_code not in expected_statuses:
            raise SandboxProviderUnavailableError(
                f"sandbox provider returned unexpected HTTP {status_code}"
            )
        return response_body

    def _load_json(self, raw_body: bytes) -> dict[str, Any]:
        try:
            parsed = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError as error:
            raise SandboxProviderUnavailableError(
                "sandbox provider returned invalid JSON"
            ) from error
        if not isinstance(parsed, dict):
            raise SandboxProviderUnavailableError(
                "sandbox provider response must be a JSON object"
            )
        return parsed

    def _validate_session_context(
        self,
        session: SandboxSession,
        tenant_id: str,
        workspace_id: str | None = None,
        run_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        self._require_response_value("tenant_id", tenant_id, session.tenant_id)
        if workspace_id is not None:
            self._require_response_value(
                "workspace_id",
                workspace_id,
                session.workspace_id,
            )
        if run_id is not None:
            self._require_response_value("run_id", run_id, session.run_id)
        if session_id is not None:
            self._require_response_value("session_id", session_id, session.id)

    def _validate_provider_context(self, actual_provider: str) -> None:
        expected = self._normalized_provider(self.provider)
        actual = self._normalized_provider(actual_provider)
        if expected == "http":
            return
        if actual != expected:
            raise SandboxProviderUnavailableError(
                "sandbox provider response context mismatch: "
                f"provider expected {self.provider!r}, got {actual_provider!r}"
            )

    def _normalized_provider(self, provider: str) -> str:
        normalized = provider.strip().lower()
        if normalized == "kubernetes":
            return "k8s"
        return normalized

    def _validate_command_context(
        self,
        result: SandboxCommandResult,
        command: SandboxCommand,
    ) -> None:
        self._require_response_value("tenant_id", command.tenant_id, result.tenant_id)
        self._require_response_value(
            "workspace_id",
            command.workspace_id,
            result.workspace_id,
        )
        self._require_response_value("run_id", command.run_id, result.run_id)
        self._require_response_value(
            "session_id",
            command.session_id,
            result.session_id,
        )
        self._require_response_value("command", command.command, result.command)

    def _validate_file_context(
        self,
        file_ref: SandboxFileRef,
        file_write: SandboxFileWrite,
    ) -> None:
        self._validate_file_identity(
            file_ref,
            tenant_id=file_write.tenant_id,
            session_id=file_write.session_id,
            path=file_write.path,
        )
        self._require_response_value(
            "workspace_id",
            file_write.workspace_id,
            file_ref.workspace_id,
        )
        self._require_response_value("run_id", file_write.run_id, file_ref.run_id)

    def _validate_file_identity(
        self,
        file_ref: SandboxFileRef,
        tenant_id: str,
        session_id: str,
        path: str | None = None,
    ) -> None:
        self._require_response_value("tenant_id", tenant_id, file_ref.tenant_id)
        self._require_response_value("session_id", session_id, file_ref.session_id)
        if path is not None:
            self._require_response_value("path", path, file_ref.path)

    def _validate_snapshot_context(
        self,
        snapshot: SandboxSnapshot,
        tenant_id: str,
        workspace_id: str,
        run_id: str,
        session_id: str,
    ) -> None:
        self._require_response_value("tenant_id", tenant_id, snapshot.tenant_id)
        self._require_response_value("workspace_id", workspace_id, snapshot.workspace_id)
        self._require_response_value("run_id", run_id, snapshot.run_id)
        self._require_response_value("session_id", session_id, snapshot.session_id)

    def _require_response_value(
        self,
        field_name: str,
        expected: str,
        actual: str,
    ) -> None:
        if actual != expected:
            raise SandboxProviderUnavailableError(
                "sandbox provider response context mismatch: "
                f"{field_name} expected {expected!r}, got {actual!r}"
            )
