import json
from typing import Any
from urllib.parse import urlparse

from taroai.browser_profiles.models import (
    BrowserProfile,
    BrowserProfileCreate,
    BrowserProfilePatch,
    BrowserProfileSession,
)
from taroai.domain import new_id, utc_now
from taroai.sandbox import BrowserAction, BrowserActionType
from taroai.secrets import SecretRef, SecretScope


class BrowserProfileService:
    def __init__(self, *, registry: Any, secret_service: Any, browser_controller: Any) -> None:
        self.registry = registry
        self.secret_service = secret_service
        self.browser_controller = browser_controller

    def create(
        self,
        tenant_id: str,
        user_id: str,
        payload: BrowserProfileCreate,
    ) -> BrowserProfile:
        existing = self.registry.list_profiles(tenant_id, payload.workspace_id)
        profile = BrowserProfile(
            tenant_id=tenant_id,
            workspace_id=payload.workspace_id,
            name=payload.name,
            description=payload.description,
            allowed_domains=payload.allowed_domains,
            is_default=payload.is_default or not existing,
            created_by_user_id=user_id,
        )
        return self.registry.create_profile(profile)

    def update(
        self,
        tenant_id: str,
        profile_id: str,
        payload: BrowserProfilePatch,
    ) -> BrowserProfile:
        return self.registry.update_profile(
            tenant_id,
            profile_id,
            **payload.model_dump(exclude_none=True),
        )

    def list_profiles(self, tenant_id: str, workspace_id: str) -> list[BrowserProfile]:
        return self.registry.list_profiles(tenant_id, workspace_id)

    def get_profile(self, tenant_id: str, profile_id: str) -> BrowserProfile:
        return self.registry.get_profile(tenant_id, profile_id)

    def open_session(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        profile_id: str | None,
        run_id: str | None,
        user_id: str | None,
        start_url: str | None = None,
    ) -> BrowserProfileSession:
        profile = self._resolve_profile(tenant_id, workspace_id, profile_id)
        if profile is not None and profile.status != "active":
            raise ValueError("Browser profile is disabled")
        if start_url:
            self._assert_url_allowed(profile, start_url)
        session_id = new_id("browser")
        effective_run_id = run_id or new_id("browser_manual")
        state = self._load_state(profile, session_id=session_id, run_id=effective_run_id)
        session = self.browser_controller.open_session(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            run_id=effective_run_id,
            session_id=session_id,
            storage_state=state,
            profile_id=profile.id if profile else None,
        )
        record = BrowserProfileSession(
            session_id=session.session_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            profile_id=profile.id if profile else None,
            run_id=run_id,
            current_url=session.current_url,
            created_by_user_id=user_id,
        )
        self.registry.save_session(record)
        if start_url:
            self.apply_action(
                tenant_id=tenant_id,
                session_id=session.session_id,
                action_type=BrowserActionType.NAVIGATE,
                url=start_url,
            )
            record = self.registry.get_session(tenant_id, session.session_id)
        return record

    def apply_action(
        self,
        *,
        tenant_id: str,
        session_id: str,
        action_type: BrowserActionType,
        url: str | None = None,
        selector: str | None = None,
        text: str | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        record = self.registry.get_session(tenant_id, session_id)
        if record.status != "active":
            raise ValueError("Browser session is not active")
        profile = (
            self.registry.get_profile(tenant_id, record.profile_id)
            if record.profile_id
            else None
        )
        if action_type == BrowserActionType.NAVIGATE and url:
            self._assert_url_allowed(profile, url)
        provider_session = self.browser_controller.get_session(
            tenant_id,
            session_id,
            workspace_id=record.workspace_id,
        )
        observation = self.browser_controller.apply(
            BrowserAction(
                tenant_id=tenant_id,
                workspace_id=record.workspace_id,
                run_id=provider_session.run_id,
                session_id=session_id,
                action_type=action_type,
                url=url,
                selector=selector,
                text=text,
                metadata=metadata or {},
            )
        )
        self.registry.save_session(
            record.model_copy(
                update={
                    "current_url": observation.current_url,
                    "last_seen_at": utc_now(),
                }
            )
        )
        return observation

    def close_session(self, tenant_id: str, session_id: str) -> BrowserProfileSession:
        record = self.registry.get_session(tenant_id, session_id)
        if record.status != "active":
            return record
        provider_session = self.browser_controller.get_session(
            tenant_id,
            session_id,
            workspace_id=record.workspace_id,
        )
        if record.profile_id:
            profile = self.registry.get_profile(tenant_id, record.profile_id)
            state = self.browser_controller.export_session_state(
                tenant_id,
                session_id,
                workspace_id=record.workspace_id,
                run_id=provider_session.run_id,
            )
            self._save_state(profile, state)
        self.browser_controller.delete_session(
            tenant_id,
            session_id,
            workspace_id=record.workspace_id,
            run_id=provider_session.run_id,
        )
        now = utc_now()
        return self.registry.save_session(
            record.model_copy(
                update={
                    "status": "closed",
                    "current_url": provider_session.current_url,
                    "last_seen_at": now,
                    "closed_at": now,
                }
            )
        )

    def list_sessions(self, tenant_id: str, workspace_id: str) -> list[BrowserProfileSession]:
        return self.registry.list_sessions(tenant_id, workspace_id)

    def _resolve_profile(
        self,
        tenant_id: str,
        workspace_id: str,
        profile_id: str | None,
    ) -> BrowserProfile | None:
        if profile_id:
            profile = self.registry.get_profile(tenant_id, profile_id)
            if profile.workspace_id != workspace_id:
                raise ValueError("Browser profile is not in this workspace")
            return profile
        return next(
            (
                item
                for item in self.registry.list_profiles(tenant_id, workspace_id)
                if item.is_default and item.status == "active"
            ),
            None,
        )

    def _load_state(
        self,
        profile: BrowserProfile | None,
        *,
        session_id: str,
        run_id: str,
    ) -> dict[str, Any] | None:
        if profile is None or profile.secret_ref_id is None:
            return None
        self.secret_service.register_secret_ref(
            SecretRef(
                id=profile.secret_ref_id,
                tenant_id=profile.tenant_id,
                workspace_id=profile.workspace_id,
                name=f"Browser profile state: {profile.name}",
                scope=SecretScope(
                    tenant_id=profile.tenant_id,
                    workspace_id=profile.workspace_id,
                    allowed_tool_names=["browser.profile"],
                    actions=["read", "write"],
                ),
                backend=profile.secret_backend or "memory",
                external_name=profile.secret_external_name,
            )
        )
        lease = self.secret_service.create_lease(
            tenant_id=profile.tenant_id,
            workspace_id=profile.workspace_id,
            secret_id=profile.secret_ref_id,
            tool_name="browser.profile",
            actions=["read"],
            ttl_seconds=60,
            run_id=run_id,
            session_id=session_id,
        )
        value = self.secret_service.resolve_lease_value(
            tenant_id=profile.tenant_id,
            lease_token=lease.lease_token,
            workspace_id=profile.workspace_id,
            run_id=run_id,
            session_id=session_id,
            tool_name="browser.profile",
            action="read",
            require_bound_context=True,
        )
        state = json.loads(value)
        if not isinstance(state, dict):
            raise ValueError("Browser profile state is invalid")
        return state

    def _save_state(self, profile: BrowserProfile, state: dict[str, Any]) -> BrowserProfile:
        serialized = json.dumps(state, separators=(",", ":"), sort_keys=True)
        if profile.secret_ref_id is None:
            secret = self.secret_service.create_secret(
                tenant_id=profile.tenant_id,
                workspace_id=profile.workspace_id,
                name=f"Browser profile state: {profile.name}",
                value=serialized,
                scope=SecretScope(
                    tenant_id=profile.tenant_id,
                    workspace_id=profile.workspace_id,
                    allowed_tool_names=["browser.profile"],
                    actions=["read", "write"],
                ),
            )
            secret_ref_id = secret.id
        else:
            self.secret_service.rotate_secret_value(
                profile.tenant_id, profile.secret_ref_id, serialized
            )
            secret_ref_id = profile.secret_ref_id
        return self.registry.update_profile(
            profile.tenant_id,
            profile.id,
            secret_ref_id=secret_ref_id,
            secret_backend=(secret.backend if profile.secret_ref_id is None else profile.secret_backend),
            secret_external_name=(
                secret.external_name
                if profile.secret_ref_id is None
                else profile.secret_external_name
            ),
            revision=profile.revision + 1,
            last_used_at=utc_now(),
        )

    def _assert_url_allowed(
        self,
        profile: BrowserProfile | None,
        url: str,
    ) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Browser navigation requires an HTTP or HTTPS URL")
        if profile is None or not profile.allowed_domains:
            return
        hostname = parsed.hostname.lower().strip(".")
        if not any(
            hostname == domain or hostname.endswith(f".{domain}")
            for domain in profile.allowed_domains
        ):
            raise ValueError("Browser profile does not allow this domain")
