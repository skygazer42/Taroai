import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse

from pydantic import PrivateAttr, ValidationError
import pytest

from taroai.deployment.install_evidence import BrowserControllerVerificationResult
from taroai.errors import NotFoundError
from taroai.sandbox import browser_verification as browser_verification_module
from taroai.sandbox.browser import (
    BrowserController,
    BrowserControllerCapabilities,
    BrowserProviderUnavailableError,
)
from taroai.sandbox.browser_verification import (
    BrowserControllerVerificationConfig,
    browser_controller_verification_passed,
    main,
    verify_browser_controller,
)
from taroai.sandbox.models import (
    BrowserAction,
    BrowserActionType,
    BrowserObservation,
    BrowserSession,
)


class RecordingBrowserController(BrowserController):
    provider: str = "playwright"

    _sessions: dict[str, BrowserSession] = PrivateAttr(default_factory=dict)
    _calls: list[tuple] = PrivateAttr(default_factory=list)

    def __init__(self):
        super().__init__(provider="playwright")

    @property
    def sessions(self) -> dict[str, BrowserSession]:
        return self._sessions

    @property
    def calls(self) -> list[tuple]:
        return self._calls

    def capabilities(self) -> BrowserControllerCapabilities:
        return BrowserControllerCapabilities(
            provider="playwright",
            auth_required=False,
            session_ttl_enforced=True,
            max_session_ttl_seconds=1800,
            max_sessions=50,
            max_sessions_per_tenant=20,
            max_sessions_per_run=3,
            navigation_allowlist_enforced=True,
            navigation_allowed_host_count=2,
        )

    def open_session(
        self,
        tenant_id: str,
        workspace_id: str,
        run_id: str,
        session_id: str,
    ) -> BrowserSession:
        self._calls.append(("open_session", tenant_id, workspace_id, run_id, session_id))
        if session_id in self._sessions:
            raise BrowserProviderUnavailableError(
                f"Browser session already exists: {session_id}"
            )
        session = BrowserSession(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            run_id=run_id,
            session_id=session_id,
        )
        self._sessions[session_id] = session
        return session

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
        self._calls.append(("list_sessions", tenant_id))
        sessions = list(self._sessions.values())
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
        self._calls.append(("delete_session", tenant_id, session_id))
        session = self.get_session(
            tenant_id,
            session_id,
            workspace_id=workspace_id,
            run_id=run_id,
        )
        del self._sessions[session_id]
        return session

    def apply(self, action: BrowserAction) -> BrowserObservation:
        self._calls.append(("apply", action.action_type, action.session_id))
        session = self.get_session(action.tenant_id, action.session_id)
        if session.workspace_id != action.workspace_id or session.run_id != action.run_id:
            raise NotFoundError(f"Browser session not found: {action.session_id}")
        return BrowserObservation(
            tenant_id=action.tenant_id,
            workspace_id=action.workspace_id,
            run_id=action.run_id,
            session_id=action.session_id,
            action_type=BrowserActionType.SCREENSHOT,
            screenshot_uri=(
                f"browser://{action.tenant_id}/runs/{action.run_id}/"
                f"sessions/{action.session_id}/screenshot.png"
            ),
            screenshot_content=b"browser-png",
        )


class FailingBrowserActionController(RecordingBrowserController):
    def apply(self, action: BrowserAction) -> BrowserObservation:
        self._calls.append(("apply", action.action_type, action.session_id))
        raise BrowserProviderUnavailableError("browser action failed")

    def delete_session(
        self,
        tenant_id: str,
        session_id: str,
        workspace_id: str | None = None,
        run_id: str | None = None,
    ) -> BrowserSession:
        self._calls.append(("delete_session", tenant_id, session_id))
        raise BrowserProviderUnavailableError("browser delete failed")


