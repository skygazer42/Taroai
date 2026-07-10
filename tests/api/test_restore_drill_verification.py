import json
from pathlib import Path

from taroai.db.models import MigrationPlan
from taroai.deployment.restore_drill_verification import (
    RestoreDrillVerificationConfig,
    main,
    verify_restore_drill,
)
from taroai.lifecycle.backup import (
    BackupComponentType,
    BackupManifest,
    BackupManifestComponent,
)
from taroai.storage.object_storage_verification import ObjectStorageVerificationResult
from taroai.workers.models import JobStatus
from taroai.workers.redis_verification import RedisQueueVerificationResult


def write_json(path: Path, value) -> Path:
    path.write_text(value.model_dump_json())
    return path


def write_restore_drill_inputs(
    tmp_path: Path,
    include_redis: bool = True,
    migration_plan: MigrationPlan | None = None,
    object_storage_result: ObjectStorageVerificationResult | None = None,
    redis_result: RedisQueueVerificationResult | None = None,
) -> dict[str, Path | list[str]]:
    components = [
        BackupManifestComponent(
            type=BackupComponentType.DATABASE,
            name="control_plane_database",
            backend="postgresql",
            location_ref="env:TAROAI_DATABASE_URL",
            restore_order=1,
        ),
        BackupManifestComponent(
            type=BackupComponentType.OBJECT_STORAGE,
            name="object_storage_bucket",
            backend="s3_compatible",
            location_ref="env:TAROAI_OBJECT_STORAGE_BUCKET",
            restore_order=2,
        ),
    ]
    restore_order = ["database", "object_storage"]
    if include_redis:
        components.append(
            BackupManifestComponent(
                type=BackupComponentType.REDIS,
                name="redis_ephemeral_state",
                backend="redis",
                location_ref="env:TAROAI_REDIS_URL",
                restore_order=3,
            )
        )
        restore_order.append("redis")
    components.append(
        BackupManifestComponent(
            type=BackupComponentType.CONFIG,
            name="pydantic_settings_snapshot",
            backend="env",
            location_ref="env:TAROAI_*",
            restore_order=4,
        )
    )
    restore_order.extend(["config", "workers"])
    manifest_path = write_json(
        tmp_path / "backup-manifest.json",
        BackupManifest(
            tenant_id="tenant_restore_drill",
            requested_by_user_id="user_restore_drill",
            environment="production",
            components=components,
            restore_order=restore_order,
        ),
    )
    migration_path = write_json(
        tmp_path / "migration-plan.json",
        migration_plan
        or MigrationPlan(
            available_versions=["001_initial"],
            applied_versions=["001_initial"],
            pending_versions=[],
            unknown_applied_versions=[],
            up_to_date=True,
        ),
    )
    object_storage_path = write_json(
        tmp_path / "object-storage-verification.json",
        object_storage_result
        or ObjectStorageVerificationResult(
            bucket="taroai",
            object_key="verify/object.txt",
            uploaded_bytes=12,
            downloaded_bytes=12,
            read_signed_url_method="GET",
            write_signed_url_method="PUT",
            deleted=True,
            object_missing_after_delete=True,
        ),
    )
    redis_path = write_json(
        tmp_path / "redis-queue-verification.json",
        redis_result
        or RedisQueueVerificationResult(
            key_prefix="taroai:verify",
            ping_ok=True,
            acknowledged_job_id="job_ack",
            acknowledged_job_status=JobStatus.SUCCEEDED,
            recovered_job_id="job_recovered",
            recovered_job_status=JobStatus.RUNNING,
            recovered_job_attempts=2,
            dead_letter_job_id="job_dead",
            dead_letter_job_status=JobStatus.DEAD_LETTER,
            dead_letter_count=1,
        ),
    )
    return {
        "manifest_path": manifest_path,
        "migration_path": migration_path,
        "object_storage_path": object_storage_path,
        "redis_path": redis_path,
        "restore_order": restore_order,
    }


