import argparse
import json
from pathlib import Path

from taroai.db.models import MigrationPlan
from taroai.deployment.install_evidence import (
    RestoreDrillVerificationConfig,
    RestoreDrillVerificationResult,
)
from taroai.lifecycle.backup import BackupComponentType, BackupManifest
from taroai.storage.object_storage_verification import ObjectStorageVerificationResult
from taroai.workers.models import JobStatus
from taroai.workers.redis_verification import RedisQueueVerificationResult


def parse_args(argv: list[str] | None = None) -> RestoreDrillVerificationConfig:
    parser = argparse.ArgumentParser(
        description="Build private install validation evidence from a backup restore drill."
    )
    parser.add_argument("--drill-id", required=True)
    parser.add_argument("--backup-manifest", required=True)
    parser.add_argument("--executed-restore-order", required=True)
    parser.add_argument("--migration-plan", required=True)
    parser.add_argument("--object-storage-verification", required=True)
    parser.add_argument("--redis-queue-verification")
    parser.add_argument("--config-restored", action="store_true")
    parser.add_argument("--post-restore-checks-passed", action="store_true")
    parser.add_argument("--rpo-minutes", required=True, type=int)
    parser.add_argument("--rto-minutes", required=True, type=int)
    parsed = parser.parse_args(argv)
    return RestoreDrillVerificationConfig(
        drill_id=parsed.drill_id,
        backup_manifest_path=Path(parsed.backup_manifest),
        executed_restore_order=parse_restore_order(parsed.executed_restore_order),
        migration_plan_path=Path(parsed.migration_plan),
        object_storage_verification_path=Path(parsed.object_storage_verification),
        redis_queue_verification_path=(
            Path(parsed.redis_queue_verification)
            if parsed.redis_queue_verification
            else None
        ),
        config_restored=parsed.config_restored,
        post_restore_checks_passed=parsed.post_restore_checks_passed,
        rpo_minutes=parsed.rpo_minutes,
        rto_minutes=parsed.rto_minutes,
    )


def parse_restore_order(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def verify_restore_drill(
    config: RestoreDrillVerificationConfig,
) -> RestoreDrillVerificationResult:
    manifest = BackupManifest.model_validate_json(
        config.backup_manifest_path.read_text()
    )
    backup_manifest_generated = bool(manifest.components and manifest.restore_order)
    restore_order_executed = normalize_restore_order(
        config.executed_restore_order
    ) == normalize_restore_order(manifest.restore_order)
    database_restore_verified = verify_database_restore(config.migration_plan_path)
    object_storage_restore_verified = verify_object_storage_restore(
        config.object_storage_verification_path
    )
    redis_restore_or_rebuild_verified = verify_redis_restore_or_rebuild(
        manifest,
        config.redis_queue_verification_path,
    )
    post_restore_validation_passed = (
        backup_manifest_generated
        and restore_order_executed
        and database_restore_verified
        and object_storage_restore_verified
        and redis_restore_or_rebuild_verified
        and config.config_restored
        and config.post_restore_checks_passed
    )
    return RestoreDrillVerificationResult(
        drill_id=config.drill_id,
        backup_manifest_generated=backup_manifest_generated,
        restore_order_executed=restore_order_executed,
        database_restore_verified=database_restore_verified,
        object_storage_restore_verified=object_storage_restore_verified,
        redis_restore_or_rebuild_verified=redis_restore_or_rebuild_verified,
        config_restore_verified=config.config_restored,
        post_restore_validation_passed=post_restore_validation_passed,
        rpo_minutes=config.rpo_minutes,
        rto_minutes=config.rto_minutes,
    )


def normalize_restore_order(values: list[str]) -> list[str]:
    return [value.strip().lower() for value in values if value.strip()]


def verify_database_restore(path: Path) -> bool:
    plan = MigrationPlan.model_validate_json(path.read_text())
    return plan.up_to_date and not plan.pending_versions and not plan.unknown_applied_versions


def verify_object_storage_restore(path: Path) -> bool:
    result = ObjectStorageVerificationResult.model_validate_json(path.read_text())
    return (
        result.uploaded_bytes > 0
        and result.downloaded_bytes == result.uploaded_bytes
        and result.deleted
        and result.object_missing_after_delete
    )


def verify_redis_restore_or_rebuild(
    manifest: BackupManifest,
    redis_queue_verification_path: Path | None,
) -> bool:
    if not backup_manifest_uses_redis(manifest):
        return True
    if redis_queue_verification_path is None:
        return False
    result = RedisQueueVerificationResult.model_validate_json(
        redis_queue_verification_path.read_text()
    )
    return (
        result.ping_ok
        and result.acknowledged_job_status == JobStatus.SUCCEEDED
        and result.recovered_job_status == JobStatus.RUNNING
        and result.recovered_job_attempts >= 2
        and result.dead_letter_job_status == JobStatus.DEAD_LETTER
        and result.dead_letter_count >= 1
    )


def backup_manifest_uses_redis(manifest: BackupManifest) -> bool:
    return any(component.type == BackupComponentType.REDIS for component in manifest.components)


def main(argv: list[str] | None = None) -> int:
    config = parse_args(argv)
    result = verify_restore_drill(config)
    print(json.dumps(result.model_dump(mode="json"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