class StickyDeleteBrowserController(RecordingBrowserController):
    def delete_session(
        self,
        tenant_id: str,
        session_id: str,
        workspace_id: str | None = None,
        run_id: str | None = None,
    ) -> BrowserSession:
        self._calls.append(("delete_session", tenant_id, session_id))
        return self.get_session(
            tenant_id,
            session_id,
            workspace_id=workspace_id,
            run_id=run_id,
        )


class ListResidualDeleteBrowserController(RecordingBrowserController):
    _deleted_session: BrowserSession | None = PrivateAttr(default=None)

    def delete_session(
        self,
        tenant_id: str,
        session_id: str,
        workspace_id: str | None = None,
        run_id: str | None = None,
    ) -> BrowserSession:
        self._calls.append(("delete_session", tenant_id, session_id))
        session = self.get_session(
            tenant_id,
            session_id,
            workspace_id=workspace_id,
            run_id=run_id,
        )
        self._deleted_session = session
        del self.sessions[session_id]
        return session

    def list_sessions(self, tenant_id: str | None = None) -> list[BrowserSession]:
        sessions = super().list_sessions(tenant_id)
        if (
            self._deleted_session is not None
            and (
                tenant_id is None
                or self._deleted_session.tenant_id == tenant_id
            )
        ):
            return sessions + [self._deleted_session]
        return sessions


class CrossTenantListBrowserController(RecordingBrowserController):
    def list_sessions(self, tenant_id: str | None = None) -> list[BrowserSession]:
        self._calls.append(("list_sessions", tenant_id))
        return list(self._sessions.values())


class DuplicateProbeUnavailableBrowserController(RecordingBrowserController):
    def open_session(
        self,
        tenant_id: str,
        workspace_id: str,
        run_id: str,
        session_id: str,
    ) -> BrowserSession:
        if session_id in self.sessions:
            self.calls.append(
                ("open_session", tenant_id, workspace_id, run_id, session_id)
            )
            raise BrowserProviderUnavailableError("browser provider temporarily unavailable")
        return super().open_session(tenant_id, workspace_id, run_id, session_id)


class ScopeProbeUnavailableBrowserController(RecordingBrowserController):
    def apply(self, action: BrowserAction) -> BrowserObservation:
        if action.workspace_id.endswith("_scope_probe"):
            self.calls.append(("apply", action.action_type, action.session_id))
            raise BrowserProviderUnavailableError("browser action failed before scope check")
        return super().apply(action)


def test_browser_controller_verification_generates_install_validation_result():
    controller = RecordingBrowserController()
    config = BrowserControllerVerificationConfig(
        base_url="http://browser.local",
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        run_id="run_1",
        session_id="browser_verify_1",
    )

    result = verify_browser_controller(config, controller=controller)

    assert result == BrowserControllerVerificationResult(
        provider="playwright",
        session_id="browser_verify_1",
        capabilities_checked=True,
        session_ttl_enforced_declared=True,
        max_session_ttl_seconds_declared=True,
        max_sessions_declared=True,
        max_sessions_per_tenant_declared=True,
        max_sessions_per_run_declared=True,
        navigation_allowlist_enforced_declared=True,
        navigation_allowed_host_count=2,
        session_opened=True,
        action_executed=True,
        session_deleted=True,
        session_delete_confirmed=True,
        duplicate_session_rejected=True,
        action_scope_enforced=True,
        session_read_scope_enforced=True,
        session_delete_scope_enforced=True,
        session_listed=True,
        tenant_session_scope_enforced=True,
        screenshot_or_extract_verified=True,
        screenshot_uri=(
            "browser://tenant_acme/runs/run_1/sessions/"
            "browser_verify_1/screenshot.png"
        ),
        screenshot_content_length=len(b"browser-png"),
        extract_text_length=0,
        auth_challenge_enforced=False,
        output_redacted=True,
    )
    assert result.session_read_scope_enforced is True
    assert result.session_delete_scope_enforced is True
    assert controller.calls == [
        ("open_session", "tenant_acme", "workspace_sales", "run_1", "browser_verify_1"),
        ("open_session", "tenant_acme", "workspace_sales", "run_1", "browser_verify_1"),
        ("delete_session", "tenant_acme", "browser_verify_1"),
        ("apply", BrowserActionType.SCREENSHOT, "browser_verify_1"),
        ("list_sessions", "tenant_acme"),
        ("list_sessions", "tenant_browser_verify_denied"),
        ("apply", BrowserActionType.SCREENSHOT, "browser_verify_1"),
        ("delete_session", "tenant_acme", "browser_verify_1"),
        ("list_sessions", "tenant_acme"),
    ]
    with pytest.raises(NotFoundError):
        controller.get_session("tenant_acme", "browser_verify_1")