def test_restore_drill_verification_generates_install_validation_evidence(
    tmp_path: Path,
):
    inputs = write_restore_drill_inputs(tmp_path)

    result = verify_restore_drill(
        RestoreDrillVerificationConfig(
            drill_id="restore_drill_2026_07",
            backup_manifest_path=inputs["manifest_path"],
            executed_restore_order=inputs["restore_order"],
            migration_plan_path=inputs["migration_path"],
            object_storage_verification_path=inputs["object_storage_path"],
            redis_queue_verification_path=inputs["redis_path"],
            config_restored=True,
            post_restore_checks_passed=True,
            rpo_minutes=45,
            rto_minutes=25,
        )
    )

    assert result.drill_id == "restore_drill_2026_07"
    assert result.backup_manifest_generated is True
    assert result.restore_order_executed is True
    assert result.database_restore_verified is True
    assert result.object_storage_restore_verified is True
    assert result.redis_restore_or_rebuild_verified is True
    assert result.config_restore_verified is True
    assert result.post_restore_validation_passed is True
    assert result.rpo_minutes == 45
    assert result.rto_minutes == 25


def test_verify_restore_drill_script_wraps_python_cli():
    script = Path("scripts/verify-restore-drill.sh")

    text = script.read_text()

    assert "python -m taroai.deployment.restore_drill_verification" in text
    assert "--backup-manifest" in text
    assert "--object-storage-verification" in text
    assert "--redis-queue-verification" in text


def test_restore_drill_verification_accepts_manifest_without_redis_component(
    tmp_path: Path,
):
    inputs = write_restore_drill_inputs(tmp_path, include_redis=False)

    result = verify_restore_drill(
        RestoreDrillVerificationConfig(
            drill_id="restore_drill_no_redis",
            backup_manifest_path=inputs["manifest_path"],
            executed_restore_order=inputs["restore_order"],
            migration_plan_path=inputs["migration_path"],
            object_storage_verification_path=inputs["object_storage_path"],
            config_restored=True,
            post_restore_checks_passed=True,
            rpo_minutes=30,
            rto_minutes=20,
        )
    )

    assert result.redis_restore_or_rebuild_verified is True
    assert result.post_restore_validation_passed is True


def test_restore_drill_verification_marks_component_failures(
    tmp_path: Path,
):
    inputs = write_restore_drill_inputs(
        tmp_path,
        migration_plan=MigrationPlan(
            available_versions=["001_initial", "002_next"],
            applied_versions=["001_initial"],
            pending_versions=["002_next"],
            unknown_applied_versions=[],
            up_to_date=False,
        ),
    )

    result = verify_restore_drill(
        RestoreDrillVerificationConfig(
            drill_id="restore_drill_pending_database",
            backup_manifest_path=inputs["manifest_path"],
            executed_restore_order=["database", "redis", "object_storage", "config", "workers"],
            migration_plan_path=inputs["migration_path"],
            object_storage_verification_path=inputs["object_storage_path"],
            redis_queue_verification_path=inputs["redis_path"],
            config_restored=True,
            post_restore_checks_passed=True,
            rpo_minutes=45,
            rto_minutes=25,
        )
    )

    assert result.restore_order_executed is False
    assert result.database_restore_verified is False
    assert result.object_storage_restore_verified is True
    assert result.redis_restore_or_rebuild_verified is True
    assert result.post_restore_validation_passed is False


def test_restore_drill_verification_cli_outputs_install_validation_json(
    tmp_path: Path,
    capsys,
):
    inputs = write_restore_drill_inputs(tmp_path)

    exit_code = main(
        [
            "--drill-id",
            "restore_drill_cli",
            "--backup-manifest",
            str(inputs["manifest_path"]),
            "--executed-restore-order",
            ",".join(inputs["restore_order"]),
            "--migration-plan",
            str(inputs["migration_path"]),
            "--object-storage-verification",
            str(inputs["object_storage_path"]),
            "--redis-queue-verification",
            str(inputs["redis_path"]),
            "--config-restored",
            "--post-restore-checks-passed",
            "--rpo-minutes",
            "45",
            "--rto-minutes",
            "25",
        ]
    )

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["drill_id"] == "restore_drill_cli"
    assert output["post_restore_validation_passed"] is True
