import argparse
import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from taroai.deployment.install_evidence import BrowserControllerVerificationResult
from taroai.errors import NotFoundError
from taroai.sandbox.browser import BrowserProviderUnavailableError, HttpBrowserController
from taroai.sandbox.models import BrowserAction, BrowserActionType, BrowserObservation


class BrowserControllerVerificationConfig(BaseModel):
    base_url: str = Field(default="http://localhost:8001", min_length=1)
    api_key: str = Field(default="", repr=False)
    tenant_id: str = Field(default="tenant_browser_verify", min_length=1)
    denied_tenant_id: str = Field(default="tenant_browser_verify_denied", min_length=1)
    workspace_id: str = Field(default="workspace_browser_verify", min_length=1)
    run_id: str = Field(default_factory=lambda: f"run_browser_verify_{uuid4().hex[:12]}")
    session_id: str = Field(
        default_factory=lambda: f"browser_verify_{uuid4().hex[:12]}",
        min_length=1,
    )
    timeout_seconds: int = Field(default=30, ge=1)

    @model_validator(mode="after")
    def validate_base_url(self) -> "BrowserControllerVerificationConfig":
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url must be an absolute HTTP URL")
        if self.denied_tenant_id == self.tenant_id:
            raise ValueError("denied_tenant_id must differ from tenant_id")
        return self


def parse_args(argv: list[str] | None = None) -> BrowserControllerVerificationConfig:
    parser = argparse.ArgumentParser(
        description="Verify a browser-controller service against its HTTP lifecycle API."
    )
    parser.add_argument("--base-url", default="http://localhost:8001")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("TAROAI_BROWSER_CONTROLLER_API_KEY", ""),
    )
    parser.add_argument("--tenant-id", default="tenant_browser_verify")
    parser.add_argument("--denied-tenant-id", default="tenant_browser_verify_denied")
    parser.add_argument("--workspace-id", default="workspace_browser_verify")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--timeout-seconds", type=int, default=30)
    parsed = parser.parse_args(argv)
    config_data: dict[str, Any] = {
        "base_url": parsed.base_url,
        "api_key": parsed.api_key,
        "tenant_id": parsed.tenant_id,
        "denied_tenant_id": parsed.denied_tenant_id,
        "workspace_id": parsed.workspace_id,
        "timeout_seconds": parsed.timeout_seconds,
    }
    if parsed.run_id is not None:
        config_data["run_id"] = parsed.run_id
    if parsed.session_id is not None:
        config_data["session_id"] = parsed.session_id
    return BrowserControllerVerificationConfig(**config_data)