def test_browser_controller_verification_reports_action_and_cleanup_failures():
    controller = FailingBrowserActionController()
    result = verify_browser_controller(
        BrowserControllerVerificationConfig(
            base_url="http://browser.local",
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
            session_id="browser_verify_1",
        ),
        controller=controller,
    )

    assert result.session_opened is True
    assert result.action_executed is False
    assert result.screenshot_or_extract_verified is False
    assert result.screenshot_uri is None
    assert result.screenshot_content_length == 0
    assert result.extract_text_length == 0
    assert result.session_deleted is False
    assert result.session_delete_confirmed is False
    assert result.session_listed is True
    assert result.tenant_session_scope_enforced is True
    assert result.output_redacted is True


def test_browser_controller_verification_fails_when_deleted_session_remains_readable():
    controller = StickyDeleteBrowserController()

    result = verify_browser_controller(
        BrowserControllerVerificationConfig(
            base_url="http://browser.local",
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
            session_id="browser_verify_1",
        ),
        controller=controller,
    )

    assert result.session_opened is True
    assert result.action_executed is True
    assert result.session_deleted is True
    assert result.session_delete_confirmed is False
    assert result.session_listed is True
    assert result.tenant_session_scope_enforced is True
    assert controller.get_session("tenant_acme", "browser_verify_1").session_id == (
        "browser_verify_1"
    )


def test_browser_controller_verification_fails_when_deleted_session_remains_listed():
    controller = ListResidualDeleteBrowserController()

    result = verify_browser_controller(
        BrowserControllerVerificationConfig(
            base_url="http://browser.local",
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
            session_id="browser_verify_1",
        ),
        controller=controller,
    )

    assert result.session_opened is True
    assert result.action_executed is True
    assert result.session_deleted is True
    assert result.session_delete_confirmed is False
    with pytest.raises(NotFoundError):
        controller.get_session("tenant_acme", "browser_verify_1")


def test_browser_controller_verification_fails_when_session_list_crosses_tenant_scope():
    controller = CrossTenantListBrowserController()

    result = verify_browser_controller(
        BrowserControllerVerificationConfig(
            base_url="http://browser.local",
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
            session_id="browser_verify_1",
        ),
        controller=controller,
    )

    assert result.session_opened is True
    assert result.session_listed is True
    assert result.tenant_session_scope_enforced is False
    assert browser_controller_verification_passed(result) is False


def test_browser_controller_verification_does_not_treat_provider_errors_as_scope_evidence():
    duplicate_result = verify_browser_controller(
        BrowserControllerVerificationConfig(
            base_url="http://browser.local",
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
            session_id="browser_verify_1",
        ),
        controller=DuplicateProbeUnavailableBrowserController(),
    )
    scope_result = verify_browser_controller(
        BrowserControllerVerificationConfig(
            base_url="http://browser.local",
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
            session_id="browser_verify_1",
        ),
        controller=ScopeProbeUnavailableBrowserController(),
    )

    assert duplicate_result.duplicate_session_rejected is False
    assert browser_controller_verification_passed(duplicate_result) is False
    assert scope_result.action_scope_enforced is False
    assert browser_controller_verification_passed(scope_result) is False


