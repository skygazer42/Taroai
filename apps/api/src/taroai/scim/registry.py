from pydantic import BaseModel, Field

from taroai.domain import utc_now
from taroai.scim.models import (
    ScimGroupRoleMapping,
    ScimGroupRoleMappingEntry,
    ScimImportRecord,
    ScimImportResult,
    ScimProvider,
    ScimProviderCreate,
    ScimProviderEntry,
    ScimProviderStatus,
    ScimUserLink,
)
from taroai.store import NotFoundError


class InMemoryScimProvisioningStore(BaseModel):
    providers: dict[str, ScimProviderEntry] = Field(default_factory=dict)
    group_role_mappings: dict[str, ScimGroupRoleMappingEntry] = Field(default_factory=dict)
    user_links: dict[str, ScimUserLink] = Field(default_factory=dict)
    import_records: dict[str, list[ScimImportRecord]] = Field(default_factory=dict)

    def create_or_update_provider(
        self,
        tenant_id: str,
        created_by_user_id: str,
        request: ScimProviderCreate,
    ) -> ScimProviderEntry:
        key = self._tenant_provider_key(tenant_id, request.id)
        existing = self.providers.get(key)
        now = utc_now()
        entry = ScimProviderEntry(
            tenant_id=tenant_id,
            provider=ScimProvider.model_validate(request.model_dump(mode="json")),
            status=existing.status if existing is not None else ScimProviderStatus.DRAFT,
            created_by_user_id=(
                existing.created_by_user_id if existing is not None else created_by_user_id
            ),
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
        )
        self.providers[key] = entry
        return entry

    def get_provider(self, tenant_id: str, provider_id: str) -> ScimProviderEntry:
        entry = self.providers.get(self._tenant_provider_key(tenant_id, provider_id))
        if entry is None:
            raise NotFoundError(f"SCIM provider not found: {provider_id}")
        return entry

    def list_providers(self, tenant_id: str) -> list[ScimProviderEntry]:
        return [
            entry
            for entry in self.providers.values()
            if entry.tenant_id == tenant_id
        ]

    def enable_provider(self, tenant_id: str, provider_id: str) -> ScimProviderEntry:
        return self._update_provider_status(tenant_id, provider_id, ScimProviderStatus.ENABLED)

    def disable_provider(self, tenant_id: str, provider_id: str) -> ScimProviderEntry:
        return self._update_provider_status(tenant_id, provider_id, ScimProviderStatus.DISABLED)

    def upsert_group_role_mapping(
        self,
        tenant_id: str,
        provider_id: str,
        created_by_user_id: str,
        mapping: ScimGroupRoleMapping,
    ) -> ScimGroupRoleMappingEntry:
        self.get_provider(tenant_id, provider_id)
        key = self._tenant_group_key(tenant_id, provider_id, mapping.group_external_id)
        existing = self.group_role_mappings.get(key)
        now = utc_now()
        entry = ScimGroupRoleMappingEntry(
            tenant_id=tenant_id,
            provider_id=provider_id,
            mapping=mapping,
            created_by_user_id=(
                existing.created_by_user_id if existing is not None else created_by_user_id
            ),
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
        )
        self.group_role_mappings[key] = entry
        return entry

    def list_group_role_mappings(
        self,
        tenant_id: str,
        provider_id: str,
    ) -> list[ScimGroupRoleMappingEntry]:
        return [
            entry
            for entry in self.group_role_mappings.values()
            if entry.tenant_id == tenant_id and entry.provider_id == provider_id
        ]

    def upsert_user_link(
        self,
        tenant_id: str,
        provider_id: str,
        external_id: str,
        user_id: str,
        email: str,
        active: bool,
    ) -> ScimUserLink:
        key = self._tenant_user_key(tenant_id, provider_id, external_id)
        existing = self.user_links.get(key)
        now = utc_now()
        link = ScimUserLink(
            tenant_id=tenant_id,
            provider_id=provider_id,
            external_id=external_id,
            user_id=user_id,
            email=email,
            active=active,
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
        )
        self.user_links[key] = link
        return link

    def get_user_link(
        self,
        tenant_id: str,
        provider_id: str,
        external_id: str,
    ) -> ScimUserLink:
        link = self.find_user_link(tenant_id, provider_id, external_id)
        if link is None:
            raise NotFoundError(f"SCIM user link not found: {external_id}")
        return link

    def find_user_link(
        self,
        tenant_id: str,
        provider_id: str,
        external_id: str,
    ) -> ScimUserLink | None:
        return self.user_links.get(self._tenant_user_key(tenant_id, provider_id, external_id))

    def record_import_result(
        self,
        tenant_id: str,
        provider_id: str,
        imported_by_user_id: str,
        result: ScimImportResult,
    ) -> ScimImportRecord:
        record = ScimImportRecord(
            **result.model_dump(mode="json"),
            tenant_id=tenant_id,
            imported_by_user_id=imported_by_user_id,
        )
        self.import_records.setdefault(
            self._tenant_provider_key(tenant_id, provider_id),
            [],
        ).append(record)
        return record

    def list_import_records(self, tenant_id: str, provider_id: str) -> list[ScimImportRecord]:
        return [
            record
            for record in self.import_records.get(
                self._tenant_provider_key(tenant_id, provider_id),
                [],
            )
            if record.tenant_id == tenant_id
        ]

    def _update_provider_status(
        self,
        tenant_id: str,
        provider_id: str,
        status: ScimProviderStatus,
    ) -> ScimProviderEntry:
        entry = self.get_provider(tenant_id, provider_id)
        updated = entry.model_copy(update={"status": status, "updated_at": utc_now()})
        self.providers[self._tenant_provider_key(tenant_id, provider_id)] = updated
        return updated

    def _tenant_provider_key(self, tenant_id: str, provider_id: str) -> str:
        return f"{tenant_id}:{provider_id}"

    def _tenant_group_key(self, tenant_id: str, provider_id: str, group_external_id: str) -> str:
        return f"{tenant_id}:{provider_id}:{group_external_id}"

    def _tenant_user_key(self, tenant_id: str, provider_id: str, external_id: str) -> str:
        return f"{tenant_id}:{provider_id}:{external_id}"