def verify_browser_controller(
    config: BrowserControllerVerificationConfig,
    controller=None,
) -> BrowserControllerVerificationResult:
    browser_controller = controller or build_browser_controller(config)
    auth_challenge_evidence = inspect_browser_controller_auth_challenge(config)
    auth_challenge_enforced = all(auth_challenge_evidence.values())
    capabilities_evidence = inspect_browser_controller_capabilities(
        browser_controller
    )
    session_opened = False
    action_executed = False
    session_deleted = False
    session_delete_confirmed = False
    duplicate_session_rejected = False
    action_scope_enforced = False
    session_read_scope_enforced = False
    session_delete_scope_enforced = False
    session_listed = False
    tenant_session_scope_enforced = False
    screenshot_or_extract_verified = False
    screenshot_uri = None
    screenshot_content_length = 0
    extract_text_length = 0

    try:
        session = browser_controller.open_session(
            tenant_id=config.tenant_id,
            workspace_id=config.workspace_id,
            run_id=config.run_id,
            session_id=config.session_id,
        )
        session_opened = session.session_id == config.session_id
    except Exception:
        return browser_controller_verification_result(
            browser_controller,
            config,
            session_opened=session_opened,
            action_executed=action_executed,
            session_deleted=session_deleted,
            session_delete_confirmed=session_delete_confirmed,
            duplicate_session_rejected=duplicate_session_rejected,
            action_scope_enforced=action_scope_enforced,
            session_read_scope_enforced=session_read_scope_enforced,
            session_delete_scope_enforced=session_delete_scope_enforced,
            session_listed=session_listed,
            tenant_session_scope_enforced=tenant_session_scope_enforced,
            screenshot_or_extract_verified=screenshot_or_extract_verified,
            screenshot_uri=screenshot_uri,
            screenshot_content_length=screenshot_content_length,
            extract_text_length=extract_text_length,
            auth_challenge_enforced=auth_challenge_enforced,
            **auth_challenge_evidence,
            **capabilities_evidence,
        )

    try:
        browser_controller.open_session(
            tenant_id=config.tenant_id,
            workspace_id=config.workspace_id,
            run_id=config.run_id,
            session_id=config.session_id,
        )
        duplicate_session_rejected = False
    except Exception as error:
        duplicate_session_rejected = duplicate_session_rejection_confirmed(error)

    try:
        browser_controller.get_session(
            tenant_id=config.tenant_id,
            workspace_id=f"{config.workspace_id}_scope_probe",
            run_id=f"{config.run_id}_scope_probe",
            session_id=config.session_id,
        )
        session_read_scope_enforced = False
    except NotFoundError:
        session_read_scope_enforced = True
    except Exception:
        session_read_scope_enforced = False

    try:
        browser_controller.delete_session(
            tenant_id=config.tenant_id,
            workspace_id=f"{config.workspace_id}_scope_probe",
            run_id=f"{config.run_id}_scope_probe",
            session_id=config.session_id,
        )
        session_delete_scope_enforced = False
    except NotFoundError:
        session_delete_scope_enforced = browser_session_still_readable(
            browser_controller,
            tenant_id=config.tenant_id,
            session_id=config.session_id,
        )
    except Exception:
        session_delete_scope_enforced = False

    try:
        browser_controller.apply(
            BrowserAction(
                tenant_id=config.tenant_id,
                workspace_id=f"{config.workspace_id}_scope_probe",
                run_id=f"{config.run_id}_scope_probe",
                session_id=config.session_id,
                action_type=BrowserActionType.SCREENSHOT,
            )
        )
        action_scope_enforced = False
    except NotFoundError:
        action_scope_enforced = True
    except Exception:
        action_scope_enforced = False

    try:
        tenant_sessions = browser_controller.list_sessions(config.tenant_id)
        session_listed = any(
            session.session_id == config.session_id for session in tenant_sessions
        )
    except Exception:
        session_listed = False

    try:
        denied_sessions = browser_controller.list_sessions(config.denied_tenant_id)
        tenant_session_scope_enforced = not any(
            session.session_id == config.session_id for session in denied_sessions
        )
    except Exception:
        tenant_session_scope_enforced = False

    try:
        observation = browser_controller.apply(
            BrowserAction(
                tenant_id=config.tenant_id,
                workspace_id=config.workspace_id,
                run_id=config.run_id,
                session_id=config.session_id,
                action_type=BrowserActionType.SCREENSHOT,
            )
        )
        action_executed = observation.action_type == BrowserActionType.SCREENSHOT
        screenshot_uri = observation.screenshot_uri
        screenshot_content_length = len(observation.screenshot_content or b"")
        extract_text_length = len(observation.text or "")
        screenshot_or_extract_verified = browser_observation_capture_verified(
            observation
        )
    except Exception:
        pass

    try:
        browser_controller.delete_session(
            tenant_id=config.tenant_id,
            session_id=config.session_id,
        )
        session_deleted = True
    except Exception:
        pass
    if session_deleted:
        session_delete_confirmed = browser_session_delete_confirmed(
            browser_controller,
            config.tenant_id,
            config.session_id,
        )

    return browser_controller_verification_result(
        browser_controller,
        config,
        session_opened=session_opened,
        action_executed=action_executed,
        session_deleted=session_deleted,
        session_delete_confirmed=session_delete_confirmed,
        duplicate_session_rejected=duplicate_session_rejected,
        action_scope_enforced=action_scope_enforced,
        session_read_scope_enforced=session_read_scope_enforced,
        session_delete_scope_enforced=session_delete_scope_enforced,
        session_listed=session_listed,
        tenant_session_scope_enforced=tenant_session_scope_enforced,
        screenshot_or_extract_verified=screenshot_or_extract_verified,
        screenshot_uri=screenshot_uri,
        screenshot_content_length=screenshot_content_length,
        extract_text_length=extract_text_length,
        auth_challenge_enforced=auth_challenge_enforced,
        **auth_challenge_evidence,
        **capabilities_evidence,
    )