def test_browser_controller_verification_records_auth_challenge_when_api_key_configured(
    monkeypatch,
):
    controller = RecordingBrowserController()
    monkeypatch.setattr(
        browser_verification_module,
        "inspect_browser_controller_auth_challenge",
        lambda _config: {
            "auth_tenant_session_list_challenge_enforced": True,
            "auth_global_session_list_challenge_enforced": True,
            "auth_capabilities_challenge_enforced": True,
        },
        raising=False,
    )

    result = verify_browser_controller(
        BrowserControllerVerificationConfig(
            base_url="http://browser.local",
            api_key="browser_controller_secret_2026_long_key",
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
            session_id="browser_verify_1",
        ),
        controller=controller,
    )

    assert result.auth_challenge_enforced is True
    assert result.auth_tenant_session_list_challenge_enforced is True
    assert result.auth_global_session_list_challenge_enforced is True
    assert result.auth_capabilities_challenge_enforced is True
    assert browser_controller_verification_passed(result) is True


def test_browser_controller_verification_records_auth_challenge_probe_details(
    monkeypatch,
):
    controller = RecordingBrowserController()

    def reject_by_path(_config, path: str) -> bool:
        return path != "/sessions"

    monkeypatch.setattr(
        browser_verification_module,
        "browser_controller_unauthenticated_request_rejected",
        reject_by_path,
        raising=False,
    )

    result = verify_browser_controller(
        BrowserControllerVerificationConfig(
            base_url="http://browser.local",
            api_key="browser_controller_secret_2026_long_key",
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_1",
            session_id="browser_verify_1",
        ),
        controller=controller,
    )

    assert result.auth_challenge_enforced is False
    assert result.auth_tenant_session_list_challenge_enforced is True
    assert result.auth_global_session_list_challenge_enforced is False
    assert result.auth_capabilities_challenge_enforced is True


def test_browser_controller_auth_challenge_requires_capabilities_auth(monkeypatch):
    class BrowserResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _traceback):
            return False

    class RecordingOpener:
        def __init__(self):
            self.requests: list[str] = []

        def open(self, request, timeout: int):
            parsed = urlparse(request.full_url)
            request_path = parsed.path
            if parsed.query:
                request_path = f"{request_path}?{parsed.query}"
            self.requests.append(request_path)
            if request_path in {
                "/sessions?tenant_id=taroai_auth_probe",
                "/sessions",
            }:
                raise HTTPError(
                    request.full_url,
                    401,
                    "Unauthorized",
                    {},
                    None,
                )
            return BrowserResponse()

    opener = RecordingOpener()
    monkeypatch.setattr(
        browser_verification_module,
        "build_opener",
        lambda _proxy_handler: opener,
    )

    result = browser_verification_module.verify_browser_controller_auth_challenge(
        BrowserControllerVerificationConfig(
            base_url="http://browser.local",
            api_key="browser_controller_secret_2026_long_key",
        )
    )

    assert result is False
    assert opener.requests == [
        "/sessions?tenant_id=taroai_auth_probe",
        "/sessions",
        "/capabilities",
    ]


def test_browser_controller_auth_challenge_requires_global_session_list_auth(monkeypatch):
    class BrowserResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _traceback):
            return False

    class RecordingOpener:
        def __init__(self):
            self.requests: list[str] = []

        def open(self, request, timeout: int):
            parsed = urlparse(request.full_url)
            request_path = parsed.path
            if parsed.query:
                request_path = f"{request_path}?{parsed.query}"
            self.requests.append(request_path)
            if request_path in {
                "/sessions?tenant_id=taroai_auth_probe",
                "/capabilities",
            }:
                raise HTTPError(
                    request.full_url,
                    401,
                    "Unauthorized",
                    {},
                    None,
                )
            return BrowserResponse()

    opener = RecordingOpener()
    monkeypatch.setattr(
        browser_verification_module,
        "build_opener",
        lambda _proxy_handler: opener,
    )

    result = browser_verification_module.verify_browser_controller_auth_challenge(
        BrowserControllerVerificationConfig(
            base_url="http://browser.local",
            api_key="browser_controller_secret_2026_long_key",
        )
    )

    assert result is False
    assert opener.requests == [
        "/sessions?tenant_id=taroai_auth_probe",
        "/sessions",
        "/capabilities",
    ]


