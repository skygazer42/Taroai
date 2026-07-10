from pydantic import BaseModel

from taroai.config import Settings
from taroai.db import SqlControlPlaneRepository
from taroai.identity import InMemoryIdentityService, SqlIdentityService
from taroai.knowledge import InMemoryKnowledgeService, SqlKnowledgeService
from taroai.onboarding.models import (
    ReadinessCheckStatus,
    TenantReadinessCheck,
    TenantReadinessReport,
)
from taroai.skills import InMemorySkillRegistry, SqlSkillRegistry
from taroai.store import InMemoryControlPlaneStore
from taroai.workers import JobQueue


STARTER_SKILL_ID_PREFIX = "starter."


class TenantReadinessService(BaseModel):
    identity_service: InMemoryIdentityService | SqlIdentityService
    store: InMemoryControlPlaneStore | SqlControlPlaneRepository
    settings: Settings
    job_queue: JobQueue | None = None
    knowledge_service: InMemoryKnowledgeService | SqlKnowledgeService | None = None
    skill_registry: InMemorySkillRegistry | SqlSkillRegistry | None = None

    def check_tenant_readiness(self, tenant_id: str, user_id: str) -> TenantReadinessReport:
        checks = [
            self._owner_user_check(tenant_id, user_id),
            self._owner_roles_check(tenant_id, user_id),
            self._auth_mode_check(),
            self._quota_profile_check(),
            self._audit_read_check(tenant_id),
            self._billing_read_check(tenant_id),
            self._storage_check(),
            self._queue_check(),
            self._starter_skills_check(tenant_id),
            self._knowledge_spaces_check(tenant_id),
        ]
        blocking_checks = [
            check.name
            for check in checks
            if check.required and check.status == ReadinessCheckStatus.FAILED
        ]
        warnings = [
            check.name
            for check in checks
            if check.status == ReadinessCheckStatus.WARNING
        ]
        return TenantReadinessReport(
            tenant_id=tenant_id,
            user_id=user_id,
            ready=blocking_checks == [],
            blocking_checks=blocking_checks,
            warnings=warnings,
            checks=checks,
        )

    def _owner_user_check(self, tenant_id: str, user_id: str) -> TenantReadinessCheck:
        try:
            account = self.identity_service.get_user(tenant_id, user_id)
        except (LookupError, PermissionError) as error:
            return self._failed("owner_user", "owner user is missing or outside tenant", {"error": str(error)})
        if account.status != "active":
            return self._failed("owner_user", "owner user is not active", {"status": account.status})
        return self._passed("owner_user", "owner user exists and is active", {"email": account.email})

    def _owner_roles_check(self, tenant_id: str, user_id: str) -> TenantReadinessCheck:
        try:
            role_ids = self.identity_service.list_role_ids_for_user(tenant_id, user_id)
        except (LookupError, PermissionError) as error:
            return self._failed("owner_roles", "owner roles cannot be loaded", {"error": str(error)})
        if role_ids == []:
            return self._failed("owner_roles", "owner has no tenant role assignments", {"role_ids": []})
        return self._passed("owner_roles", "owner has tenant role assignments", {"role_ids": role_ids})

    def _auth_mode_check(self) -> TenantReadinessCheck:
        if self.settings.dev_request_headers_enabled:
            return self._failed(
                "auth_mode",
                "dev request headers are still enabled",
                {"dev_request_headers_enabled": True},
            )
        return self._passed(
            "auth_mode",
            "Bearer token authentication is required",
            {"dev_request_headers_enabled": False},
        )

    def _quota_profile_check(self) -> TenantReadinessCheck:
        return self._passed(
            "quota_profile",
            "tenant quota profile is configured",
            {"profile": self.settings.tenant_quota_profile},
        )

    def _audit_read_check(self, tenant_id: str) -> TenantReadinessCheck:
        try:
            events = self.store.list_audit_events(tenant_id)
        except Exception as error:
            return self._failed("audit_read", "audit events cannot be queried", {"error": str(error)})
        return self._passed("audit_read", "audit events can be queried", {"event_count": len(events)})

    def _billing_read_check(self, tenant_id: str) -> TenantReadinessCheck:
        try:
            meters = self.store.list_billing_meters(tenant_id)
        except Exception as error:
            return self._failed("billing_read", "billing meters cannot be queried", {"error": str(error)})
        return self._passed("billing_read", "billing meters can be queried", {"meter_count": len(meters)})

    def _storage_check(self) -> TenantReadinessCheck:
        return self._passed(
            "object_storage",
            "object storage bucket is configured",
            {
                "bucket": self.settings.object_storage_bucket,
                "catalog_backend": self.settings.storage_catalog_backend,
            },
        )

    def _queue_check(self) -> TenantReadinessCheck:
        if self.settings.run_execution_dispatch_mode == "queue" and self.job_queue is None:
            return self._failed("job_queue", "queued run execution requires a job queue")
        return self._passed(
            "job_queue",
            "run execution dispatch is available",
            {
                "dispatch_mode": self.settings.run_execution_dispatch_mode,
                "queue_backend": self.settings.job_queue_backend,
            },
        )

    def _starter_skills_check(self, tenant_id: str) -> TenantReadinessCheck:
        if self.skill_registry is None:
            return self._warning("starter_skills", "starter skill registry is not attached")
        try:
            entries = self.skill_registry.list_for_tenant(tenant_id)
            analytics = self.skill_registry.get_marketplace_analytics(tenant_id)
        except Exception as error:
            return self._warning("starter_skills", "starter skill registry cannot be queried", {"error": str(error)})
        starter_skill_ids = [
            entry.manifest.id
            for entry in entries
            if entry.manifest.id.startswith(STARTER_SKILL_ID_PREFIX)
        ]
        if starter_skill_ids == []:
            return self._warning("starter_skills", "starter skill packs are not seeded yet")
        return self._passed(
            "starter_skills",
            "starter skill packs are seeded",
            {
                "skill_ids": starter_skill_ids,
                "installation_count": analytics.total_installations,
            },
        )

    def _knowledge_spaces_check(self, tenant_id: str) -> TenantReadinessCheck:
        if self.knowledge_service is None:
            return self._warning("knowledge_spaces", "knowledge service is not attached")
        try:
            knowledge_bases = self.knowledge_service.list_bases_for_tenant(tenant_id)
        except Exception as error:
            return self._warning("knowledge_spaces", "knowledge spaces cannot be queried", {"error": str(error)})
        if knowledge_bases == []:
            return self._warning("knowledge_spaces", "starter knowledge spaces are not seeded yet")
        return self._passed(
            "knowledge_spaces",
            "starter knowledge spaces are seeded",
            {
                "knowledge_base_ids": [knowledge_base.id for knowledge_base in knowledge_bases],
                "workspace_ids": sorted({knowledge_base.workspace_id for knowledge_base in knowledge_bases}),
            },
        )

    def _passed(self, name: str, message: str, metadata: dict | None = None) -> TenantReadinessCheck:
        return TenantReadinessCheck(
            name=name,
            status=ReadinessCheckStatus.PASSED,
            message=message,
            metadata=metadata or {},
        )

    def _failed(self, name: str, message: str, metadata: dict | None = None) -> TenantReadinessCheck:
        return TenantReadinessCheck(
            name=name,
            status=ReadinessCheckStatus.FAILED,
            message=message,
            metadata=metadata or {},
        )

    def _warning(self, name: str, message: str, metadata: dict | None = None) -> TenantReadinessCheck:
        return TenantReadinessCheck(
            name=name,
            status=ReadinessCheckStatus.WARNING,
            required=False,
            message=message,
            metadata=metadata or {},
        )
