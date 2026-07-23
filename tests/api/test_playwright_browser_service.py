import base64
from datetime import timedelta
from threading import current_thread

import pytest
from fastapi.testclient import TestClient
from pydantic import PrivateAttr, ValidationError

from taroai.domain import utc_now
from taroai.sandbox import BrowserAction, BrowserActionType
from taroai.sandbox.browser import PlaywrightBrowserController
from taroai.sandbox.playwright_service import (
    BrowserControllerServiceSettings,
    create_playwright_browser_app,
)
from taroai.store import NotFoundError


class RecordingPage:
    def __init__(self, calls: list[tuple]):
        self.calls = calls
        self.url = "about:blank"
        self.values: dict[str, str] = {}

    def goto(self, url: str, wait_until: str):
        self.calls.append(("goto", url, wait_until))
        self.url = url

    def fill(self, selector: str, text: str):
        self.calls.append(("fill", selector, text))
        self.values[selector] = text

    def click(self, selector: str):
        self.calls.append(("click", selector))

    def locator(self, selector: str):
        self.calls.append(("locator", selector))
        return RecordingLocator(selector, self.calls, self.values)

    def text_content(self, selector: str):
        self.calls.append(("text_content", selector))
        return "Account summary"

    def screenshot(self, type: str):
        self.calls.append(("screenshot", type))
        return b"browser-png"


class RecordingLocator:
    FORM_CONTROLS = {"#tenant-id", "#user-id", "#workspace-id"}

    def __init__(
        self,
        selector: str,
        calls: list[tuple],
        values: dict[str, str],
    ):
        self.selector = selector
        self.calls = calls
        self.values = values

    def evaluate(self, script: str):
        self.calls.append(("locator.evaluate", self.selector, script))
        if self.selector in self.FORM_CONTROLS:
            return "input"
        return "div"

    def input_value(self):
        self.calls.append(("locator.input_value", self.selector))
        return self.values.get(self.selector, "")


class RecordingContext:
    def __init__(self, calls: list[tuple]):
        self.calls = calls
        self.page = RecordingPage(calls)

    def new_page(self):
        self.calls.append(("new_page",))
        return self.page

    def close(self):
        self.calls.append(("context.close",))


class RecordingBrowser:
    def __init__(self, calls: list[tuple]):
        self.calls = calls
        self.context = RecordingContext(calls)

    def new_context(self, storage_state=None):
        self.calls.append(
            ("new_context", storage_state) if storage_state else ("new_context",)
        )
        return self.context

    def close(self):
        self.calls.append(("browser.close",))


class RecordingChromium:
    def __init__(self, calls: list[tuple]):
        self.calls = calls
        self.browser = RecordingBrowser(calls)

    def launch(self, headless: bool):
        self.calls.append(("launch", headless))
        return self.browser


class RecordingPlaywright:
    def __init__(self):
        self.calls: list[tuple] = []
        self.chromium = RecordingChromium(self.calls)

    def start(self):
        self.calls.append(("start",))
        return self

    def stop(self):
        self.calls.append(("stop",))


class RecordingPlaywrightFactory:
    def __init__(self):
        self.runtime = RecordingPlaywright()

    def __call__(self):
        return self.runtime


class ThreadRecordingController(PlaywrightBrowserController):
    _service_threads: list[str] = PrivateAttr(default_factory=list)

    def open_session(self, *args, **kwargs):
        self._service_threads.append(current_thread().name)
        return super().open_session(*args, **kwargs)

    def apply(self, action):
        self._service_threads.append(current_thread().name)
        return super().apply(action)


def browser_action(action_type: BrowserActionType, **updates) -> BrowserAction:
    return BrowserAction(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        run_id="run_1",
        session_id="browser_1",
        action_type=action_type,
        **updates,
    )


def test_browser_controller_service_settings_loads_from_env_with_pydantic(monkeypatch):
    monkeypatch.setenv(
        "TAROAI_BROWSER_CONTROLLER_API_KEY",
        "browser_controller_secret_2026_long_key",
    )
    monkeypatch.setenv("TAROAI_BROWSER_CONTROLLER_SESSION_TTL_SECONDS", "120")
    monkeypatch.setenv("TAROAI_BROWSER_CONTROLLER_MAX_SESSIONS", "7")
    monkeypatch.setenv("TAROAI_BROWSER_CONTROLLER_MAX_SESSIONS_PER_TENANT", "4")
    monkeypatch.setenv("TAROAI_BROWSER_CONTROLLER_MAX_SESSIONS_PER_RUN", "2")
    monkeypatch.setenv(
        "TAROAI_BROWSER_CONTROLLER_NAVIGATION_ALLOWED_HOSTS",
        '["app.example.com","*.trusted.internal"]',
    )

    settings = BrowserControllerServiceSettings()

    assert settings.api_key == "browser_controller_secret_2026_long_key"
    assert settings.session_ttl_seconds == 120
    assert settings.max_sessions == 7
    assert settings.max_sessions_per_tenant == 4
    assert settings.max_sessions_per_run == 2
    assert settings.navigation_allowed_hosts == [
        "app.example.com",
        "*.trusted.internal",
    ]


