from pydantic import BaseModel, Field

from taroai.domain import utc_now
from taroai.sso.models import SsoProvider, SsoProviderCreate, SsoProviderEntry, SsoProviderStatus
from taroai.store import NotFoundError


class InMemorySsoProviderRegistry(BaseModel):
    entries: dict[str, SsoProviderEntry] = Field(default_factory=dict)

    def create_or_update(
        self,
        tenant_id: str,
        created_by_user_id: str,
        request: SsoProviderCreate,
    ) -> SsoProviderEntry:
        key = self._tenant_provider_key(tenant_id, request.id)
        existing = self.entries.get(key)
        now = utc_now()
        entry = SsoProviderEntry(
            tenant_id=tenant_id,
            provider=SsoProvider.model_validate(request.model_dump(mode="json")),
            status=existing.status if existing is not None else SsoProviderStatus.DRAFT,
            created_by_user_id=(
                existing.created_by_user_id if existing is not None else created_by_user_id
            ),
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
        )
        self.entries[key] = entry
        return entry

    def get_for_tenant(self, tenant_id: str, provider_id: str) -> SsoProviderEntry:
        entry = self.entries.get(self._tenant_provider_key(tenant_id, provider_id))
        if entry is None:
            raise NotFoundError(f"SSO provider not found: {provider_id}")
        return entry

    def list_for_tenant(self, tenant_id: str) -> list[SsoProviderEntry]:
        return [
            entry
            for entry in self.entries.values()
            if entry.tenant_id == tenant_id
        ]

    def enable(self, tenant_id: str, provider_id: str) -> SsoProviderEntry:
        return self._update_status(tenant_id, provider_id, SsoProviderStatus.ENABLED)

    def disable(self, tenant_id: str, provider_id: str) -> SsoProviderEntry:
        return self._update_status(tenant_id, provider_id, SsoProviderStatus.DISABLED)

    def find_enabled_for_email(self, tenant_id: str, email: str) -> SsoProviderEntry | None:
        domain = self._email_domain(email)
        if domain is None:
            return None
        for entry in self.list_for_tenant(tenant_id):
            if entry.status == SsoProviderStatus.ENABLED and domain in entry.provider.domains:
                return entry
        return None

    def _update_status(
        self,
        tenant_id: str,
        provider_id: str,
        status: SsoProviderStatus,
    ) -> SsoProviderEntry:
        entry = self.get_for_tenant(tenant_id, provider_id)
        updated = entry.model_copy(update={"status": status, "updated_at": utc_now()})
        self.entries[self._tenant_provider_key(tenant_id, provider_id)] = updated
        return updated

    def _tenant_provider_key(self, tenant_id: str, provider_id: str) -> str:
        return f"{tenant_id}:{provider_id}"

    def _email_domain(self, email: str) -> str | None:
        if "@" not in email:
            return None
        domain = email.rsplit("@", 1)[1].strip().lower()
        return domain or None