def browser_observation_capture_verified(observation: BrowserObservation) -> bool:
    if observation.action_type == BrowserActionType.SCREENSHOT:
        return bool(observation.screenshot_uri) and bool(observation.screenshot_content)
    if observation.action_type == BrowserActionType.EXTRACT:
        return bool((observation.text or "").strip())
    return False


def browser_session_delete_confirmed(
    browser_controller,
    tenant_id: str,
    session_id: str,
) -> bool:
    try:
        browser_controller.get_session(
            tenant_id=tenant_id,
            session_id=session_id,
        )
        return False
    except NotFoundError:
        pass
    except Exception:
        return False
    try:
        tenant_sessions = browser_controller.list_sessions(tenant_id)
    except Exception:
        return False
    return not any(session.session_id == session_id for session in tenant_sessions)


def browser_session_still_readable(
    browser_controller,
    tenant_id: str,
    session_id: str,
) -> bool:
    try:
        browser_controller.get_session(
            tenant_id=tenant_id,
            session_id=session_id,
        )
    except Exception:
        return False
    return True


def verify_browser_controller_auth_challenge(
    config: BrowserControllerVerificationConfig,
) -> bool:
    return all(inspect_browser_controller_auth_challenge(config).values())


def inspect_browser_controller_auth_challenge(
    config: BrowserControllerVerificationConfig,
) -> dict[str, bool]:
    if not config.api_key.strip():
        return {
            "auth_tenant_session_list_challenge_enforced": False,
            "auth_global_session_list_challenge_enforced": False,
            "auth_capabilities_challenge_enforced": False,
        }
    return {
        "auth_tenant_session_list_challenge_enforced": (
            browser_controller_unauthenticated_request_rejected(
                config,
                "/sessions?tenant_id=taroai_auth_probe",
            )
        ),
        "auth_global_session_list_challenge_enforced": (
            browser_controller_unauthenticated_request_rejected(
                config,
                "/sessions",
            )
        ),
        "auth_capabilities_challenge_enforced": (
            browser_controller_unauthenticated_request_rejected(
                config,
                "/capabilities",
            )
        ),
    }


