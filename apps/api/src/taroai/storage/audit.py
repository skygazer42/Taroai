from taroai.storage.models import StorageObject


def storage_object_audit_metadata(storage_object: StorageObject) -> dict:
    return {
        "storage_object_id": storage_object.id,
        "workspace_id": storage_object.workspace_id,
        "run_id": storage_object.run_id,
        "purpose": storage_object.purpose.value,
        "bucket": storage_object.bucket,
        "key": storage_object.key,
        "content_type": storage_object.content_type,
        "size_bytes": storage_object.size_bytes,
        "acl_subject_count": len(storage_object.acl_subjects),
        "sensitivity_level": storage_object.sensitivity_level,
        "retention_expires_at": (
            storage_object.retention_expires_at.isoformat()
            if storage_object.retention_expires_at is not None
            else None
        ),
        "deleted_at": (
            storage_object.deleted_at.isoformat()
            if storage_object.deleted_at is not None
            else None
        ),
    }
