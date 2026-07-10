from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from taroai.domain import new_id
from taroai.identity import UserAccountCreate
from taroai.scim.models import (
    ScimImportRequest,
    ScimImportResult,
    ScimProviderStatus,
    ScimUserResource,
)
from taroai.scim.registry import InMemoryScimProvisioningStore
from taroai.store import NotFoundError


class ScimProvisioningService(BaseModel):
    identity_service: Any
    store: Any = Field(default_factory=InMemoryScimProvisioningStore)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @staticmethod
    def empty_result(provider_id: str) -> ScimImportResult:
        return ScimImportResult(provider_id=provider_id)

    def apply_import(
        self,
        tenant_id: str,
        provider_id: str,
        imported_by_user_id: str,
        request: ScimImportRequest,
    ) -> ScimImportResult:
        provider_entry = self.store.get_provider(tenant_id, provider_id)
        if provider_entry.status != ScimProviderStatus.ENABLED:
            raise ValueError("SCIM provider must be enabled before importing users")

        result = self.empty_result(provider_id)
        group_ids_by_user = self._group_ids_by_user(request)
        mappings = {
            entry.mapping.group_external_id: entry.mapping.role_ids
            for entry in self.store.list_group_role_mappings(tenant_id, provider_id)
        }

        for user in request.users:
            result.users_seen += 1
            existing_link = self.store.find_user_link(
                tenant_id,
                provider_id,
                user.external_id,
            )
            if not user.active:
                if existing_link is not None:
                    self.identity_service.disable_user(tenant_id, existing_link.user_id)
                    self.store.upsert_user_link(
                        tenant_id=tenant_id,
                        provider_id=provider_id,
                        external_id=user.external_id,
                        user_id=existing_link.user_id,
                        email=existing_link.email,
                        active=False,
                    )
                    result.users_disabled += 1
                continue

            account, created = self._resolve_or_create_user(
                tenant_id,
                provider_id,
                user,
                existing_link,
                provider_entry.provider.jit_create_users,
            )
            if created:
                result.users_created += 1
            elif existing_link is None:
                result.users_linked += 1

            self.store.upsert_user_link(
                tenant_id=tenant_id,
                provider_id=provider_id,
                external_id=user.external_id,
                user_id=account.id,
                email=user.email_address(),
                active=True,
            )
            result.roles_assigned += self._assign_roles(
                tenant_id=tenant_id,
                user_id=account.id,
                role_ids=self._role_ids_for_user(
                    provider_entry.provider.default_role_ids,
                    group_ids_by_user.get(user.external_id, set()),
                    mappings,
                ),
            )

        self.store.record_import_result(
            tenant_id=tenant_id,
            provider_id=provider_id,
            imported_by_user_id=imported_by_user_id,
            result=result,
        )
        return result

    def _resolve_or_create_user(
        self,
        tenant_id: str,
        provider_id: str,
        user: ScimUserResource,
        existing_link,
        jit_create_users: bool,
    ):
        if existing_link is not None:
            return self.identity_service.get_user(tenant_id, existing_link.user_id), False

        email = user.email_address()
        try:
            return self.identity_service.get_user_by_email(tenant_id, email), False
        except NotFoundError:
            if not jit_create_users:
                raise ValueError(f"SCIM user is not linked and JIT creation is disabled: {email}")
            account = self.identity_service.create_user(
                UserAccountCreate(
                    tenant_id=tenant_id,
                    email=email,
                    display_name=user.resolved_display_name(),
                    password=new_id("scim_password"),
                )
            )
            return account, True

    def _assign_roles(self, tenant_id: str, user_id: str, role_ids: list[str]) -> int:
        existing_role_ids = set(self.identity_service.list_role_ids_for_user(tenant_id, user_id))
        assigned = 0
        for role_id in role_ids:
            if role_id in existing_role_ids:
                continue
            self.identity_service.assign_role(tenant_id, user_id, role_id)
            existing_role_ids.add(role_id)
            assigned += 1
        return assigned

    def _role_ids_for_user(
        self,
        default_role_ids: list[str],
        group_ids: set[str],
        mappings: dict[str, list[str]],
    ) -> list[str]:
        role_ids = list(default_role_ids)
        for group_id in group_ids:
            role_ids.extend(mappings.get(group_id, []))
        return list(dict.fromkeys(role_ids))

    def _group_ids_by_user(self, request: ScimImportRequest) -> dict[str, set[str]]:
        groups_by_user: dict[str, set[str]] = {}
        for user in request.users:
            groups_by_user.setdefault(user.external_id, set()).update(
                group.value for group in user.groups
            )
        for group in request.groups:
            for member in group.members:
                groups_by_user.setdefault(member.value, set()).add(group.external_id)
        return groups_by_user