def test_browser_controller_service_settings_rejects_invalid_env(monkeypatch):
    monkeypatch.setenv("TAROAI_BROWSER_CONTROLLER_MAX_SESSIONS", "0")

    with pytest.raises(ValidationError):
        BrowserControllerServiceSettings()


def test_browser_controller_service_settings_rejects_short_api_key():
    with pytest.raises(ValidationError) as error:
        BrowserControllerServiceSettings(api_key="short_browser_secret")

    assert "api_key must be at least 32 characters when configured" in str(error.value)


def test_playwright_browser_controller_executes_page_actions():
    factory = RecordingPlaywrightFactory()
    controller = PlaywrightBrowserController(playwright_factory=factory)

    session = controller.open_session(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        run_id="run_1",
        session_id="browser_1",
    )
    navigation = controller.apply(
        browser_action(BrowserActionType.NAVIGATE, url="https://example.test/account")
    )
    controller.apply(
        browser_action(BrowserActionType.TYPE, selector="#search", text="quarterly revenue")
    )
    controller.apply(browser_action(BrowserActionType.CLICK, selector="button[type=submit]"))
    extracted = controller.apply(browser_action(BrowserActionType.EXTRACT, selector="#result"))
    screenshot = controller.apply(browser_action(BrowserActionType.SCREENSHOT))
    fetched = controller.get_session("tenant_acme", "browser_1")
    controller.close()

    assert session.session_id == "browser_1"
    assert navigation.current_url == "https://example.test/account"
    assert fetched.current_url == "https://example.test/account"
    assert extracted.text == "Account summary"
    assert screenshot.screenshot_uri == (
        "browser://tenant_acme/runs/run_1/sessions/browser_1/screenshot.png"
    )
    assert screenshot.screenshot_content == b"browser-png"
    assert factory.runtime.calls == [
        ("start",),
        ("launch", True),
        ("new_context",),
        ("new_page",),
        ("goto", "https://example.test/account", "domcontentloaded"),
        ("fill", "#search", "quarterly revenue"),
        ("click", "button[type=submit]"),
        ("locator", "#result"),
        ("locator.evaluate", "#result", "element => element.tagName.toLowerCase()"),
        ("text_content", "#result"),
        ("screenshot", "png"),
        ("context.close",),
        ("browser.close",),
        ("stop",),
    ]


def test_playwright_browser_controller_extracts_form_control_values():
    factory = RecordingPlaywrightFactory()
    controller = PlaywrightBrowserController(playwright_factory=factory)

    controller.open_session(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        run_id="run_1",
        session_id="browser_1",
    )
    controller.apply(
        browser_action(BrowserActionType.TYPE, selector="#tenant-id", text="tenant_acme")
    )
    extracted = controller.apply(
        browser_action(BrowserActionType.EXTRACT, selector="#tenant-id")
    )
    controller.close()

    assert extracted.text == "tenant_acme"
    assert ("locator.input_value", "#tenant-id") in factory.runtime.calls


def test_playwright_browser_service_matches_http_controller_contract():
    factory = RecordingPlaywrightFactory()
    controller = PlaywrightBrowserController(playwright_factory=factory)
    client = TestClient(create_playwright_browser_app(controller=controller))
    storage_state = {"cookies": [], "origins": []}

    session_response = client.post(
        "/sessions",
        json={
            "tenant_id": "tenant_acme",
            "workspace_id": "workspace_sales",
            "run_id": "run_1",
            "session_id": "browser_1",
            "storage_state": storage_state,
            "profile_id": "profile_1",
        },
    )
    action_response = client.post(
        "/actions",
        json=browser_action(BrowserActionType.SCREENSHOT).model_dump(mode="json"),
    )
    fetched_response = client.get(
        "/sessions/browser_1"
        "?tenant_id=tenant_acme&workspace_id=workspace_sales&run_id=run_1"
    )

    assert session_response.status_code == 201
    assert session_response.json()["session_id"] == "browser_1"
    assert action_response.status_code == 201
    assert action_response.json()["screenshot_content_base64"] == (
        base64.b64encode(b"browser-png").decode("ascii")
    )
    assert "screenshot_content" not in action_response.json()
    assert fetched_response.status_code == 200
    assert fetched_response.json()["session_id"] == "browser_1"
    assert ("new_context", storage_state) in factory.runtime.calls