def test_browser_controller_verification_config_rejects_invalid_url():
    with pytest.raises(ValidationError):
        BrowserControllerVerificationConfig(base_url="browser.local")


def test_browser_controller_verification_main_prints_redacted_json(
    capsys,
    monkeypatch,
):
    controller = RecordingBrowserController()

    def build_controller(_config: BrowserControllerVerificationConfig):
        return controller

    monkeypatch.setattr(
        "taroai.sandbox.browser_verification.build_browser_controller",
        build_controller,
    )

    exit_code = main(
        [
            "--base-url",
            "http://browser.local",
            "--tenant-id",
            "tenant_acme",
            "--workspace-id",
            "workspace_sales",
            "--run-id",
            "run_1",
            "--session-id",
            "browser_verify_1",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload == {
        "action_executed": True,
        "action_scope_enforced": True,
        "auth_capabilities_challenge_enforced": False,
        "capabilities_checked": True,
        "duplicate_session_rejected": True,
        "extract_text_length": 0,
        "auth_challenge_enforced": False,
        "auth_global_session_list_challenge_enforced": False,
        "auth_tenant_session_list_challenge_enforced": False,
        "max_session_ttl_seconds_declared": True,
        "max_sessions_declared": True,
        "max_sessions_per_run_declared": True,
        "max_sessions_per_tenant_declared": True,
        "navigation_allowed_host_count": 2,
        "navigation_allowlist_enforced_declared": True,
        "output_redacted": True,
        "provider": "playwright",
        "screenshot_content_length": len(b"browser-png"),
        "screenshot_or_extract_verified": True,
        "screenshot_uri": (
            "browser://tenant_acme/runs/run_1/sessions/"
            "browser_verify_1/screenshot.png"
        ),
        "session_delete_confirmed": True,
        "session_delete_scope_enforced": True,
        "session_deleted": True,
        "session_id": "browser_verify_1",
        "session_listed": True,
        "session_opened": True,
        "session_read_scope_enforced": True,
        "session_ttl_enforced_declared": True,
        "tenant_session_scope_enforced": True,
    }


def test_browser_controller_verification_main_fails_when_api_key_auth_challenge_is_missing(
    capsys,
    monkeypatch,
):
    controller = RecordingBrowserController()

    def build_controller(_config: BrowserControllerVerificationConfig):
        return controller

    monkeypatch.setattr(
        "taroai.sandbox.browser_verification.build_browser_controller",
        build_controller,
    )
    monkeypatch.setattr(
        browser_verification_module,
        "verify_browser_controller_auth_challenge",
        lambda _config: False,
    )

    exit_code = main(
        [
            "--base-url",
            "http://browser.local",
            "--api-key",
            "browser_controller_secret_2026_long_key",
            "--tenant-id",
            "tenant_acme",
            "--workspace-id",
            "workspace_sales",
            "--run-id",
            "run_1",
            "--session-id",
            "browser_verify_1",
        ]
    )

    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["auth_challenge_enforced"] is False


def test_verify_browser_controller_script_wraps_python_cli():
    script = Path("scripts/verify-browser-controller.sh")

    text = script.read_text()

    assert "python -m taroai.sandbox.browser_verification" in text
    assert "--base-url" in text
    assert "--api-key" in text
    assert "TAROAI_BROWSER_CONTROLLER_API_KEY" in text
