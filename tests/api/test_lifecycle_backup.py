from taroai.config import Settings
from taroai.lifecycle import BackupManifestRequest, BackupManifestService, BackupComponentType


def test_backup_manifest_describes_required_components_without_secrets():
    settings = Settings(
        environment="poc",
        database_url="postgresql://user:secret@db.internal:5432/taroai",
        object_storage_bucket="tenant-backups",
        object_storage_region="us-west-2",
        object_storage_endpoint="https://minio.internal",
        short_term_memory_backend="redis",
        job_queue_backend="redis",
        redis_url="redis://:secret@redis.internal:6379/0",
        _env_file=None,
    )
    service = BackupManifestService(settings=settings)

    manifest = service.create_manifest(
        BackupManifestRequest(
            tenant_id="tenant_acme",
            requested_by_user_id="user_admin",
        )
    )

    component_types = [component.type for component in manifest.components]
    assert component_types == [
        BackupComponentType.DATABASE,
        BackupComponentType.OBJECT_STORAGE,
        BackupComponentType.REDIS,
        BackupComponentType.CONFIG,
    ]
    assert manifest.restore_order == [
        "database",
        "object_storage",
        "redis",
        "config",
        "workers",
    ]
    assert manifest.components[0].backend == "postgresql"
    assert manifest.components[0].location_ref == "env:TAROAI_DATABASE_URL"
    assert manifest.components[1].metadata["bucket"] == "tenant-backups"
    assert manifest.components[1].metadata["region"] == "us-west-2"
    assert manifest.components[2].location_ref == "env:TAROAI_REDIS_URL"
    serialized = manifest.model_dump_json()
    assert "secret" not in serialized
    assert "postgresql://user" not in serialized
    assert "redis://:" not in serialized


def test_backup_manifest_omits_redis_component_when_redis_backends_are_disabled():
    settings = Settings(
        short_term_memory_backend="memory",
        job_queue_backend="disabled",
        _env_file=None,
    )
    service = BackupManifestService(settings=settings)

    manifest = service.create_manifest(
        BackupManifestRequest(
            tenant_id="tenant_acme",
            requested_by_user_id="user_admin",
        )
    )

    assert BackupComponentType.REDIS not in [component.type for component in manifest.components]
    assert manifest.restore_order == [
        "database",
        "object_storage",
        "config",
        "workers",
    ]