def test_playwright_browser_service_keeps_sync_runtime_on_one_thread():
    controller = ThreadRecordingController(
        playwright_factory=RecordingPlaywrightFactory()
    )
    client = TestClient(create_playwright_browser_app(controller=controller))

    opened = client.post(
        "/sessions",
        json={
            "tenant_id": "tenant_acme",
            "workspace_id": "workspace_sales",
            "run_id": "run_1",
            "session_id": "browser_1",
        },
    )
    acted = client.post(
        "/actions",
        json=browser_action(BrowserActionType.SCREENSHOT).model_dump(mode="json"),
    )

    assert opened.status_code == 201
    assert acted.status_code == 201
    assert set(controller._service_threads) == {"taroai-playwright_0"}


def test_playwright_browser_service_rejects_duplicate_session_id_without_replacing_context():
    factory = RecordingPlaywrightFactory()
    controller = PlaywrightBrowserController(playwright_factory=factory)
    client = TestClient(create_playwright_browser_app(controller=controller))
    payload = {
        "tenant_id": "tenant_acme",
        "workspace_id": "workspace_sales",
        "run_id": "run_1",
        "session_id": "browser_1",
    }

    first = client.post("/sessions", json=payload)
    second = client.post("/sessions", json=payload | {"run_id": "run_2"})
    fetched = client.get(
        "/sessions/browser_1"
        "?tenant_id=tenant_acme&workspace_id=workspace_sales&run_id=run_1"
    )

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["detail"] == "Browser session already exists: browser_1"
    assert fetched.status_code == 200
    assert fetched.json()["run_id"] == "run_1"
    assert factory.runtime.calls.count(("new_context",)) == 1


def test_playwright_browser_service_lists_sessions_by_tenant():
    controller = PlaywrightBrowserController(playwright_factory=RecordingPlaywrightFactory())
    client = TestClient(create_playwright_browser_app(controller=controller))
    client.post(
        "/sessions",
        json={
            "tenant_id": "tenant_acme",
            "workspace_id": "workspace_sales",
            "run_id": "run_1",
            "session_id": "browser_1",
        },
    )
    client.post(
        "/sessions",
        json={
            "tenant_id": "tenant_other",
            "workspace_id": "workspace_other",
            "run_id": "run_2",
            "session_id": "browser_2",
        },
    )

    response = client.get("/sessions?tenant_id=tenant_acme")

    assert response.status_code == 200
    assert [session["session_id"] for session in response.json()["sessions"]] == [
        "browser_1"
    ]


def test_playwright_browser_service_lists_all_sessions_when_tenant_filter_omitted():
    controller = PlaywrightBrowserController(playwright_factory=RecordingPlaywrightFactory())
    client = TestClient(create_playwright_browser_app(controller=controller))
    client.post(
        "/sessions",
        json={
            "tenant_id": "tenant_acme",
            "workspace_id": "workspace_sales",
            "run_id": "run_1",
            "session_id": "browser_1",
        },
    )
    client.post(
        "/sessions",
        json={
            "tenant_id": "tenant_other",
            "workspace_id": "workspace_other",
            "run_id": "run_2",
            "session_id": "browser_2",
        },
    )

    response = client.get("/sessions")

    assert response.status_code == 200
    assert [session["session_id"] for session in response.json()["sessions"]] == [
        "browser_1",
        "browser_2",
    ]


def test_playwright_browser_service_enforces_configured_bearer_token():
    controller = PlaywrightBrowserController(playwright_factory=RecordingPlaywrightFactory())
    api_key = "browser_controller_secret_2026_long_key"
    client = TestClient(
        create_playwright_browser_app(
            controller=controller,
            settings=BrowserControllerServiceSettings(api_key=api_key),
        )
    )

    missing = client.post(
        "/sessions",
        json={
            "tenant_id": "tenant_acme",
            "workspace_id": "workspace_sales",
            "run_id": "run_1",
            "session_id": "browser_1",
        },
    )
    wrong = client.post(
        "/sessions",
        headers={"Authorization": "Bearer wrong_secret"},
        json={
            "tenant_id": "tenant_acme",
            "workspace_id": "workspace_sales",
            "run_id": "run_1",
            "session_id": "browser_1",
        },
    )
    authorized = client.post(
        "/sessions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "tenant_id": "tenant_acme",
            "workspace_id": "workspace_sales",
            "run_id": "run_1",
            "session_id": "browser_1",
        },
    )
    health = client.get("/healthz")

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert authorized.status_code == 201
    assert health.status_code == 200


