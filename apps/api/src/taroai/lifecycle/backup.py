from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from taroai.config import Settings
from taroai.domain import new_id, utc_now


class BackupComponentType(str, Enum):
    DATABASE = "database"
    OBJECT_STORAGE = "object_storage"
    REDIS = "redis"
    CONFIG = "config"


class BackupManifestRequest(BaseModel):
    tenant_id: str = Field(min_length=1)
    requested_by_user_id: str = Field(min_length=1)


class BackupVerificationCheck(BaseModel):
    name: str
    target: str
    expected_result: str


class BackupManifestComponent(BaseModel):
    type: BackupComponentType
    name: str
    backend: str
    location_ref: str
    restore_order: int = Field(ge=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    verification_checks: list[BackupVerificationCheck] = Field(default_factory=list)


class BackupManifest(BaseModel):
    id: str = Field(default_factory=lambda: new_id("backup_manifest"))
    tenant_id: str
    requested_by_user_id: str
    environment: str
    components: list[BackupManifestComponent]
    restore_order: list[str]
    created_at: datetime = Field(default_factory=utc_now)


class BackupManifestService(BaseModel):
    settings: Settings

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def create_manifest(self, request: BackupManifestRequest) -> BackupManifest:
        components = [
            self._database_component(),
            self._object_storage_component(),
        ]
        if self._uses_redis():
            components.append(self._redis_component())
        components.append(self._config_component())
        return BackupManifest(
            tenant_id=request.tenant_id,
            requested_by_user_id=request.requested_by_user_id,
            environment=self.settings.environment,
            components=components,
            restore_order=self._restore_order(components),
        )

    def _database_component(self) -> BackupManifestComponent:
        return BackupManifestComponent(
            type=BackupComponentType.DATABASE,
            name="control_plane_database",
            backend=self._database_backend(),
            location_ref="env:TAROAI_DATABASE_URL",
            restore_order=1,
            metadata={
                "store_backend": self.settings.control_plane_store_backend,
                "identity_backend": self.settings.identity_service_backend,
                "knowledge_backend": self.settings.knowledge_service_backend,
                "storage_catalog_backend": self.settings.storage_catalog_backend,
                "lifecycle_policy_backend": self.settings.lifecycle_policy_backend,
            },
            verification_checks=[
                BackupVerificationCheck(
                    name="schema_migrations_present",
                    target="schema_migrations",
                    expected_result="migration history can be queried",
                ),
                BackupVerificationCheck(
                    name="tenant_rows_present",
                    target="tenants",
                    expected_result="tenant rows restore before dependent records",
                ),
            ],
        )

    def _object_storage_component(self) -> BackupManifestComponent:
        return BackupManifestComponent(
            type=BackupComponentType.OBJECT_STORAGE,
            name="object_storage_bucket",
            backend="s3_compatible",
            location_ref="env:TAROAI_OBJECT_STORAGE_BUCKET",
            restore_order=2,
            metadata={
                "bucket": self.settings.object_storage_bucket,
                "region": self.settings.object_storage_region,
                "endpoint_configured": bool(self.settings.object_storage_endpoint),
            },
            verification_checks=[
                BackupVerificationCheck(
                    name="bucket_reachable",
                    target="object_storage",
                    expected_result="bucket metadata can be listed",
                ),
                BackupVerificationCheck(
                    name="storage_catalog_references_resolve",
                    target="storage_objects",
                    expected_result="catalog object keys exist in restored bucket",
                ),
            ],
        )

    def _redis_component(self) -> BackupManifestComponent:
        return BackupManifestComponent(
            type=BackupComponentType.REDIS,
            name="redis_ephemeral_state",
            backend="redis",
            location_ref="env:TAROAI_REDIS_URL",
            restore_order=3,
            metadata={
                "short_term_memory_backend": self.settings.short_term_memory_backend,
                "job_queue_backend": self.settings.job_queue_backend,
            },
            verification_checks=[
                BackupVerificationCheck(
                    name="ttl_keys_restored_or_rebuilt",
                    target="redis",
                    expected_result="short-lived keys are restored only when policy requires it",
                ),
            ],
        )

    def _config_component(self) -> BackupManifestComponent:
        return BackupManifestComponent(
            type=BackupComponentType.CONFIG,
            name="pydantic_settings_snapshot",
            backend="env",
            location_ref="env:TAROAI_*",
            restore_order=4,
            metadata={
                "settings_prefix": "TAROAI_",
                "environment": self.settings.environment,
                "sensitive_values_excluded": True,
            },
            verification_checks=[
                BackupVerificationCheck(
                    name="settings_loaded",
                    target="config",
                    expected_result="Pydantic settings load without validation errors",
                ),
            ],
        )

    def _restore_order(self, components: list[BackupManifestComponent]) -> list[str]:
        order = [
            component.type.value
            for component in sorted(components, key=lambda component: component.restore_order)
        ]
        order.append("workers")
        return order

    def _uses_redis(self) -> bool:
        return (
            self.settings.short_term_memory_backend == "redis"
            or self.settings.job_queue_backend == "redis"
        )

    def _database_backend(self) -> str:
        database_url = self.settings.database_url
        if database_url.startswith("postgresql://") or database_url.startswith("postgres://"):
            return "postgresql"
        if database_url.startswith("sqlite:///"):
            return "sqlite"
        return "unknown"
