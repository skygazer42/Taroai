import base64
import json
from binascii import Error as Base64DecodeError
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import ProxyHandler, Request, build_opener

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from taroai.sandbox.models import (
    BrowserAction,
    BrowserActionType,
    BrowserObservation,
    BrowserSession,
)
from taroai.errors import NotFoundError


class BrowserProviderUnavailableError(RuntimeError):
    pass


class BrowserController(BaseModel):
    provider: str = "disabled"

    def capabilities(self) -> "BrowserControllerCapabilities":
        raise BrowserProviderUnavailableError("browser provider is disabled")

    def open_session(
        self,
        tenant_id: str,
        workspace_id: str,
        run_id: str,
        session_id: str,
        storage_state: dict[str, Any] | None = None,
        profile_id: str | None = None,
    ) -> BrowserSession:
        raise BrowserProviderUnavailableError("browser provider is disabled")

    def get_session(
        self,
        tenant_id: str,
        session_id: str,
        workspace_id: str | None = None,
        run_id: str | None = None,
    ) -> BrowserSession:
        raise BrowserProviderUnavailableError("browser provider is disabled")

    def list_sessions(self, tenant_id: str | None = None) -> list[BrowserSession]:
        raise BrowserProviderUnavailableError("browser provider is disabled")

    def delete_session(
        self,
        tenant_id: str,
        session_id: str,
        workspace_id: str | None = None,
        run_id: str | None = None,
    ) -> BrowserSession:
        raise BrowserProviderUnavailableError("browser provider is disabled")

    def export_session_state(
        self,
        tenant_id: str,
        session_id: str,
        workspace_id: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        raise BrowserProviderUnavailableError("browser provider session state is unavailable")

    def apply(self, action: BrowserAction) -> BrowserObservation:
        raise BrowserProviderUnavailableError("browser provider is disabled")


class BrowserControllerCapabilities(BaseModel):
    provider: str = Field(min_length=1)
    auth_required: bool = False
    session_ttl_enforced: bool = False
    max_session_ttl_seconds: int = Field(default=0, ge=0)
    max_sessions: int = Field(default=0, ge=0)
    max_sessions_per_tenant: int = Field(default=0, ge=0)
    max_sessions_per_run: int = Field(default=0, ge=0)
    navigation_allowlist_enforced: bool = False
    navigation_allowed_host_count: int = Field(default=0, ge=0)

    model_config = ConfigDict(extra="forbid")


class HttpBrowserController(BrowserController):
    base_url: str = Field(default="", min_length=0)
    api_key: str = ""
    timeout_seconds: int = Field(default=30, ge=1)
    enforce_capabilities: bool = True

    _capabilities_cache: BrowserControllerCapabilities | None = PrivateAttr(default=None)
    _session_context_cache: dict[tuple[str, str], BrowserSession] = PrivateAttr(
        default_factory=dict
    )

    model_config = ConfigDict(extra="forbid")

    def capabilities(self) -> BrowserControllerCapabilities:
        if self._capabilities_cache is not None:
            return self._capabilities_cache
        response_body = self._request(
            "GET",
            "/capabilities",
            None,
            expected_statuses={200},
        )
        capabilities = BrowserControllerCapabilities.model_validate(response_body)
        if capabilities.provider != self.provider and self.provider != "http":
            raise BrowserProviderUnavailableError(
                "browser provider capabilities response context mismatch: provider"
            )
        self._capabilities_cache = capabilities
        return capabilities

    def open_session(
        self,
        tenant_id: str,
        workspace_id: str,
        run_id: str,
        session_id: str,
        storage_state: dict[str, Any] | None = None,
        profile_id: str | None = None,
    ) -> BrowserSession:
        self._require_capabilities_for_open(
            tenant_id=tenant_id,
            run_id=run_id,
        )
        body = {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "run_id": run_id,
            "session_id": session_id,
            "storage_state": storage_state,
            "profile_id": profile_id,
        }
        response_body = self._request("POST", "/sessions", body, expected_statuses={200, 201})
        session = BrowserSession.model_validate(response_body)
        self._validate_session_context(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            run_id=run_id,
            session_id=session_id,
        )
        self._cache_session_context(session)
        return session

    def _require_capabilities_for_open(self, tenant_id: str, run_id: str) -> None:
        if not self.enforce_capabilities:
            return
        capabilities = self.capabilities()
        missing = []
        if not capabilities.auth_required:
            missing.append("auth_required")
        if not capabilities.session_ttl_enforced:
            missing.append("session_ttl_enforced")
        if capabilities.max_session_ttl_seconds <= 0:
            missing.append("max_session_ttl_seconds")
        if capabilities.max_sessions <= 0:
            missing.append("max_sessions")
        if capabilities.max_sessions_per_tenant <= 0:
            missing.append("max_sessions_per_tenant")
        if capabilities.max_sessions_per_run <= 0:
            missing.append("max_sessions_per_run")
        if missing:
            raise BrowserProviderUnavailableError(
                "browser controller capabilities are insufficient: "
                + ", ".join(missing)
            )
        sessions = self.list_sessions()
        if len(sessions) >= capabilities.max_sessions:
            raise BrowserProviderUnavailableError(
                "browser controller session capacity is full"
            )
        tenant_sessions = [
            session for session in sessions if session.tenant_id == tenant_id
        ]
        if len(tenant_sessions) >= capabilities.max_sessions_per_tenant:
            raise BrowserProviderUnavailableError(
                "browser controller tenant session capacity is full"
            )
        run_session_count = len(
            [session for session in tenant_sessions if session.run_id == run_id]
        )
        if run_session_count >= capabilities.max_sessions_per_run:
            raise BrowserProviderUnavailableError(
                "browser controller run session capacity is full"
            )

    def get_session(
        self,
        tenant_id: str,
        session_id: str,
        workspace_id: str | None = None,
        run_id: str | None = None,
    ) -> BrowserSession:
        workspace_id, run_id = self._resolve_session_scope(
            tenant_id=tenant_id,
            session_id=session_id,
            workspace_id=workspace_id,
            run_id=run_id,
        )
        query = urlencode(
            {
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "run_id": run_id,
            }
        )
        path = f"/sessions/{quote(session_id, safe='')}?{query}"
        response_body = self._request(
            "GET",
            path,
            None,
            expected_statuses={200},
            not_found_message=f"Browser session not found: {session_id}",
        )
        session = BrowserSession.model_validate(response_body)
        self._validate_session_context(
            session,
            tenant_id=tenant_id,
            session_id=session_id,
            workspace_id=workspace_id,
            run_id=run_id,
        )
        self._cache_session_context(session)
        return session

    def list_sessions(self, tenant_id: str | None = None) -> list[BrowserSession]:
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
            raise BrowserProviderUnavailableError(
                "browser provider session list response must include sessions"
            )
        validated_sessions = [
            BrowserSession.model_validate(session) for session in sessions
        ]
        for session in validated_sessions:
            self._cache_session_context(session)
        if tenant_id is not None:
            for session in validated_sessions:
                self._validate_session_context(session, tenant_id=tenant_id)
        return validated_sessions

    def delete_session(
        self,
        tenant_id: str,
        session_id: str,
        workspace_id: str | None = None,
        run_id: str | None = None,
    ) -> BrowserSession:
        workspace_id, run_id = self._resolve_session_scope(
            tenant_id=tenant_id,
            session_id=session_id,
            workspace_id=workspace_id,
            run_id=run_id,
        )
        query = urlencode(
            {
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "run_id": run_id,
            }
        )
        path = f"/sessions/{quote(session_id, safe='')}?{query}"
        response_body = self._request(
            "DELETE",
            path,
            None,
            expected_statuses={200, 204},
            not_found_message=f"Browser session not found: {session_id}",
        )
        if not response_body:
            raise BrowserProviderUnavailableError(
                "browser provider delete response must include session"
            )
        session = BrowserSession.model_validate(response_body)
        self._validate_session_context(
            session,
            tenant_id=tenant_id,
            session_id=session_id,
            workspace_id=workspace_id,
            run_id=run_id,
        )
        self._session_context_cache.pop((tenant_id, session_id), None)
        if not self._delete_confirmed(tenant_id, session_id):
            raise BrowserProviderUnavailableError(
                "browser provider did not confirm deleted session"
            )
        return session

    def export_session_state(
        self,
        tenant_id: str,
        session_id: str,
        workspace_id: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        workspace_id, run_id = self._resolve_session_scope(
            tenant_id=tenant_id,
            session_id=session_id,
            workspace_id=workspace_id,
            run_id=run_id,
        )
        query = urlencode(
            {"tenant_id": tenant_id, "workspace_id": workspace_id, "run_id": run_id}
        )
        response = self._request(
            "GET",
            f"/sessions/{quote(session_id, safe='')}/state?{query}",
            None,
            expected_statuses={200},
            not_found_message=f"Browser session not found: {session_id}",
        )
        state = response.get("storage_state", response)
        if not isinstance(state, dict):
            raise BrowserProviderUnavailableError("browser provider returned invalid session state")
        return state

    def _delete_confirmed(self, tenant_id: str, session_id: str) -> bool:
        sessions = self.list_sessions(tenant_id)
        return not any(session.session_id == session_id for session in sessions)

    def _cache_session_context(self, session: BrowserSession) -> None:
        self._session_context_cache[
            (session.tenant_id, session.session_id)
        ] = session

    def _resolve_session_scope(
        self,
        tenant_id: str,
        session_id: str,
        workspace_id: str | None = None,
        run_id: str | None = None,
    ) -> tuple[str, str]:
        if workspace_id is not None and run_id is not None:
            return workspace_id, run_id
        cached = self._session_context_cache.get((tenant_id, session_id))
        if cached is not None:
            if workspace_id is None:
                workspace_id = cached.workspace_id
            if run_id is None:
                run_id = cached.run_id
            if workspace_id is not None and run_id is not None:
                return workspace_id, run_id
        sessions = self.list_sessions(tenant_id)
        for session in sessions:
            if session.session_id != session_id:
                continue
            if workspace_id is None:
                workspace_id = session.workspace_id
            if run_id is None:
                run_id = session.run_id
            if workspace_id is not None and run_id is not None:
                return workspace_id, run_id
        raise NotFoundError(f"Browser session not found: {session_id}")

    def apply(self, action: BrowserAction) -> BrowserObservation:
        response_body = self._request(
            "POST",
            "/actions",
            action.model_dump(mode="json"),
            expected_statuses={200, 201},
            not_found_message=f"Browser session not found: {action.session_id}",
        )
        observation = self._observation_from_response(response_body)
        self._validate_observation_context(observation, action)
        return observation

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        expected_statuses: set[int],
        not_found_message: str | None = None,
    ) -> dict[str, Any]:
        if not self.base_url.strip():
            raise BrowserProviderUnavailableError("browser provider endpoint is not configured")
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
            with opener.open(request, timeout=self.timeout_seconds) as response:
                status_code = response.status
                response_body = self._load_json(response.read())
        except HTTPError as error:
            detail = error.read().decode("utf-8")
            if error.code == 404 and not_found_message is not None:
                raise NotFoundError(not_found_message) from error
            raise BrowserProviderUnavailableError(
                f"browser provider returned HTTP {error.code}: {detail}"
            ) from error
        except (URLError, TimeoutError) as error:
            raise BrowserProviderUnavailableError(f"browser provider request failed: {error}") from error

        if status_code not in expected_statuses:
            raise BrowserProviderUnavailableError(
                f"browser provider returned unexpected HTTP {status_code}"
            )
        return response_body

    def _load_json(self, raw_body: bytes) -> dict[str, Any]:
        if not raw_body:
            return {}
        try:
            parsed = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError as error:
            raise BrowserProviderUnavailableError("browser provider returned invalid JSON") from error
        if not isinstance(parsed, dict):
            raise BrowserProviderUnavailableError("browser provider response must be a JSON object")
        return parsed

    def _observation_from_response(self, body: dict[str, Any]) -> BrowserObservation:
        observation_body = dict(body)
        encoded_screenshot = observation_body.pop("screenshot_content_base64", None)
        if encoded_screenshot is not None:
            try:
                observation_body["screenshot_content"] = base64.b64decode(encoded_screenshot)
            except (Base64DecodeError, ValueError) as error:
                raise BrowserProviderUnavailableError(
                    "browser provider returned invalid screenshot content"
                ) from error
        return BrowserObservation.model_validate(observation_body)

    def _validate_session_context(
        self,
        session: BrowserSession,
        tenant_id: str,
        session_id: str | None = None,
        workspace_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        mismatches = []
        if session.tenant_id != tenant_id:
            mismatches.append("tenant_id")
        if session_id is not None and session.session_id != session_id:
            mismatches.append("session_id")
        if workspace_id is not None and session.workspace_id != workspace_id:
            mismatches.append("workspace_id")
        if run_id is not None and session.run_id != run_id:
            mismatches.append("run_id")
        if mismatches:
            raise BrowserProviderUnavailableError(
                "browser provider response context mismatch: "
                + ", ".join(mismatches)
            )

    def _validate_observation_context(
        self,
        observation: BrowserObservation,
        action: BrowserAction,
    ) -> None:
        mismatches = []
        if observation.tenant_id != action.tenant_id:
            mismatches.append("tenant_id")
        if observation.workspace_id != action.workspace_id:
            mismatches.append("workspace_id")
        if observation.run_id != action.run_id:
            mismatches.append("run_id")
        if observation.session_id != action.session_id:
            mismatches.append("session_id")
        if observation.action_type != action.action_type:
            mismatches.append("action_type")
        if mismatches:
            raise BrowserProviderUnavailableError(
                "browser provider response context mismatch: "
                + ", ".join(mismatches)
            )


class PlaywrightBrowserController(BrowserController):
    provider: str = "playwright"
    headless: bool = True
    navigation_wait_until: str = "domcontentloaded"
    playwright_factory: Any | None = Field(default=None, exclude=True, repr=False)

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    _runtime: Any = PrivateAttr(default=None)
    _browser: Any = PrivateAttr(default=None)
    _sessions: dict[str, BrowserSession] = PrivateAttr(default_factory=dict)
    _handles: dict[str, dict[str, Any]] = PrivateAttr(default_factory=dict)

    def open_session(
        self,
        tenant_id: str,
        workspace_id: str,
        run_id: str,
        session_id: str,
        storage_state: dict[str, Any] | None = None,
        profile_id: str | None = None,
    ) -> BrowserSession:
        if session_id in self._sessions:
            raise BrowserProviderUnavailableError(
                f"Browser session already exists: {session_id}"
            )
        browser = self._ensure_browser()
        try:
            context = browser.new_context(storage_state=storage_state) if storage_state else browser.new_context()
            page = context.new_page()
        except Exception as error:
            raise BrowserProviderUnavailableError(
                f"browser provider could not open a session: {error}"
            ) from error

        session = BrowserSession(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            run_id=run_id,
            session_id=session_id,
        )
        self._sessions[session_id] = session
        self._handles[session_id] = {"context": context, "page": page}
        return session

    def export_session_state(
        self,
        tenant_id: str,
        session_id: str,
        workspace_id: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        self.get_session(
            tenant_id, session_id, workspace_id=workspace_id, run_id=run_id
        )
        handle = self._handles.get(session_id)
        if handle is None:
            raise NotFoundError(f"Browser session not found: {session_id}")
        try:
            state = handle["context"].storage_state()
        except Exception as error:
            raise BrowserProviderUnavailableError(
                f"browser provider could not export session state: {error}"
            ) from error
        if not isinstance(state, dict):
            raise BrowserProviderUnavailableError("browser provider returned invalid session state")
        return state

    def get_session(
        self,
        tenant_id: str,
        session_id: str,
        workspace_id: str | None = None,
        run_id: str | None = None,
    ) -> BrowserSession:
        session = self._sessions.get(session_id)
        if session is None or session.tenant_id != tenant_id:
            raise NotFoundError(f"Browser session not found: {session_id}")
        if workspace_id is not None and session.workspace_id != workspace_id:
            raise NotFoundError(f"Browser session not found: {session_id}")
        if run_id is not None and session.run_id != run_id:
            raise NotFoundError(f"Browser session not found: {session_id}")
        return session

    def list_sessions(self, tenant_id: str | None = None) -> list[BrowserSession]:
        sessions = sorted(
            self._sessions.values(),
            key=lambda session: (session.created_at, session.session_id),
        )
        if tenant_id is None:
            return sessions
        return [session for session in sessions if session.tenant_id == tenant_id]

    def delete_session(
        self,
        tenant_id: str,
        session_id: str,
        workspace_id: str | None = None,
        run_id: str | None = None,
    ) -> BrowserSession:
        session = self.get_session(
            tenant_id=tenant_id,
            session_id=session_id,
            workspace_id=workspace_id,
            run_id=run_id,
        )
        handle = self._handles.pop(session_id, None)
        if handle is not None:
            context = handle.get("context")
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass
        self._sessions.pop(session_id, None)
        return session

    def _extract_text(self, page, selector: str) -> str | None:
        try:
            locator = page.locator(selector)
            tag_name = str(
                locator.evaluate("element => element.tagName.toLowerCase()") or ""
            ).lower()
            if tag_name in {"input", "textarea", "select"}:
                return locator.input_value()
        except Exception:
            pass
        return page.text_content(selector)

    def apply(self, action: BrowserAction) -> BrowserObservation:
        session = self.get_session(action.tenant_id, action.session_id)
        if session.workspace_id != action.workspace_id or session.run_id != action.run_id:
            raise NotFoundError(f"Browser session not found: {action.session_id}")

        handle = self._handles.get(action.session_id)
        if handle is None:
            raise NotFoundError(f"Browser session not found: {action.session_id}")

        page = handle["page"]
        current_url = session.current_url
        text = None
        screenshot_content = None
        screenshot_uri = None
        try:
            if action.action_type == BrowserActionType.NAVIGATE:
                url = self._require(action.url, "url")
                page.goto(url, wait_until=self.navigation_wait_until)
                current_url = url
            elif action.action_type == BrowserActionType.TYPE:
                page.fill(
                    self._require(action.selector, "selector"),
                    self._require(action.text, "text"),
                )
                current_url = self._page_url(page, current_url)
            elif action.action_type == BrowserActionType.CLICK:
                page.click(self._require(action.selector, "selector"))
                current_url = self._page_url(page, current_url)
            elif action.action_type == BrowserActionType.EXTRACT:
                text = self._extract_text(page, action.selector or "body")
                current_url = self._page_url(page, current_url)
            elif action.action_type == BrowserActionType.SCREENSHOT:
                screenshot_content = page.screenshot(type="png")
                screenshot_uri = (
                    f"browser://{action.tenant_id}/runs/{action.run_id}/sessions/"
                    f"{action.session_id}/screenshot.png"
                )
                current_url = self._page_url(page, current_url)
        except BrowserProviderUnavailableError:
            raise
        except Exception as error:
            raise BrowserProviderUnavailableError(f"browser action failed: {error}") from error

        updated_session = session.model_copy(
            update={
                "current_url": current_url,
                "actions": [*session.actions, action],
            }
        )
        self._sessions[action.session_id] = updated_session
        return BrowserObservation(
            tenant_id=action.tenant_id,
            workspace_id=action.workspace_id,
            run_id=action.run_id,
            session_id=action.session_id,
            action_type=action.action_type,
            current_url=current_url,
            text=text,
            screenshot_uri=screenshot_uri,
            screenshot_content=screenshot_content,
            metadata=action.metadata,
        )

    def close(self) -> None:
        for handle in list(self._handles.values()):
            context = handle.get("context")
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass
        self._handles.clear()
        self._sessions.clear()
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._runtime is not None:
            try:
                self._runtime.stop()
            except Exception:
                pass
            self._runtime = None

    def _ensure_browser(self) -> Any:
        if self._runtime is None:
            runtime_factory = self.playwright_factory or self._load_playwright
            try:
                runtime_candidate = runtime_factory()
                self._runtime = runtime_candidate.start()
            except BrowserProviderUnavailableError:
                raise
            except Exception as error:
                raise BrowserProviderUnavailableError(
                    f"browser provider could not start Playwright: {error}"
                ) from error
        if self._browser is None:
            try:
                self._browser = self._runtime.chromium.launch(headless=self.headless)
            except Exception as error:
                raise BrowserProviderUnavailableError(
                    f"browser provider could not launch Chromium: {error}"
                ) from error
        return self._browser

    def _load_playwright(self) -> Any:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise BrowserProviderUnavailableError("playwright is not installed") from error
        return sync_playwright()

    def _require(self, value: str | None, field_name: str) -> str:
        if value is None or not value.strip():
            raise BrowserProviderUnavailableError(f"browser action requires {field_name}")
        return value

    def _page_url(self, page: Any, fallback: str | None) -> str | None:
        page_url = getattr(page, "url", None)
        if isinstance(page_url, str) and page_url:
            return page_url
        return fallback