def test_playwright_browser_service_reports_governed_capabilities():
    controller = PlaywrightBrowserController(playwright_factory=RecordingPlaywrightFactory())
    api_key = "browser_controller_secret_2026_long_key"
    client = TestClient(
        create_playwright_browser_app(
            controller=controller,
            settings=BrowserControllerServiceSettings(
                api_key=api_key,
                session_ttl_seconds=900,
                max_sessions=8,
                max_sessions_per_tenant=4,
                max_sessions_per_run=2,
                navigation_allowed_hosts=["app.example", "*.trusted.internal"],
            ),
        )
    )

    missing = client.get("/capabilities")
    response = client.get(
        "/capabilities",
        headers={"Authorization": f"Bearer {api_key}"},
    )

    assert missing.status_code == 401
    assert response.status_code == 200
    assert response.json() == {
        "provider": "playwright",
        "auth_required": True,
        "session_ttl_enforced": True,
        "max_session_ttl_seconds": 900,
        "max_sessions": 8,
        "max_sessions_per_tenant": 4,
        "max_sessions_per_run": 2,
        "navigation_allowlist_enforced": True,
        "navigation_allowed_host_count": 2,
    }


def test_playwright_browser_service_deletes_session_and_closes_context():
    factory = RecordingPlaywrightFactory()
    controller = PlaywrightBrowserController(playwright_factory=factory)
    client = TestClient(create_playwright_browser_app(controller=controller))

    client.post(
        "/sessions",
        json={
            "tenant_id": "tenant_acme",
            "workspace_id": "workspace_sales",
            "run_id": "run_1",
            "session_id": "browser_1",
        },
    )

    session_scope_query = (
        "?tenant_id=tenant_acme&workspace_id=workspace_sales&run_id=run_1"
    )
    deleted = client.delete(f"/sessions/browser_1{session_scope_query}")
    fetched = client.get(f"/sessions/browser_1{session_scope_query}")

    assert deleted.status_code == 200
    assert deleted.json()["tenant_id"] == "tenant_acme"
    assert deleted.json()["workspace_id"] == "workspace_sales"
    assert deleted.json()["run_id"] == "run_1"
    assert deleted.json()["session_id"] == "browser_1"
    assert fetched.status_code == 404
    assert ("context.close",) in factory.runtime.calls


def test_playwright_browser_service_requires_workspace_run_scope_for_session_read_and_delete():
    factory = RecordingPlaywrightFactory()
    controller = PlaywrightBrowserController(playwright_factory=factory)
    client = TestClient(create_playwright_browser_app(controller=controller))
    client.post(
        "/sessions",
        json={
            "tenant_id": "tenant_acme",
            "workspace_id": "workspace_sales",
            "run_id": "run_1",
            "session_id": "browser_1",
        },
    )

    missing_scope = client.get("/sessions/browser_1?tenant_id=tenant_acme")
    wrong_scope_read = client.get(
        "/sessions/browser_1"
        "?tenant_id=tenant_acme&workspace_id=workspace_finance&run_id=run_2"
    )
    wrong_scope_delete = client.delete(
        "/sessions/browser_1"
        "?tenant_id=tenant_acme&workspace_id=workspace_finance&run_id=run_2"
    )
    correct_scope = client.get(
        "/sessions/browser_1"
        "?tenant_id=tenant_acme&workspace_id=workspace_sales&run_id=run_1"
    )

    assert missing_scope.status_code == 422
    assert wrong_scope_read.status_code == 404
    assert wrong_scope_delete.status_code == 404
    assert correct_scope.status_code == 200
    assert correct_scope.json()["session_id"] == "browser_1"
    assert ("context.close",) not in factory.runtime.calls


def test_playwright_browser_service_cleans_expired_sessions_before_use():
    factory = RecordingPlaywrightFactory()
    controller = PlaywrightBrowserController(playwright_factory=factory)
    client = TestClient(
        create_playwright_browser_app(
            controller=controller,
            settings=BrowserControllerServiceSettings(session_ttl_seconds=60),
        )
    )
    client.post(
        "/sessions",
        json={
            "tenant_id": "tenant_acme",
            "workspace_id": "workspace_sales",
            "run_id": "run_1",
            "session_id": "browser_1",
        },
    )
    expired = controller.get_session("tenant_acme", "browser_1").model_copy(
        update={"created_at": utc_now() - timedelta(seconds=61)}
    )
    controller._sessions["browser_1"] = expired

    response = client.get(
        "/sessions/browser_1"
        "?tenant_id=tenant_acme&workspace_id=workspace_sales&run_id=run_1"
    )

    assert response.status_code == 404
    assert ("context.close",) in factory.runtime.calls