def browser_controller_unauthenticated_request_rejected(
    config: BrowserControllerVerificationConfig,
    path: str,
) -> bool:
    request = Request(
        f"{config.base_url.rstrip('/')}{path}",
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open(request, timeout=config.timeout_seconds) as response:
            return response.status in {401, 403}
    except HTTPError as error:
        error.read()
        return error.code in {401, 403}
    except (TimeoutError, URLError):
        return False


def duplicate_session_rejection_confirmed(error: Exception) -> bool:
    if not isinstance(error, BrowserProviderUnavailableError):
        return False
    message = str(error).lower()
    return "already exists" in message or "http 409" in message


def inspect_browser_controller_capabilities(controller) -> dict[str, int | bool]:
    try:
        capabilities = controller.capabilities()
    except Exception:
        return {
            "capabilities_checked": False,
            "session_ttl_enforced_declared": False,
            "max_session_ttl_seconds_declared": False,
            "max_sessions_declared": False,
            "max_sessions_per_tenant_declared": False,
            "max_sessions_per_run_declared": False,
            "navigation_allowlist_enforced_declared": False,
            "navigation_allowed_host_count": 0,
        }
    return {
        "capabilities_checked": True,
        "session_ttl_enforced_declared": capabilities.session_ttl_enforced,
        "max_session_ttl_seconds_declared": capabilities.max_session_ttl_seconds > 0,
        "max_sessions_declared": capabilities.max_sessions > 0,
        "max_sessions_per_tenant_declared": capabilities.max_sessions_per_tenant > 0,
        "max_sessions_per_run_declared": capabilities.max_sessions_per_run > 0,
        "navigation_allowlist_enforced_declared": (
            capabilities.navigation_allowlist_enforced
        ),
        "navigation_allowed_host_count": capabilities.navigation_allowed_host_count,
    }


def browser_controller_verification_result(
    controller,
    config: BrowserControllerVerificationConfig,
    session_opened: bool,
    action_executed: bool,
    session_deleted: bool,
    session_delete_confirmed: bool,
    screenshot_or_extract_verified: bool,
    duplicate_session_rejected: bool = False,
    action_scope_enforced: bool = False,
    session_read_scope_enforced: bool = False,
    session_delete_scope_enforced: bool = False,
    session_listed: bool = False,
    tenant_session_scope_enforced: bool = False,
    screenshot_uri: str | None = None,
    screenshot_content_length: int = 0,
    extract_text_length: int = 0,
    auth_challenge_enforced: bool = False,
    auth_tenant_session_list_challenge_enforced: bool = False,
    auth_global_session_list_challenge_enforced: bool = False,
    auth_capabilities_challenge_enforced: bool = False,
    capabilities_checked: bool = False,
    session_ttl_enforced_declared: bool = False,
    max_session_ttl_seconds_declared: bool = False,
    max_sessions_declared: bool = False,
    max_sessions_per_tenant_declared: bool = False,
    max_sessions_per_run_declared: bool = False,
    navigation_allowlist_enforced_declared: bool = False,
    navigation_allowed_host_count: int = 0,
) -> BrowserControllerVerificationResult:
    return BrowserControllerVerificationResult(
        provider=str(getattr(controller, "provider", "browser_controller")),
        session_id=config.session_id,
        capabilities_checked=capabilities_checked,
        session_ttl_enforced_declared=session_ttl_enforced_declared,
        max_session_ttl_seconds_declared=max_session_ttl_seconds_declared,
        max_sessions_declared=max_sessions_declared,
        max_sessions_per_tenant_declared=max_sessions_per_tenant_declared,
        max_sessions_per_run_declared=max_sessions_per_run_declared,
        navigation_allowlist_enforced_declared=navigation_allowlist_enforced_declared,
        navigation_allowed_host_count=navigation_allowed_host_count,
        session_opened=session_opened,
        action_executed=action_executed,
        session_deleted=session_deleted,
        session_delete_confirmed=session_delete_confirmed,
        duplicate_session_rejected=duplicate_session_rejected,
        action_scope_enforced=action_scope_enforced,
        session_read_scope_enforced=session_read_scope_enforced,
        session_delete_scope_enforced=session_delete_scope_enforced,
        session_listed=session_listed,
        tenant_session_scope_enforced=tenant_session_scope_enforced,
        screenshot_or_extract_verified=screenshot_or_extract_verified,
        screenshot_uri=screenshot_uri,
        screenshot_content_length=screenshot_content_length,
        extract_text_length=extract_text_length,
        auth_challenge_enforced=auth_challenge_enforced,
        auth_tenant_session_list_challenge_enforced=(
            auth_tenant_session_list_challenge_enforced
        ),
        auth_global_session_list_challenge_enforced=(
            auth_global_session_list_challenge_enforced
        ),
        auth_capabilities_challenge_enforced=auth_capabilities_challenge_enforced,
        output_redacted=True,
    )


def build_browser_controller(
    config: BrowserControllerVerificationConfig,
) -> HttpBrowserController:
    return HttpBrowserController(
        provider="http",
        base_url=config.base_url,
        api_key=config.api_key,
        timeout_seconds=config.timeout_seconds,
    )


def browser_controller_verification_passed(
    result: BrowserControllerVerificationResult,
    auth_challenge_required: bool = False,
) -> bool:
    return (
        result.session_opened
        and result.action_executed
        and result.session_deleted
        and result.session_delete_confirmed
        and result.duplicate_session_rejected
        and result.action_scope_enforced
        and result.session_read_scope_enforced
        and result.session_delete_scope_enforced
        and result.session_listed
        and result.tenant_session_scope_enforced
        and result.capabilities_checked
        and result.session_ttl_enforced_declared
        and result.max_session_ttl_seconds_declared
        and result.max_sessions_declared
        and result.max_sessions_per_tenant_declared
        and result.max_sessions_per_run_declared
        and result.screenshot_or_extract_verified
        and browser_controller_capture_evidence_present(result)
        and (
            not auth_challenge_required
            or result.auth_challenge_enforced
        )
        and result.output_redacted
    )


def browser_controller_capture_evidence_present(
    result: BrowserControllerVerificationResult,
) -> bool:
    screenshot_evidence = (
        bool(result.screenshot_uri)
        and result.screenshot_content_length > 0
    )
    extract_evidence = result.extract_text_length > 0
    return screenshot_evidence or extract_evidence


def main(argv: list[str] | None = None) -> int:
    config = parse_args(argv)
    result = verify_browser_controller(config)
    print(json.dumps(result.model_dump(mode="json"), sort_keys=True))
    return 0 if browser_controller_verification_passed(
        result,
        auth_challenge_required=bool(config.api_key.strip()),
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