def test_playwright_browser_service_enforces_session_capacity_limits():
    controller = PlaywrightBrowserController(playwright_factory=RecordingPlaywrightFactory())
    client = TestClient(
        create_playwright_browser_app(
            controller=controller,
            settings=BrowserControllerServiceSettings(
                max_sessions=1,
                max_sessions_per_tenant=1,
                max_sessions_per_run=1,
            ),
        )
    )
    first = client.post(
        "/sessions",
        json={
            "tenant_id": "tenant_acme",
            "workspace_id": "workspace_sales",
            "run_id": "run_1",
            "session_id": "browser_1",
        },
    )
    second_same_run = client.post(
        "/sessions",
        json={
            "tenant_id": "tenant_acme",
            "workspace_id": "workspace_sales",
            "run_id": "run_1",
            "session_id": "browser_2",
        },
    )

    assert first.status_code == 201
    assert second_same_run.status_code == 429
    assert second_same_run.json()["detail"] == "browser session limit reached"


def test_playwright_browser_service_enforces_navigation_host_allowlist():
    factory = RecordingPlaywrightFactory()
    controller = PlaywrightBrowserController(playwright_factory=factory)
    client = TestClient(
        create_playwright_browser_app(
            controller=controller,
            settings=BrowserControllerServiceSettings(
                navigation_allowed_hosts=["allowed.example", "*.trusted.internal"],
            ),
        )
    )
    client.post(
        "/sessions",
        json={
            "tenant_id": "tenant_acme",
            "workspace_id": "workspace_sales",
            "run_id": "run_1",
            "session_id": "browser_1",
        },
    )

    allowed = client.post(
        "/actions",
        json=browser_action(
            BrowserActionType.NAVIGATE,
            url="https://allowed.example/account",
        ).model_dump(mode="json"),
    )
    wildcard_allowed = client.post(
        "/actions",
        json=browser_action(
            BrowserActionType.NAVIGATE,
            url="http://app.trusted.internal/dashboard",
        ).model_dump(mode="json"),
    )
    denied = client.post(
        "/actions",
        json=browser_action(
            BrowserActionType.NAVIGATE,
            url="http://metadata.internal/latest",
        ).model_dump(mode="json"),
    )

    assert allowed.status_code == 201
    assert wildcard_allowed.status_code == 201
    assert denied.status_code == 403
    assert denied.json()["detail"] == "browser navigation host is not allowed"
    assert ("goto", "http://metadata.internal/latest", "domcontentloaded") not in (
        factory.runtime.calls
    )


def test_playwright_browser_controller_requires_existing_session():
    controller = PlaywrightBrowserController(playwright_factory=RecordingPlaywrightFactory())

    with pytest.raises(NotFoundError):
        controller.apply(browser_action(BrowserActionType.SCREENSHOT))


def test_playwright_browser_service_returns_not_found_for_missing_session():
    controller = PlaywrightBrowserController(playwright_factory=RecordingPlaywrightFactory())
    client = TestClient(create_playwright_browser_app(controller=controller))

    response = client.post(
        "/actions",
        json=browser_action(BrowserActionType.SCREENSHOT).model_dump(mode="json"),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Browser session not found: browser_1"


def test_playwright_browser_service_rejects_cross_workspace_run_action_scope():
    factory = RecordingPlaywrightFactory()
    controller = PlaywrightBrowserController(playwright_factory=factory)
    client = TestClient(create_playwright_browser_app(controller=controller))
    client.post(
        "/sessions",
        json={
            "tenant_id": "tenant_acme",
            "workspace_id": "workspace_sales",
            "run_id": "run_1",
            "session_id": "browser_1",
        },
    )

    response = client.post(
        "/actions",
        json=BrowserAction(
            tenant_id="tenant_acme",
            workspace_id="workspace_finance",
            run_id="run_2",
            session_id="browser_1",
            action_type=BrowserActionType.NAVIGATE,
            url="https://example.test/finance",
        ).model_dump(mode="json"),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Browser session not found: browser_1"
    assert ("goto", "https://example.test/finance", "domcontentloaded") not in (
        factory.runtime.calls
    )
