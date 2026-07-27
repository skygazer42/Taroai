import json
import hashlib
import os
import stat
import subprocess
import zipfile
import base64
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
from pydantic import ValidationError

import taroai.deployment.release_package as release_package_module
from taroai.deployment.release_package import (
    ReleasePackageBuildConfig,
    ReleasePackageBuildResult,
    ReleasePackageSigningConfig,
    ReleasePackageSigningResult,
    ReleasePackageVerificationConfig,
    ReleasePackageVerificationReport,
    build_release_package,
    manifest_schema_json,
    required_archive_entries,
    sign_release_package,
    verify_release_package,
)


def sign_release_package_for_test(package_path: Path) -> tuple[Path, str, str]:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=Encoding.Raw,
        format=PublicFormat.Raw,
    )
    payload = {
        "algorithm": "ed25519",
        "key_id": "creao-release-2026-01",
        "package_sha256": sha256_file(package_path),
    }
    signature_payload = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    signature = private_key.sign(signature_payload)
    signature_path = package_path.with_suffix(".zip.sig.json")
    signature_path.write_text(
        json.dumps(
            {
                **payload,
                "signature": base64.b64encode(signature).decode("ascii"),
            }
        )
    )
    return (
        signature_path,
        payload["key_id"],
        base64.b64encode(public_key).decode("ascii"),
    )


def rewrite_zip_entry_mode(source: Path, target: Path, entry_name: str, mode: int) -> None:
    with zipfile.ZipFile(source) as source_archive:
        with zipfile.ZipFile(target, mode="w", compression=zipfile.ZIP_DEFLATED) as target_archive:
            for item in source_archive.infolist():
                content = source_archive.read(item.filename)
                rewritten = zipfile.ZipInfo(item.filename)
                rewritten.date_time = item.date_time
                rewritten.compress_type = zipfile.ZIP_DEFLATED
                rewritten.external_attr = item.external_attr
                if item.filename == entry_name:
                    rewritten.external_attr = (mode & 0xFFFF) << 16
                target_archive.writestr(rewritten, content)


def rewrite_zip_entry_content(
    source: Path,
    target: Path,
    entry_name: str,
    content: bytes,
) -> None:
    with zipfile.ZipFile(source) as source_archive:
        with zipfile.ZipFile(target, mode="w", compression=zipfile.ZIP_DEFLATED) as target_archive:
            for item in source_archive.infolist():
                rewritten = zipfile.ZipInfo(item.filename)
                rewritten.date_time = item.date_time
                rewritten.compress_type = zipfile.ZIP_DEFLATED
                rewritten.external_attr = item.external_attr
                target_archive.writestr(
                    rewritten,
                    content if item.filename == entry_name else source_archive.read(item.filename),
                )


def remove_zip_entry(source: Path, target: Path, entry_name: str) -> None:
    with zipfile.ZipFile(source) as source_archive:
        with zipfile.ZipFile(target, mode="w", compression=zipfile.ZIP_DEFLATED) as target_archive:
            for item in source_archive.infolist():
                if item.filename == entry_name:
                    continue
                rewritten = zipfile.ZipInfo(item.filename)
                rewritten.date_time = item.date_time
                rewritten.compress_type = zipfile.ZIP_DEFLATED
                rewritten.external_attr = item.external_attr
                target_archive.writestr(rewritten, source_archive.read(item.filename))


def append_executable_zip_entry(
    path: Path,
    entry_name: str,
    content: str,
) -> None:
    with zipfile.ZipFile(path, mode="a", compression=zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo(entry_name)
        info.external_attr = (0o755 & 0xFFFF) << 16
        archive.writestr(info, content.encode("utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_release_package_contract_models_reject_unknown_fields(tmp_path: Path):
    checksum = "a" * 64
    cases = [
        (
            ReleasePackageBuildConfig,
            {"unexpected_output_path": "release.zip"},
            "unexpected_output_path",
        ),
        (
            ReleasePackageBuildResult,
            {
                "output_path": tmp_path / "release.zip",
                "file_count": 1,
                "manifest_path": "infra/package/manifest.json",
                "checksum_sha256": checksum,
                "archive_checksum": checksum,
            },
            "archive_checksum",
        ),
        (
            ReleasePackageSigningConfig,
            {
                "package_path": tmp_path / "release.zip",
                "signature_path": tmp_path / "release.zip.sig.json",
                "key_id": "creao-release-2026-01",
                "private_key_base64": "secret",
                "public_key_base64": "not-accepted-here",
            },
            "public_key_base64",
        ),
        (
            ReleasePackageSigningResult,
            {
                "signature_path": tmp_path / "release.zip.sig.json",
                "key_id": "creao-release-2026-01",
                "package_sha256": checksum,
                "public_key_base64": "public-key",
                "private_key_base64": "must-not-appear",
            },
            "private_key_base64",
        ),
        (
            ReleasePackageVerificationConfig,
            {
                "package_path": tmp_path / "release.zip",
                "expected_checksum_sha256": checksum,
                "expected_sha256": checksum,
            },
            "expected_sha256",
        ),
        (
            ReleasePackageVerificationReport,
            {
                "package_path": tmp_path / "release.zip",
                "valid": True,
                "file_count": 1,
                "manifest_valid": True,
                "verified": True,
            },
            "verified",
        ),
    ]

    for model, payload, field_name in cases:
        with pytest.raises(ValidationError) as error:
            model.model_validate(payload)

        assert field_name in str(error.value)


def test_release_package_builder_creates_clean_zip_with_generated_manifest(tmp_path: Path):
    output_path = tmp_path / "taroai-release.zip"

    result = build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )

    assert result.output_path == output_path
    assert result.file_count > 0
    assert result.manifest_path == "infra/package/manifest.json"
    assert result.checksum_sha256 == sha256_file(output_path)

    with zipfile.ZipFile(output_path) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("infra/package/manifest.json"))

    assert "apps/api/Dockerfile" in names
    assert "apps/web/Dockerfile" in names
    assert "apps/api/Dockerfile.sandbox" in names
    assert "apps/api/src/taroai/app.py" in names
    assert "apps/api/src/taroai/deployment/local_cloud_poc_demo_gate.py" in names
    assert "apps/web/index.html" in names
    assert "apps/web/assets/main.js" in names
    assert "apps/web/assets/styles.css" in names
    assert "infra/docker-compose.yml" in names
    assert "scripts/build-package-manifest.sh" in names
    assert "scripts/build-migration-plan.sh" in names
    assert "scripts/verify-object-storage.sh" in names
    assert "scripts/verify-redis-queue.sh" in names
    assert "scripts/verify-secret-manager.sh" in names
    assert "scripts/verify-event-stream.sh" in names
    assert "scripts/verify-audit-write.sh" in names
    assert "scripts/verify-trace-collector.sh" in names
    assert "scripts/verify-restore-drill.sh" in names
    assert "scripts/sign-release-package.sh" in names
    assert "scripts/build-release-transfer-evidence.sh" in names
    assert "scripts/redact-support-bundle.sh" in names
    assert "scripts/verify-model-gateway.sh" in names
    assert "scripts/verify-sandbox-lifecycle.sh" in names
    assert "scripts/verify-kubernetes-sandbox.sh" in names
    assert "scripts/verify-browser-controller.sh" in names
    assert "scripts/verify-local-cloud-poc.sh" in names
    assert "scripts/verify-local-cloud-demo-ready.sh" in names
    assert "scripts/verify-compose-strict-e2e.sh" in names
    assert ".env.example" in names

    image_names = {image["name"] for image in manifest["images"]}
    assert {"api", "worker", "browser-controller", "web"}.issubset(image_names)
    assert len(manifest["migrations"]) == 49
    assert (
        manifest["migrations"][-1]["id"]
        == "049_billing_meter_run_index"
    )

    forbidden_exact = {".env", "a.md"}
    assert forbidden_exact.isdisjoint(names)
    assert not any(name.startswith(".git/") for name in names)
    assert not any(name.startswith(".direnv/") for name in names)
    assert not any(name.startswith(".idea/") for name in names)
    assert not any(name.startswith(".pytest_cache/") for name in names)
    assert not any("__pycache__/" in name for name in names)
    assert not any(name.endswith(".pyc") for name in names)
    assert not any(name.startswith("tests/") for name in names)


def test_release_package_required_entries_cover_core_deployment_manifests():
    entries = set(
        required_archive_entries(
            "infra/package/manifest.json",
            "infra/package/manifest.schema.json",
        )
    )

    expected_entries = {
        "apps/api/src/taroai/sandbox/controller_service.py",
        "apps/api/src/taroai/sandbox/docker.py",
        "apps/api/src/taroai/sandbox/http.py",
        "apps/api/src/taroai/sandbox/kubernetes.py",
        "infra/k8s/api.yaml",
        "infra/k8s/sandbox-runtime-policy.yaml",
        "infra/k8s/sandbox-controller.yaml",
        "infra/k8s/browser-controller.yaml",
        "infra/k8s/configmap.yaml",
        "infra/k8s/kustomization.yaml",
        "infra/k8s/minio.yaml",
        "infra/k8s/network-policy.yaml",
        "infra/k8s/postgres.yaml",
        "infra/k8s/redis.yaml",
        "infra/k8s/secrets.example.yaml",
        "infra/k8s/web.yaml",
        "infra/k8s/worker.yaml",
        "infra/helm/taroai/Chart.yaml",
        "infra/helm/taroai/values.yaml",
        "infra/helm/taroai/templates/README.md",
        "infra/helm/taroai/templates/api.yaml",
        "infra/helm/taroai/templates/sandbox-controller.yaml",
        "infra/helm/taroai/templates/browser-controller.yaml",
        "infra/helm/taroai/templates/configmap.yaml",
        "infra/helm/taroai/templates/hpa.yaml",
        "infra/helm/taroai/templates/ingress.yaml",
        "infra/helm/taroai/templates/migration-job.yaml",
        "infra/helm/taroai/templates/network-policy.yaml",
        "infra/helm/taroai/templates/sandbox-runtime-policy.yaml",
        "infra/helm/taroai/templates/serviceaccount.yaml",
        "infra/helm/taroai/templates/web.yaml",
        "infra/helm/taroai/templates/worker.yaml",
    }

    assert expected_entries.issubset(entries)


def test_release_package_required_entries_cover_private_delivery_runbooks_and_profiles():
    entries = set(
        required_archive_entries(
            "infra/package/manifest.json",
            "infra/package/manifest.schema.json",
        )
    )

    expected_entries = {
        "infra/package/README.md",
        "infra/package/upgrade-matrix.md",
        "infra/config/cloud.env.example",
        "infra/config/byoc.env.example",
        "infra/config/private.env.example",
        "infra/config/deepseek.env.example",
        "infra/config/zhipu.env.example",
        "docs/customer-success/admin-training.md",
        "docs/customer-success/employee-training.md",
        "docs/customer-success/rollout-playbook.md",
        "docs/customer-success/solution-engineer-checklist.md",
        "docs/operations/mvp-local-cloud-poc.md",
        "docs/operations/private-install-validation.md",
        "docs/operations/private-upgrade-rollback.md",
        "docs/operations/air-gapped-install.md",
        "docs/operations/alert-routing.md",
        "docs/operations/disaster-recovery.md",
        "docs/operations/postmortem-template.md",
        "docs/operations/tenant-offboarding-runbook.md",
        "docs/operations/triggers-runbook.md",
        "docs/solution-packs/ecommerce.md",
        "docs/solution-packs/operations.md",
        "docs/solution-packs/sales.md",
        "docs/solution-packs/support.md",
    }

    assert expected_entries.issubset(entries)


def test_release_package_required_entries_cover_container_build_inputs():
    entries = set(
        required_archive_entries(
            "infra/package/manifest.json",
            "infra/package/manifest.schema.json",
        )
    )

    expected_entries = {
        "apps/api/Dockerfile",
        "apps/api/Dockerfile.browser",
        "apps/api/entrypoint.sh",
        "apps/api/requirements.txt",
        "apps/api/requirements-browser.txt",
        "apps/api/migrations/001_initial.sql",
    }

    assert expected_entries.issubset(entries)


def test_release_package_required_entries_cover_executable_python_entrypoints():
    entries = set(
        required_archive_entries(
            "infra/package/manifest.json",
            "infra/package/manifest.schema.json",
        )
    )

    expected_entries = {
        "apps/api/src/taroai/db/migration_cli.py",
        "apps/api/src/taroai/deployment/api_verification.py",
        "apps/api/src/taroai/deployment/install_validation.py",
        "apps/api/src/taroai/deployment/local_cloud_poc_verification.py",
        "apps/api/src/taroai/deployment/package_manifest.py",
        "apps/api/src/taroai/deployment/release_package.py",
        "apps/api/src/taroai/deployment/restore_drill_verification.py",
        "apps/api/src/taroai/deployment/transfer_evidence.py",
        "apps/api/src/taroai/model_gateway/verification.py",
        "apps/api/src/taroai/observability/verification.py",
        "apps/api/src/taroai/sandbox/browser_verification.py",
        "apps/api/src/taroai/sandbox/kubernetes_verification.py",
        "apps/api/src/taroai/sandbox/lifecycle_verification.py",
        "apps/api/src/taroai/sandbox/playwright_service.py",
        "apps/api/src/taroai/secrets/verification.py",
        "apps/api/src/taroai/storage/object_storage_verification.py",
        "apps/api/src/taroai/support/redaction.py",
        "apps/api/src/taroai/workers/redis_verification.py",
    }

    assert expected_entries.issubset(entries)


def test_release_package_required_entries_cover_release_validation_dependencies():
    entries = set(
        required_archive_entries(
            "infra/package/manifest.json",
            "infra/package/manifest.schema.json",
        )
    )

    expected_entries = {
        "apps/api/src/taroai/config.py",
        "apps/api/src/taroai/db/models.py",
        "apps/api/src/taroai/deployment/models.py",
        "apps/api/src/taroai/deployment/install_evidence.py",
        "apps/api/src/taroai/deployment/validation.py",
        "apps/api/src/taroai/deployment_evidence.py",
        "apps/api/src/taroai/sandbox/image_policy.py",
        "apps/api/src/taroai/workers/models.py",
    }

    assert expected_entries.issubset(entries)


def test_release_package_required_entries_cover_verifier_dependency_modules():
    entries = set(
        required_archive_entries(
            "infra/package/manifest.json",
            "infra/package/manifest.schema.json",
        )
    )

    expected_entries = {
        "apps/api/src/taroai/domain.py",
        "apps/api/src/taroai/errors.py",
        "apps/api/src/taroai/lifecycle/backup.py",
        "apps/api/src/taroai/model_gateway/gateway.py",
        "apps/api/src/taroai/model_gateway/models.py",
        "apps/api/src/taroai/model_gateway/providers.py",
        "apps/api/src/taroai/observability/exporter.py",
        "apps/api/src/taroai/observability/models.py",
        "apps/api/src/taroai/sandbox/adapter.py",
        "apps/api/src/taroai/sandbox/browser.py",
        "apps/api/src/taroai/sandbox/models.py",
        "apps/api/src/taroai/secrets/models.py",
        "apps/api/src/taroai/secrets/service.py",
        "apps/api/src/taroai/storage/adapter.py",
        "apps/api/src/taroai/storage/models.py",
        "apps/api/src/taroai/workers/queue.py",
    }

    assert expected_entries.issubset(entries)


def test_release_package_required_entries_cover_top_level_release_metadata():
    entries = set(
        required_archive_entries(
            "infra/package/manifest.json",
            "infra/package/manifest.schema.json",
        )
    )

    expected_entries = {
        ".env.example",
        "README.md",
        "pyproject.toml",
    }

    assert expected_entries.issubset(entries)


def test_release_package_builder_skips_existing_embedded_manifest(tmp_path: Path):
    repository_root = tmp_path / "repo"
    migrations_dir = repository_root / "apps/api/migrations"
    package_dir = repository_root / "infra/package"
    migrations_dir.mkdir(parents=True)
    package_dir.mkdir(parents=True)
    (migrations_dir / "001_initial.sql").write_text("create table release_check(id text);\n")
    (package_dir / "manifest.json").write_text('{"package_version": "stale"}\n')

    output_path = tmp_path / "taroai-release.zip"

    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=repository_root,
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
            include_paths=[Path("infra/package")],
        )
    )

    with zipfile.ZipFile(output_path) as archive:
        manifest_entries = [
            item.filename
            for item in archive.infolist()
            if item.filename == "infra/package/manifest.json"
        ]
        manifest = json.loads(archive.read("infra/package/manifest.json"))

    assert manifest_entries == ["infra/package/manifest.json"]
    assert manifest["app_version"] == "0.1.0"
    assert manifest["migrations"][0]["id"] == "001_initial"


def test_release_package_builder_writes_generated_manifest_schema(tmp_path: Path):
    repository_root = tmp_path / "repo"
    migrations_dir = repository_root / "apps/api/migrations"
    package_dir = repository_root / "infra/package"
    migrations_dir.mkdir(parents=True)
    package_dir.mkdir(parents=True)
    (migrations_dir / "001_initial.sql").write_text("create table release_check(id text);\n")
    (package_dir / "manifest.schema.json").write_text('{"title": "Outdated"}\n')

    output_path = tmp_path / "taroai-release.zip"

    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=repository_root,
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
            include_paths=[Path("infra/package")],
        )
    )

    with zipfile.ZipFile(output_path) as archive:
        schema_entries = [
            item.filename
            for item in archive.infolist()
            if item.filename == "infra/package/manifest.schema.json"
        ]
        schema = json.loads(archive.read("infra/package/manifest.schema.json"))

    assert schema_entries == ["infra/package/manifest.schema.json"]
    assert schema == json.loads(manifest_schema_json())


def test_release_package_builder_rejects_unsafe_generated_manifest_path(
    tmp_path: Path,
):
    repository_root = tmp_path / "repo"
    migrations_dir = repository_root / "apps/api/migrations"
    migrations_dir.mkdir(parents=True)
    (migrations_dir / "001_initial.sql").write_text("create table release_check(id text);\n")

    with pytest.raises(
        ValueError,
        match="release generated archive path must be relative and safe",
    ):
        build_release_package(
            ReleasePackageBuildConfig(
                repository_root=repository_root,
                output_path=tmp_path / "taroai-release.zip",
                package_version="0.1.0",
                app_version="0.1.0",
                image_tag="0.1.0",
                include_paths=[],
                manifest_path="../manifest.json",
            )
        )


def test_release_package_builder_rejects_absolute_generated_schema_path(
    tmp_path: Path,
):
    repository_root = tmp_path / "repo"
    migrations_dir = repository_root / "apps/api/migrations"
    migrations_dir.mkdir(parents=True)
    (migrations_dir / "001_initial.sql").write_text("create table release_check(id text);\n")

    with pytest.raises(
        ValueError,
        match="release generated archive path must be relative and safe",
    ):
        build_release_package(
            ReleasePackageBuildConfig(
                repository_root=repository_root,
                output_path=tmp_path / "taroai-release.zip",
                package_version="0.1.0",
                app_version="0.1.0",
                image_tag="0.1.0",
                include_paths=[],
                schema_path="/tmp/manifest.schema.json",
            )
        )


def test_release_package_builder_excludes_existing_output_path_from_archive(
    tmp_path: Path,
):
    repository_root = tmp_path / "repo"
    migrations_dir = repository_root / "apps/api/migrations"
    docs_dir = repository_root / "docs"
    migrations_dir.mkdir(parents=True)
    docs_dir.mkdir(parents=True)
    (migrations_dir / "001_initial.sql").write_text("create table release_check(id text);\n")
    (docs_dir / "runbook.md").write_text("# Runbook\n")
    output_path = docs_dir / "taroai-release.zip"
    output_path.write_bytes(b"previous-package-bytes")

    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=repository_root,
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
            include_paths=[Path("docs")],
        )
    )

    with zipfile.ZipFile(output_path) as archive:
        names = set(archive.namelist())
        contents = [archive.read(name) for name in names]

    assert "docs/runbook.md" in names
    assert "docs/taroai-release.zip" not in names
    assert b"previous-package-bytes" not in contents


def test_release_package_builder_excludes_editor_and_backup_artifacts(
    tmp_path: Path,
):
    repository_root = tmp_path / "repo"
    docs_dir = repository_root / "docs"
    migrations_dir = repository_root / "apps/api/migrations"
    docs_dir.mkdir(parents=True)
    migrations_dir.mkdir(parents=True)
    (migrations_dir / "001_initial.sql").write_text("create table release_check(id text);\n")
    (docs_dir / "runbook.md").write_text("# Runbook\n")
    (docs_dir / "runbook.md~").write_text("editor backup\n")
    (docs_dir / "runbook.md.bak").write_text("backup\n")
    (docs_dir / "runbook.md.orig").write_text("merge original\n")
    (docs_dir / "runbook.md.rej").write_text("patch reject\n")
    (docs_dir / ".runbook.md.swp").write_text("swap\n")
    (docs_dir / "package.tmp").write_text("temporary package bytes\n")

    output_path = tmp_path / "taroai-release.zip"

    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=repository_root,
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
            include_paths=[Path("docs")],
        )
    )

    with zipfile.ZipFile(output_path) as archive:
        names = set(archive.namelist())

    assert "docs/runbook.md" in names
    assert "docs/runbook.md~" not in names
    assert "docs/runbook.md.bak" not in names
    assert "docs/runbook.md.orig" not in names
    assert "docs/runbook.md.rej" not in names
    assert "docs/.runbook.md.swp" not in names
    assert "docs/package.tmp" not in names


def test_release_package_builder_excludes_os_ide_and_coverage_artifacts(
    tmp_path: Path,
):
    repository_root = tmp_path / "repo"
    docs_dir = repository_root / "docs"
    migrations_dir = repository_root / "apps/api/migrations"
    docs_dir.mkdir(parents=True)
    migrations_dir.mkdir(parents=True)
    (migrations_dir / "001_initial.sql").write_text("create table release_check(id text);\n")
    (docs_dir / "runbook.md").write_text("# Runbook\n")
    (docs_dir / ".coverage").write_text("coverage sqlite bytes\n")
    (docs_dir / ".coverage.worker").write_text("coverage worker bytes\n")
    (docs_dir / "coverage.xml").write_text("<coverage />\n")
    (docs_dir / "Thumbs.db").write_bytes(b"windows-thumbnail-cache")
    (docs_dir / "desktop.ini").write_text("[.ShellClassInfo]\n")
    (docs_dir / "__MACOSX").mkdir()
    (docs_dir / "__MACOSX" / "._runbook.md").write_bytes(b"mac metadata")
    (docs_dir / ".vscode").mkdir()
    (docs_dir / ".vscode" / "settings.json").write_text("{}\n")
    (docs_dir / "htmlcov").mkdir()
    (docs_dir / "htmlcov" / "index.html").write_text("<html></html>\n")

    output_path = tmp_path / "taroai-release.zip"

    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=repository_root,
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
            include_paths=[Path("docs")],
        )
    )

    with zipfile.ZipFile(output_path) as archive:
        names = set(archive.namelist())

    assert "docs/runbook.md" in names
    assert "docs/.coverage" not in names
    assert "docs/.coverage.worker" not in names
    assert "docs/coverage.xml" not in names
    assert "docs/Thumbs.db" not in names
    assert "docs/desktop.ini" not in names
    assert "docs/__MACOSX/._runbook.md" not in names
    assert "docs/.vscode/settings.json" not in names
    assert "docs/htmlcov/index.html" not in names


def test_release_package_builder_excludes_credential_named_artifacts(
    tmp_path: Path,
):
    repository_root = tmp_path / "repo"
    docs_dir = repository_root / "docs"
    migrations_dir = repository_root / "apps/api/migrations"
    docs_dir.mkdir(parents=True)
    migrations_dir.mkdir(parents=True)
    (migrations_dir / "001_initial.sql").write_text("create table release_check(id text);\n")
    (docs_dir / "runbook.md").write_text("# Runbook\n")
    (docs_dir / ".netrc").write_text("machine api.example.com login operator\n")
    (docs_dir / ".npmrc").write_text("//registry.example.com/:_authToken=placeholder\n")
    (docs_dir / ".pypirc").write_text("[distutils]\n")
    (docs_dir / "id_rsa").write_text("local ssh key bytes\n")
    (docs_dir / "id_ed25519").write_text("local ssh key bytes\n")
    (docs_dir / "kubeconfig").write_text("apiVersion: v1\n")
    (docs_dir / "service-account.json").write_text("{}\n")
    (docs_dir / "client_secret.json").write_text("{}\n")
    (docs_dir / ".aws").mkdir()
    (docs_dir / ".aws" / "credentials").write_text("[default]\n")
    (docs_dir / ".kube").mkdir()
    (docs_dir / ".kube" / "config").write_text("apiVersion: v1\n")

    output_path = tmp_path / "taroai-release.zip"

    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=repository_root,
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
            include_paths=[Path("docs")],
        )
    )

    with zipfile.ZipFile(output_path) as archive:
        names = set(archive.namelist())

    assert "docs/runbook.md" in names
    assert "docs/.netrc" not in names
    assert "docs/.npmrc" not in names
    assert "docs/.pypirc" not in names
    assert "docs/id_rsa" not in names
    assert "docs/id_ed25519" not in names
    assert "docs/kubeconfig" not in names
    assert "docs/service-account.json" not in names
    assert "docs/client_secret.json" not in names
    assert "docs/.aws/credentials" not in names
    assert "docs/.kube/config" not in names


def test_release_package_builder_excludes_credential_extension_artifacts(
    tmp_path: Path,
):
    repository_root = tmp_path / "repo"
    docs_dir = repository_root / "docs"
    migrations_dir = repository_root / "apps/api/migrations"
    docs_dir.mkdir(parents=True)
    migrations_dir.mkdir(parents=True)
    (migrations_dir / "001_initial.sql").write_text("create table release_check(id text);\n")
    (docs_dir / "runbook.md").write_text("# Runbook\n")
    credential_filenames = [
        "operator.cer",
        "operator.crt",
        "operator.der",
        "operator.pem",
        "operator.key",
        "operator.p12",
        "operator.pfx",
        "operator.jks",
        "operator.keystore",
    ]
    for filename in credential_filenames:
        (docs_dir / filename).write_text("credential bytes\n")

    output_path = tmp_path / "taroai-release.zip"

    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=repository_root,
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
            include_paths=[Path("docs")],
        )
    )

    with zipfile.ZipFile(output_path) as archive:
        names = set(archive.namelist())

    assert "docs/runbook.md" in names
    for filename in credential_filenames:
        assert f"docs/{filename}" not in names


def test_release_package_builder_preserves_existing_output_when_zip_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repository_root = tmp_path / "repo"
    migrations_dir = repository_root / "apps/api/migrations"
    docs_dir = repository_root / "docs"
    migrations_dir.mkdir(parents=True)
    docs_dir.mkdir(parents=True)
    (migrations_dir / "001_initial.sql").write_text("create table release_check(id text);\n")
    (docs_dir / "runbook.md").write_text("# Runbook\n")
    output_path = tmp_path / "taroai-release.zip"
    previous_bytes = b"previous release package bytes"
    output_path.write_bytes(previous_bytes)

    def fail_zip_write(*args, **kwargs):
        raise RuntimeError("zip write failed")

    monkeypatch.setattr(release_package_module, "write_bytes_to_zip", fail_zip_write)

    with pytest.raises(RuntimeError, match="zip write failed"):
        build_release_package(
            ReleasePackageBuildConfig(
                repository_root=repository_root,
                output_path=output_path,
                package_version="0.1.0",
                app_version="0.1.0",
                image_tag="0.1.0",
                include_paths=[Path("docs")],
            )
        )

    assert output_path.read_bytes() == previous_bytes
    assert list(tmp_path.glob("*.tmp")) == []


def test_release_package_builder_rejects_symlink_output_path(tmp_path: Path):
    repository_root = tmp_path / "repo"
    migrations_dir = repository_root / "apps/api/migrations"
    output_dir = repository_root / "dist"
    migrations_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    (migrations_dir / "001_initial.sql").write_text("create table release_check(id text);\n")
    outside_output = tmp_path / "outside-release.zip"
    outside_output.write_bytes(b"outside-release-placeholder")
    (output_dir / "taroai-release.zip").symlink_to(outside_output)

    with pytest.raises(ValueError, match="release output path must not be a symlink"):
        build_release_package(
            ReleasePackageBuildConfig(
                repository_root=repository_root,
                output_path=Path("dist/taroai-release.zip"),
                package_version="0.1.0",
                app_version="0.1.0",
                image_tag="0.1.0",
                include_paths=[],
            )
        )


def test_release_package_builder_rejects_broken_symlink_output_path(
    tmp_path: Path,
):
    repository_root = tmp_path / "repo"
    migrations_dir = repository_root / "apps/api/migrations"
    output_dir = repository_root / "dist"
    migrations_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    (migrations_dir / "001_initial.sql").write_text("create table release_check(id text);\n")
    (output_dir / "taroai-release.zip").symlink_to(tmp_path / "missing-release.zip")

    with pytest.raises(ValueError, match="release output path must not be a symlink"):
        build_release_package(
            ReleasePackageBuildConfig(
                repository_root=repository_root,
                output_path=Path("dist/taroai-release.zip"),
                package_version="0.1.0",
                app_version="0.1.0",
                image_tag="0.1.0",
                include_paths=[],
            )
        )


def test_release_package_builder_skips_symlink_paths(tmp_path: Path):
    repository_root = tmp_path / "repo"
    docs_dir = repository_root / "docs"
    migrations_dir = repository_root / "apps/api/migrations"
    docs_dir.mkdir(parents=True)
    migrations_dir.mkdir(parents=True)
    (migrations_dir / "001_initial.sql").write_text("create table release_check(id text);\n")
    (docs_dir / "runbook.md").write_text("# Runbook\n")
    outside_file = tmp_path / "operator-local-secret.txt"
    outside_file.write_text("operator-local-secret-value\n")
    (docs_dir / "operator-secret-link.txt").symlink_to(outside_file)

    output_path = tmp_path / "taroai-release.zip"

    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=repository_root,
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
            include_paths=[Path("docs")],
        )
    )

    with zipfile.ZipFile(output_path) as archive:
        names = set(archive.namelist())
        contents = [archive.read(name) for name in names]

    assert "docs/runbook.md" in names
    assert "docs/operator-secret-link.txt" not in names
    assert b"operator-local-secret-value" not in contents


def test_release_package_builder_rejects_secret_shaped_source_content(tmp_path: Path):
    repository_root = tmp_path / "repo"
    docs_dir = repository_root / "docs"
    migrations_dir = repository_root / "apps/api/migrations"
    docs_dir.mkdir(parents=True)
    migrations_dir.mkdir(parents=True)
    (migrations_dir / "001_initial.sql").write_text("create table release_check(id text);\n")
    (docs_dir / "leaked-key.txt").write_text("sk-" + ("A" * 24))
    output_path = tmp_path / "taroai-release.zip"

    with pytest.raises(
        ValueError,
        match="release source contains secret-shaped content",
    ):
        build_release_package(
            ReleasePackageBuildConfig(
                repository_root=repository_root,
                output_path=output_path,
                package_version="0.1.0",
                app_version="0.1.0",
                image_tag="0.1.0",
                include_paths=[Path("docs")],
            )
        )

    assert not output_path.exists()


def test_release_package_builder_rejects_symlink_include_root(tmp_path: Path):
    repository_root = tmp_path / "repo"
    migrations_dir = repository_root / "apps/api/migrations"
    outside_docs_dir = tmp_path / "outside-docs"
    migrations_dir.mkdir(parents=True)
    outside_docs_dir.mkdir()
    (migrations_dir / "001_initial.sql").write_text("create table release_check(id text);\n")
    (outside_docs_dir / "runbook.md").write_text("# External Runbook\n")
    (repository_root / "docs").symlink_to(outside_docs_dir, target_is_directory=True)

    with pytest.raises(ValueError, match="release include path must not be a symlink"):
        build_release_package(
            ReleasePackageBuildConfig(
                repository_root=repository_root,
                output_path=tmp_path / "taroai-release.zip",
                package_version="0.1.0",
                app_version="0.1.0",
                image_tag="0.1.0",
                include_paths=[Path("docs")],
            )
        )


def test_release_package_builder_rejects_broken_symlink_include_root(
    tmp_path: Path,
):
    repository_root = tmp_path / "repo"
    migrations_dir = repository_root / "apps/api/migrations"
    migrations_dir.mkdir(parents=True)
    (migrations_dir / "001_initial.sql").write_text("create table release_check(id text);\n")
    (repository_root / "docs").symlink_to(
        tmp_path / "missing-docs",
        target_is_directory=True,
    )

    with pytest.raises(ValueError, match="release include path must not be a symlink"):
        build_release_package(
            ReleasePackageBuildConfig(
                repository_root=repository_root,
                output_path=tmp_path / "taroai-release.zip",
                package_version="0.1.0",
                app_version="0.1.0",
                image_tag="0.1.0",
                include_paths=[Path("docs")],
            )
        )


def test_release_package_builder_rejects_absolute_include_root_outside_repository(
    tmp_path: Path,
):
    repository_root = tmp_path / "repo"
    migrations_dir = repository_root / "apps/api/migrations"
    outside_docs_dir = tmp_path / "outside-docs"
    migrations_dir.mkdir(parents=True)
    outside_docs_dir.mkdir()
    (migrations_dir / "001_initial.sql").write_text("create table release_check(id text);\n")
    (outside_docs_dir / "runbook.md").write_text("# External Runbook\n")

    with pytest.raises(
        ValueError,
        match="release include path must stay under repository root",
    ):
        build_release_package(
            ReleasePackageBuildConfig(
                repository_root=repository_root,
                output_path=tmp_path / "taroai-release.zip",
                package_version="0.1.0",
                app_version="0.1.0",
                image_tag="0.1.0",
                include_paths=[outside_docs_dir],
            )
        )


def test_release_package_builder_rejects_parent_relative_include_root_escape(
    tmp_path: Path,
):
    repository_root = tmp_path / "repo"
    migrations_dir = repository_root / "apps/api/migrations"
    outside_docs_dir = tmp_path / "outside-docs"
    migrations_dir.mkdir(parents=True)
    outside_docs_dir.mkdir()
    (migrations_dir / "001_initial.sql").write_text("create table release_check(id text);\n")
    (outside_docs_dir / "runbook.md").write_text("# External Runbook\n")

    with pytest.raises(
        ValueError,
        match="release include path must stay under repository root",
    ):
        build_release_package(
            ReleasePackageBuildConfig(
                repository_root=repository_root,
                output_path=tmp_path / "taroai-release.zip",
                package_version="0.1.0",
                app_version="0.1.0",
                image_tag="0.1.0",
                include_paths=[Path("../outside-docs")],
            )
        )


def test_build_release_package_script_wraps_python_cli():
    script = Path("scripts/build-release-package.sh")

    text = script.read_text()

    assert "python -m taroai.deployment.release_package" in text
    assert "--output" in text


def test_sign_release_package_script_wraps_python_cli():
    script = Path("scripts/sign-release-package.sh")

    text = script.read_text()

    assert "python -m taroai.deployment.release_package --sign" in text
    assert "--private-key-env" in text
    assert "--signature-output" in text


def test_redact_support_bundle_script_wraps_python_cli():
    script = Path("scripts/redact-support-bundle.sh")

    text = script.read_text()

    assert "python -m taroai.support.redaction" in text
    assert "--input" in text
    assert "--output" in text


def test_release_package_verifier_accepts_clean_zip(tmp_path: Path):
    output_path = tmp_path / "taroai-release.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=output_path)
    )

    assert report.valid is True
    assert report.manifest_valid is True
    assert report.forbidden_entries == []
    assert report.missing_required_entries == []
    assert report.missing_migration_entries == []
    assert report.migration_checksum_mismatches == []
    assert report.checksum_sha256 == sha256_file(output_path)
    assert report.file_count > 0


def test_release_package_verifier_rejects_missing_first_party_import_dependency(
    tmp_path: Path,
):
    output_path = tmp_path / "taroai-release.zip"
    broken_path = tmp_path / "broken-release.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    remove_zip_entry(
        output_path,
        broken_path,
        "apps/api/src/taroai/agent/__init__.py",
    )

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=broken_path)
    )

    assert report.valid is False
    assert "apps/api/src/taroai/agent/__init__.py" in (
        report.missing_import_dependency_entries
    )


def test_release_package_verifier_expands_from_taroai_import_submodule_dependencies(
    tmp_path: Path,
):
    output_path = tmp_path / "taroai-release.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    with zipfile.ZipFile(output_path, mode="a") as archive:
        archive.writestr(
            "apps/api/src/taroai/release_probe.py",
            "from taroai import missing_customer_module\n",
        )

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=output_path)
    )

    assert report.valid is False
    assert "apps/api/src/taroai/missing_customer_module.py" in (
        report.missing_import_dependency_entries
    )


def test_release_package_verifier_rejects_missing_script_module_target(
    tmp_path: Path,
):
    output_path = tmp_path / "taroai-release.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    append_executable_zip_entry(
        output_path,
        "scripts/verify-customer-health.sh",
        "#!/usr/bin/env sh\nexec python -m taroai.customer.health_check \"$@\"\n",
    )

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=output_path)
    )

    assert report.valid is False
    assert "apps/api/src/taroai/customer/health_check.py" in (
        report.missing_script_module_entries
    )


def test_release_package_verifier_rejects_invalid_python_source(tmp_path: Path):
    output_path = tmp_path / "taroai-release.zip"
    broken_path = tmp_path / "broken-release.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    rewrite_zip_entry_content(
        output_path,
        broken_path,
        "apps/api/src/taroai/deployment/release_package.py",
        b"def broken(:\n",
    )

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=broken_path)
    )

    assert report.valid is False
    assert "apps/api/src/taroai/deployment/release_package.py" in (
        report.invalid_python_entries
    )


def test_release_package_verifier_accepts_expected_checksum(tmp_path: Path):
    output_path = tmp_path / "taroai-release.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )

    report = verify_release_package(
        ReleasePackageVerificationConfig(
            package_path=output_path,
            expected_checksum_sha256=sha256_file(output_path),
        )
    )

    assert report.valid is True
    assert report.expected_checksum_sha256 == sha256_file(output_path)
    assert report.checksum_mismatch_errors == []


def test_release_package_verifier_accepts_detached_signature(tmp_path: Path):
    output_path = tmp_path / "taroai-release.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    signature_path, key_id, public_key = sign_release_package_for_test(output_path)

    report = verify_release_package(
        ReleasePackageVerificationConfig(
            package_path=output_path,
            signature_path=signature_path,
            trusted_public_keys={key_id: public_key},
        )
    )

    assert report.valid is True
    assert report.signature_valid is True
    assert report.signature_key_id == key_id
    assert report.signature_errors == []


def test_release_package_signer_writes_detached_signature_for_verifier(tmp_path: Path):
    output_path = tmp_path / "taroai-release.zip"
    signature_path = tmp_path / "taroai-release.zip.sig.json"
    private_key = Ed25519PrivateKey.generate()
    private_key_base64 = base64.b64encode(
        private_key.private_bytes(
            encoding=Encoding.Raw,
            format=PrivateFormat.Raw,
            encryption_algorithm=NoEncryption(),
        )
    ).decode("ascii")
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )

    result = sign_release_package(
        ReleasePackageSigningConfig(
            package_path=output_path,
            signature_path=signature_path,
            key_id="creao-release-2026-01",
            private_key_base64=private_key_base64,
        )
    )

    envelope = json.loads(signature_path.read_text())
    assert result.signature_path == signature_path
    assert result.key_id == "creao-release-2026-01"
    assert result.package_sha256 == sha256_file(output_path)
    assert envelope["package_sha256"] == sha256_file(output_path)
    assert "signature" in envelope
    assert private_key_base64 not in signature_path.read_text()

    report = verify_release_package(
        ReleasePackageVerificationConfig(
            package_path=output_path,
            signature_path=signature_path,
            trusted_public_keys={result.key_id: result.public_key_base64},
        )
    )

    assert report.valid is True
    assert report.signature_valid is True
    assert report.signature_errors == []


def test_release_package_signer_preserves_existing_signature_when_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    output_path = tmp_path / "taroai-release.zip"
    signature_path = tmp_path / "taroai-release.zip.sig.json"
    previous_bytes = b"previous signature envelope"
    signature_path.write_bytes(previous_bytes)
    private_key = Ed25519PrivateKey.generate()
    private_key_base64 = base64.b64encode(
        private_key.private_bytes(
            encoding=Encoding.Raw,
            format=PrivateFormat.Raw,
            encryption_algorithm=NoEncryption(),
        )
    ).decode("ascii")
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    original_write_text = Path.write_text

    def partial_signature_write(path: Path, data: str, *args, **kwargs):
        if "sig.json" in path.name:
            path.write_bytes(b"partial signature")
            raise RuntimeError("signature write failed")
        return original_write_text(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", partial_signature_write)

    with pytest.raises(RuntimeError, match="signature write failed"):
        sign_release_package(
            ReleasePackageSigningConfig(
                package_path=output_path,
                signature_path=signature_path,
                key_id="creao-release-2026-01",
                private_key_base64=private_key_base64,
            )
        )

    assert signature_path.read_bytes() == previous_bytes
    assert list(tmp_path.glob("*.tmp")) == []


def test_release_package_verifier_rejects_tampered_package_signature(tmp_path: Path):
    output_path = tmp_path / "taroai-release.zip"
    tampered_path = tmp_path / "taroai-release-tampered.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    signature_path, key_id, public_key = sign_release_package_for_test(output_path)
    rewrite_zip_entry_content(
        output_path,
        tampered_path,
        "infra/package/manifest.json",
        b'{"package_version":"tampered"}',
    )

    report = verify_release_package(
        ReleasePackageVerificationConfig(
            package_path=tampered_path,
            signature_path=signature_path,
            trusted_public_keys={key_id: public_key},
        )
    )

    assert report.valid is False
    assert report.signature_valid is False
    assert report.signature_errors == [
        "release package signature package_sha256 does not match archive SHA256"
    ]


def test_release_package_verifier_rejects_untrusted_signature_key(tmp_path: Path):
    output_path = tmp_path / "taroai-release.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    signature_path, _, _ = sign_release_package_for_test(output_path)

    report = verify_release_package(
        ReleasePackageVerificationConfig(
            package_path=output_path,
            signature_path=signature_path,
            trusted_public_keys={},
        )
    )

    assert report.valid is False
    assert report.signature_valid is False
    assert report.signature_errors == ["release package signing key is not trusted"]


def test_release_package_verifier_normalizes_uppercase_expected_checksum(
    tmp_path: Path,
):
    output_path = tmp_path / "taroai-release.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    checksum = sha256_file(output_path)

    report = verify_release_package(
        ReleasePackageVerificationConfig(
            package_path=output_path,
            expected_checksum_sha256=checksum.upper(),
        )
    )

    assert report.valid is True
    assert report.expected_checksum_sha256 == checksum
    assert report.checksum_mismatch_errors == []


def test_release_package_verifier_rejects_unexpected_checksum(tmp_path: Path):
    output_path = tmp_path / "taroai-release.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )

    report = verify_release_package(
        ReleasePackageVerificationConfig(
            package_path=output_path,
            expected_checksum_sha256="0" * 64,
        )
    )

    assert report.valid is False
    assert report.expected_checksum_sha256 == "0" * 64
    assert report.checksum_mismatch_errors == [
        "release package checksum does not match expected SHA256"
    ]


def test_release_package_verifier_rejects_forbidden_entries(tmp_path: Path):
    output_path = tmp_path / "taroai-release.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    with zipfile.ZipFile(output_path, mode="a") as archive:
        archive.writestr(".env", "TAROAI_MODEL_GATEWAY_API_KEY=secret")

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=output_path)
    )

    assert report.valid is False
    assert ".env" in report.forbidden_entries


def test_release_package_verifier_rejects_local_build_artifacts(tmp_path: Path):
    output_path = tmp_path / "taroai-release.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    with zipfile.ZipFile(output_path, mode="a") as archive:
        archive.writestr("a.out", b"local compiler output")
        archive.writestr("dist/taroai-release.zip", b"nested release archive")

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=output_path)
    )

    assert report.valid is False
    assert "a.out" in report.forbidden_entries
    assert "dist/taroai-release.zip" in report.forbidden_entries


def test_release_package_verifier_rejects_editor_and_backup_artifacts(
    tmp_path: Path,
):
    output_path = tmp_path / "taroai-release.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    with zipfile.ZipFile(output_path, mode="a") as archive:
        archive.writestr("docs/runbook.md~", "editor backup")
        archive.writestr("docs/runbook.md.bak", "backup")
        archive.writestr("docs/runbook.md.orig", "merge original")
        archive.writestr("docs/runbook.md.rej", "patch reject")
        archive.writestr("docs/.runbook.md.swp", "swap")
        archive.writestr("docs/package.tmp", "temporary package bytes")

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=output_path)
    )

    assert report.valid is False
    assert "docs/runbook.md~" in report.forbidden_entries
    assert "docs/runbook.md.bak" in report.forbidden_entries
    assert "docs/runbook.md.orig" in report.forbidden_entries
    assert "docs/runbook.md.rej" in report.forbidden_entries
    assert "docs/.runbook.md.swp" in report.forbidden_entries
    assert "docs/package.tmp" in report.forbidden_entries


def test_release_package_verifier_rejects_os_ide_and_coverage_artifacts(
    tmp_path: Path,
):
    output_path = tmp_path / "taroai-release.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    with zipfile.ZipFile(output_path, mode="a") as archive:
        archive.writestr("docs/.coverage", "coverage sqlite bytes")
        archive.writestr("docs/.coverage.worker", "coverage worker bytes")
        archive.writestr("docs/coverage.xml", "<coverage />")
        archive.writestr("docs/Thumbs.db", b"windows-thumbnail-cache")
        archive.writestr("docs/desktop.ini", "[.ShellClassInfo]")
        archive.writestr("docs/__MACOSX/._runbook.md", b"mac metadata")
        archive.writestr("docs/.vscode/settings.json", "{}")
        archive.writestr("docs/htmlcov/index.html", "<html></html>")

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=output_path)
    )

    assert report.valid is False
    assert "docs/.coverage" in report.forbidden_entries
    assert "docs/.coverage.worker" in report.forbidden_entries
    assert "docs/coverage.xml" in report.forbidden_entries
    assert "docs/Thumbs.db" in report.forbidden_entries
    assert "docs/desktop.ini" in report.forbidden_entries
    assert "docs/__MACOSX/._runbook.md" in report.forbidden_entries
    assert "docs/.vscode/settings.json" in report.forbidden_entries
    assert "docs/htmlcov/index.html" in report.forbidden_entries


def test_release_package_verifier_rejects_credential_named_artifacts(
    tmp_path: Path,
):
    output_path = tmp_path / "taroai-release.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    with zipfile.ZipFile(output_path, mode="a") as archive:
        archive.writestr("docs/.netrc", "machine api.example.com login operator")
        archive.writestr("docs/.npmrc", "//registry.example.com/:_authToken=placeholder")
        archive.writestr("docs/.pypirc", "[distutils]")
        archive.writestr("docs/id_rsa", "local ssh key bytes")
        archive.writestr("docs/id_ed25519", "local ssh key bytes")
        archive.writestr("docs/kubeconfig", "apiVersion: v1")
        archive.writestr("docs/service-account.json", "{}")
        archive.writestr("docs/client_secret.json", "{}")
        archive.writestr("docs/.aws/credentials", "[default]")
        archive.writestr("docs/.kube/config", "apiVersion: v1")

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=output_path)
    )

    assert report.valid is False
    assert "docs/.netrc" in report.forbidden_entries
    assert "docs/.npmrc" in report.forbidden_entries
    assert "docs/.pypirc" in report.forbidden_entries
    assert "docs/id_rsa" in report.forbidden_entries
    assert "docs/id_ed25519" in report.forbidden_entries
    assert "docs/kubeconfig" in report.forbidden_entries
    assert "docs/service-account.json" in report.forbidden_entries
    assert "docs/client_secret.json" in report.forbidden_entries
    assert "docs/.aws/credentials" in report.forbidden_entries
    assert "docs/.kube/config" in report.forbidden_entries


def test_release_package_verifier_rejects_credential_extension_artifacts(
    tmp_path: Path,
):
    output_path = tmp_path / "taroai-release.zip"
    credential_filenames = [
        "operator.cer",
        "operator.crt",
        "operator.der",
        "operator.pem",
        "operator.key",
        "operator.p12",
        "operator.pfx",
        "operator.jks",
        "operator.keystore",
    ]
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    with zipfile.ZipFile(output_path, mode="a") as archive:
        for filename in credential_filenames:
            archive.writestr(f"docs/{filename}", "credential bytes")

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=output_path)
    )

    assert report.valid is False
    for filename in credential_filenames:
        assert f"docs/{filename}" in report.forbidden_entries


def test_release_package_verifier_rejects_nested_archives(tmp_path: Path):
    output_path = tmp_path / "taroai-release.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    with zipfile.ZipFile(output_path, mode="a") as archive:
        archive.writestr("Taroai(7).zip", b"nested workspace snapshot")
        archive.writestr("docs/support-bundle.tar.gz", b"nested support bundle")

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=output_path)
    )

    assert report.valid is False
    assert "Taroai(7).zip" in report.forbidden_entries
    assert "docs/support-bundle.tar.gz" in report.forbidden_entries


def test_release_package_verifier_rejects_local_env_variants(tmp_path: Path):
    output_path = tmp_path / "taroai-release.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    with zipfile.ZipFile(output_path, mode="a") as archive:
        archive.writestr(".env.local", "TAROAI_ACCESS_TOKEN_SECRET=local")
        archive.writestr("infra/config/prod.env", "TAROAI_ENVIRONMENT=production")
        archive.writestr(
            "infra/config/runtime.env.example",
            "TAROAI_ENVIRONMENT=local",
        )

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=output_path)
    )

    assert report.valid is False
    assert ".env.local" in report.forbidden_entries
    assert "infra/config/prod.env" in report.forbidden_entries
    assert "infra/config/runtime.env.example" not in report.forbidden_entries


def test_release_package_verifier_rejects_direnv_files(tmp_path: Path):
    output_path = tmp_path / "taroai-release.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    with zipfile.ZipFile(output_path, mode="a") as archive:
        archive.writestr(".envrc", "export TAROAI_MODEL_GATEWAY_API_KEY=local")
        archive.writestr("docs/.envrc", "dotenv ../.env")
        archive.writestr(".direnv/export", "export TAROAI_MODEL_GATEWAY_API_KEY=local")
        archive.writestr(
            "docs/.direnv/cache",
            "TAROAI_ACCESS_TOKEN_SECRET=local",
        )

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=output_path)
    )

    assert report.valid is False
    assert ".envrc" in report.forbidden_entries
    assert "docs/.envrc" in report.forbidden_entries
    assert ".direnv/export" in report.forbidden_entries
    assert "docs/.direnv/cache" in report.forbidden_entries


def test_release_package_verifier_rejects_virtualenv_directories(tmp_path: Path):
    output_path = tmp_path / "taroai-release.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    with zipfile.ZipFile(output_path, mode="a") as archive:
        archive.writestr(".venv/pyvenv.cfg", "home=/usr/bin/python")
        archive.writestr("venv/bin/python", b"local interpreter")
        archive.writestr(".tox/py310/.pkg", b"tox package cache")

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=output_path)
    )

    assert report.valid is False
    assert ".venv/pyvenv.cfg" in report.forbidden_entries
    assert "venv/bin/python" in report.forbidden_entries
    assert ".tox/py310/.pkg" in report.forbidden_entries


def test_release_package_verifier_rejects_secret_shaped_content(tmp_path: Path):
    output_path = tmp_path / "taroai-release.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    secret_value = "sk-" + ("A" * 24)
    with zipfile.ZipFile(output_path, mode="a") as archive:
        archive.writestr("docs/operations/leaked-key.txt", secret_value)

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=output_path)
    )

    assert report.valid is False
    assert "docs/operations/leaked-key.txt" in report.secret_pattern_entries


def test_release_package_verifier_rejects_secret_shaped_content_with_url_safe_chars(
    tmp_path: Path,
):
    output_path = tmp_path / "taroai-release.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    secret_value = "sk-" + ("A" * 12) + "_-" + ("B" * 12)
    with zipfile.ZipFile(output_path, mode="a") as archive:
        archive.writestr("docs/operations/url-safe-key.txt", secret_value)

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=output_path)
    )

    assert report.valid is False
    assert "docs/operations/url-safe-key.txt" in report.secret_pattern_entries


def test_release_package_verifier_rejects_credentialed_http_urls(tmp_path: Path):
    output_path = tmp_path / "taroai-release.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    with zipfile.ZipFile(output_path, mode="a") as archive:
        archive.writestr(
            "docs/operations/leaked-url.txt",
            "\n".join(
                [
                    "https://agent:secret-value@api.customer.local/v1/runs",
                    "https://api.customer.local/callback?access_token=secret-value",
                ]
            ),
        )

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=output_path)
    )

    assert report.valid is False
    assert "docs/operations/leaked-url.txt" in report.secret_pattern_entries


def test_release_package_verifier_rejects_private_key_blocks(tmp_path: Path):
    output_path = tmp_path / "taroai-release.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    private_key_text = "\n".join(
        [
            "-----BEGIN PRIVATE KEY-----",
            "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC",
            "-----END PRIVATE KEY-----",
        ]
    )
    with zipfile.ZipFile(output_path, mode="a") as archive:
        archive.writestr("docs/operations/operator-key.pem", private_key_text)

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=output_path)
    )

    assert report.valid is False
    assert "docs/operations/operator-key.pem" in report.secret_pattern_entries


def test_release_package_verifier_rejects_duplicate_archive_entries(tmp_path: Path):
    output_path = tmp_path / "taroai-release.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    with zipfile.ZipFile(output_path, mode="a") as archive:
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("apps/web/index.html", "<!doctype html>replacement")

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=output_path)
    )

    assert report.valid is False
    assert report.duplicate_entries == ["apps/web/index.html"]


def test_release_package_verifier_rejects_unsafe_archive_entries(tmp_path: Path):
    output_path = tmp_path / "taroai-release.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    with zipfile.ZipFile(output_path, mode="a") as archive:
        archive.writestr("../outside.txt", "path traversal")
        archive.writestr("/absolute.txt", "absolute path")

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=output_path)
    )

    assert report.valid is False
    assert "../outside.txt" in report.unsafe_entries
    assert "/absolute.txt" in report.unsafe_entries


def test_release_package_verifier_rejects_symlink_archive_entries(tmp_path: Path):
    output_path = tmp_path / "taroai-release.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    with zipfile.ZipFile(output_path, mode="a") as archive:
        symlink_info = zipfile.ZipInfo("apps/web/link-to-env")
        symlink_info.external_attr = (0o120777 & 0xFFFF) << 16
        archive.writestr(symlink_info, "../../.env")

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=output_path)
    )

    assert report.valid is False
    assert "apps/web/link-to-env" in report.symlink_entries


def test_release_package_verifier_rejects_non_executable_scripts(tmp_path: Path):
    output_path = tmp_path / "taroai-release.zip"
    tampered_path = tmp_path / "taroai-release-tampered.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    rewrite_zip_entry_mode(
        output_path,
        tampered_path,
        "scripts/validate-install.sh",
        0o644,
    )

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=tampered_path)
    )

    assert report.valid is False
    assert report.non_executable_script_entries == ["scripts/validate-install.sh"]


def test_release_package_verifier_requires_verifier_script(tmp_path: Path):
    output_path = tmp_path / "taroai-release.zip"
    tampered_path = tmp_path / "taroai-release-tampered.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    remove_zip_entry(
        output_path,
        tampered_path,
        "scripts/verify-release-package.sh",
    )

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=tampered_path)
    )

    assert report.valid is False
    assert "scripts/verify-release-package.sh" in report.missing_required_entries


def test_release_package_verifier_requires_local_cloud_poc_verifier_script(
    tmp_path: Path,
):
    output_path = tmp_path / "taroai-release.zip"
    tampered_path = tmp_path / "taroai-release-tampered.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    remove_zip_entry(
        output_path,
        tampered_path,
        "scripts/verify-local-cloud-poc.sh",
    )

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=tampered_path)
    )

    assert report.valid is False
    assert "scripts/verify-local-cloud-poc.sh" in report.missing_required_entries


def test_release_package_verifier_requires_local_cloud_demo_gate_script(
    tmp_path: Path,
):
    output_path = tmp_path / "taroai-release.zip"
    tampered_path = tmp_path / "taroai-release-tampered.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    remove_zip_entry(
        output_path,
        tampered_path,
        "scripts/verify-local-cloud-demo-ready.sh",
    )

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=tampered_path)
    )

    assert report.valid is False
    assert "scripts/verify-local-cloud-demo-ready.sh" in (
        report.missing_required_entries
    )


def test_release_package_verifier_requires_signing_script(tmp_path: Path):
    output_path = tmp_path / "taroai-release.zip"
    tampered_path = tmp_path / "taroai-release-tampered.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    remove_zip_entry(
        output_path,
        tampered_path,
        "scripts/sign-release-package.sh",
    )

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=tampered_path)
    )

    assert report.valid is False
    assert "scripts/sign-release-package.sh" in report.missing_required_entries


def test_release_package_verifier_requires_transfer_evidence_script(tmp_path: Path):
    output_path = tmp_path / "taroai-release.zip"
    tampered_path = tmp_path / "taroai-release-tampered.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    remove_zip_entry(
        output_path,
        tampered_path,
        "scripts/build-release-transfer-evidence.sh",
    )

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=tampered_path)
    )

    assert report.valid is False
    assert "scripts/build-release-transfer-evidence.sh" in report.missing_required_entries


def test_release_package_verifier_requires_support_bundle_redaction_script(
    tmp_path: Path,
):
    output_path = tmp_path / "taroai-release.zip"
    tampered_path = tmp_path / "taroai-release-tampered.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    remove_zip_entry(
        output_path,
        tampered_path,
        "scripts/redact-support-bundle.sh",
    )

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=tampered_path)
    )

    assert report.valid is False
    assert "scripts/redact-support-bundle.sh" in report.missing_required_entries


def test_release_package_verifier_requires_web_dockerfile(tmp_path: Path):
    output_path = tmp_path / "taroai-release.zip"
    tampered_path = tmp_path / "taroai-release-tampered.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    remove_zip_entry(
        output_path,
        tampered_path,
        "apps/web/Dockerfile",
    )

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=tampered_path)
    )

    assert report.valid is False
    assert "apps/web/Dockerfile" in report.missing_required_entries


def test_release_package_verifier_requires_web_workspace_assets(tmp_path: Path):
    output_path = tmp_path / "taroai-release.zip"
    tampered_path = tmp_path / "taroai-release-tampered.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    remove_zip_entry(
        output_path,
        tampered_path,
        "apps/web/assets/main.js",
    )

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=tampered_path)
    )

    assert report.valid is False
    assert "apps/web/assets/main.js" in report.missing_required_entries


def test_release_package_verifier_requires_web_kubernetes_manifest(tmp_path: Path):
    output_path = tmp_path / "taroai-release.zip"
    tampered_path = tmp_path / "taroai-release-tampered.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    remove_zip_entry(
        output_path,
        tampered_path,
        "infra/k8s/web.yaml",
    )

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=tampered_path)
    )

    assert report.valid is False
    assert "infra/k8s/web.yaml" in report.missing_required_entries


def test_release_package_verifier_requires_web_helm_template(tmp_path: Path):
    output_path = tmp_path / "taroai-release.zip"
    tampered_path = tmp_path / "taroai-release-tampered.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    remove_zip_entry(
        output_path,
        tampered_path,
        "infra/helm/taroai/templates/web.yaml",
    )

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=tampered_path)
    )

    assert report.valid is False
    assert "infra/helm/taroai/templates/web.yaml" in report.missing_required_entries


def test_release_package_verifier_requires_helm_values(tmp_path: Path):
    output_path = tmp_path / "taroai-release.zip"
    tampered_path = tmp_path / "taroai-release-tampered.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    remove_zip_entry(
        output_path,
        tampered_path,
        "infra/helm/taroai/values.yaml",
    )

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=tampered_path)
    )

    assert report.valid is False
    assert "infra/helm/taroai/values.yaml" in report.missing_required_entries


def test_release_package_verifier_requires_private_install_runbook(tmp_path: Path):
    output_path = tmp_path / "taroai-release.zip"
    tampered_path = tmp_path / "taroai-release-tampered.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    remove_zip_entry(
        output_path,
        tampered_path,
        "docs/operations/private-install-validation.md",
    )

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=tampered_path)
    )

    assert report.valid is False
    assert "docs/operations/private-install-validation.md" in (
        report.missing_required_entries
    )


def test_release_package_verifier_requires_private_env_profile(tmp_path: Path):
    output_path = tmp_path / "taroai-release.zip"
    tampered_path = tmp_path / "taroai-release-tampered.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    remove_zip_entry(
        output_path,
        tampered_path,
        "infra/config/private.env.example",
    )

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=tampered_path)
    )

    assert report.valid is False
    assert "infra/config/private.env.example" in report.missing_required_entries


def test_release_package_verifier_requires_api_requirements_file(tmp_path: Path):
    output_path = tmp_path / "taroai-release.zip"
    tampered_path = tmp_path / "taroai-release-tampered.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    remove_zip_entry(
        output_path,
        tampered_path,
        "apps/api/requirements.txt",
    )

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=tampered_path)
    )

    assert report.valid is False
    assert "apps/api/requirements.txt" in report.missing_required_entries


def test_release_package_verifier_requires_api_entrypoint(tmp_path: Path):
    output_path = tmp_path / "taroai-release.zip"
    tampered_path = tmp_path / "taroai-release-tampered.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    remove_zip_entry(
        output_path,
        tampered_path,
        "apps/api/entrypoint.sh",
    )

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=tampered_path)
    )

    assert report.valid is False
    assert "apps/api/entrypoint.sh" in report.missing_required_entries


def test_release_package_verifier_requires_local_poc_module(tmp_path: Path):
    output_path = tmp_path / "taroai-release.zip"
    tampered_path = tmp_path / "taroai-release-tampered.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    remove_zip_entry(
        output_path,
        tampered_path,
        "apps/api/src/taroai/deployment/local_cloud_poc_verification.py",
    )

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=tampered_path)
    )

    assert report.valid is False
    assert "apps/api/src/taroai/deployment/local_cloud_poc_verification.py" in (
        report.missing_required_entries
    )


def test_release_package_verifier_requires_browser_controller_module(tmp_path: Path):
    output_path = tmp_path / "taroai-release.zip"
    tampered_path = tmp_path / "taroai-release-tampered.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    remove_zip_entry(
        output_path,
        tampered_path,
        "apps/api/src/taroai/sandbox/playwright_service.py",
    )

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=tampered_path)
    )

    assert report.valid is False
    assert "apps/api/src/taroai/sandbox/playwright_service.py" in (
        report.missing_required_entries
    )


def test_release_package_verifier_requires_top_level_readme(tmp_path: Path):
    output_path = tmp_path / "taroai-release.zip"
    tampered_path = tmp_path / "taroai-release-tampered.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    remove_zip_entry(
        output_path,
        tampered_path,
        "README.md",
    )

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=tampered_path)
    )

    assert report.valid is False
    assert "README.md" in report.missing_required_entries


def test_release_package_verifier_requires_pyproject(tmp_path: Path):
    output_path = tmp_path / "taroai-release.zip"
    tampered_path = tmp_path / "taroai-release-tampered.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    remove_zip_entry(
        output_path,
        tampered_path,
        "pyproject.toml",
    )

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=tampered_path)
    )

    assert report.valid is False
    assert "pyproject.toml" in report.missing_required_entries


def test_release_package_verifier_requires_schema_script(tmp_path: Path):
    output_path = tmp_path / "taroai-release.zip"
    tampered_path = tmp_path / "taroai-release-tampered.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    remove_zip_entry(
        output_path,
        tampered_path,
        "scripts/build-package-schema.sh",
    )

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=tampered_path)
    )

    assert report.valid is False
    assert "scripts/build-package-schema.sh" in report.missing_required_entries


def test_release_package_verifier_requires_manifest_schema(tmp_path: Path):
    output_path = tmp_path / "taroai-release.zip"
    tampered_path = tmp_path / "taroai-release-tampered.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    remove_zip_entry(
        output_path,
        tampered_path,
        "infra/package/manifest.schema.json",
    )

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=tampered_path)
    )

    assert report.valid is False
    assert "infra/package/manifest.schema.json" in report.missing_required_entries


def test_release_package_verifier_rejects_manifest_schema_drift(tmp_path: Path):
    output_path = tmp_path / "taroai-release.zip"
    tampered_path = tmp_path / "taroai-release-tampered.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    with zipfile.ZipFile(output_path) as archive:
        schema = json.loads(archive.read("infra/package/manifest.schema.json"))
    schema["title"] = "OutdatedDeploymentPackageManifest"
    rewrite_zip_entry_content(
        output_path,
        tampered_path,
        "infra/package/manifest.schema.json",
        json.dumps(schema).encode("utf-8"),
    )

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=tampered_path)
    )

    assert report.valid is False
    assert report.manifest_schema_errors == [
        "deployment package schema must match DeploymentPackageManifest"
    ]


def test_release_package_verifier_rejects_stale_upgrade_matrix(tmp_path: Path):
    output_path = tmp_path / "taroai-release.zip"
    tampered_path = tmp_path / "taroai-release-tampered.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    rewrite_zip_entry_content(
        output_path,
        tampered_path,
        "infra/package/upgrade-matrix.md",
        b"| 0.1.0 | 0.1.0 | 001_initial to 011_license_validations |\n",
    )

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=tampered_path)
    )

    assert report.valid is False
    assert report.upgrade_matrix_errors == [
        (
            "upgrade matrix must cover migration range "
            "001_initial to 049_billing_meter_run_index"
        )
    ]


def test_release_package_verifier_rejects_unexpected_image_repository(tmp_path: Path):
    output_path = tmp_path / "taroai-release.zip"
    tampered_path = tmp_path / "taroai-release-tampered.zip"
    build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=output_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    with zipfile.ZipFile(output_path) as archive:
        manifest = json.loads(archive.read("infra/package/manifest.json"))
    manifest["images"][0]["repository"] = "registry.example.com/taroai-api"
    rewrite_zip_entry_content(
        output_path,
        tampered_path,
        "infra/package/manifest.json",
        json.dumps(manifest).encode("utf-8"),
    )

    report = verify_release_package(
        ReleasePackageVerificationConfig(package_path=tampered_path)
    )

    assert report.valid is False
    assert report.manifest_image_errors == [
        "deployment image api repository must be ghcr.io/creao-ai/taroai-api"
    ]


def test_verify_release_package_script_wraps_python_cli():
    script = Path("scripts/verify-release-package.sh")

    text = script.read_text()

    assert "python -m taroai.deployment.release_package" in text
    assert "--verify" in text
    assert "--signature" in text
    assert "--trusted-public-key" in text


def test_verify_local_cloud_poc_script_wraps_python_cli():
    script = Path("scripts/verify-local-cloud-poc.sh")

    text = script.read_text()

    assert "python -m taroai.deployment.local_cloud_poc_verification" in text
    assert "PYTHONPATH" in text
    assert "--require-model-execution" in text


def test_verify_local_cloud_demo_ready_script_wraps_python_cli():
    script = Path("scripts/verify-local-cloud-demo-ready.sh")

    text = script.read_text()

    assert "python -m taroai.deployment.local_cloud_poc_demo_gate" in text
    assert "PYTHONPATH" in text
    assert "--require-workspace-execution" in text
    assert "--require-skill-reuse" in text
    assert "--require-browser-controller-governance" in text
    assert "--require-sandbox-governance" in text


def test_verify_compose_strict_e2e_script_runs_compose_and_strict_verifier():
    script = Path("scripts/verify-compose-strict-e2e.sh")

    text = script.read_text()

    assert "docker compose" in text
    assert "infra/docker-compose.yml" in text
    assert "scripts/verify-local-cloud-poc.sh" in text
    assert "--require-model-execution" in text
    assert "--browser-workspace-url" in text
    assert "--browser-workspace-api-base-url" in text
    assert (
        'TAROAI_BROWSER_CONTROLLER_API_KEY="${TAROAI_BROWSER_CONTROLLER_API_KEY:-local_browser_controller_key_2026_dev_only}"'
        in text
    )
    assert '--browser-controller-api-key "$TAROAI_BROWSER_CONTROLLER_API_KEY"' in text
    assert "TAROAI_MODEL_GATEWAY_API_KEY" in text
    assert "TAROAI_COMPOSE_STRICT_E2E_OUTPUT" in text
    assert '--output "$STRICT_E2E_OUTPUT"' in text
    assert "TAROAI_COMPOSE_STRICT_E2E_DEMO_GATE_OUTPUT" in text
    assert '--output "$STRICT_E2E_DEMO_GATE_OUTPUT"' in text
    assert "TAROAI_COMPOSE_STRICT_E2E_REQUIRE_SANDBOX_GOVERNANCE" in text
    assert "--require-sandbox-governance" in text
    assert "verify-local-cloud-demo-ready.sh" in text
    assert "--require-workspace-execution" in text
    assert "--require-skill-reuse" in text
    assert "--require-browser-controller-governance" in text
    assert "down --remove-orphans" in text


def test_verify_compose_strict_e2e_can_emit_install_validation_runtime_evidence():
    script = Path("scripts/verify-compose-strict-e2e.sh")

    text = script.read_text()

    assert "TAROAI_COMPOSE_STRICT_E2E_INSTALL_VALIDATION_OUTPUT" in text
    assert "TAROAI_COMPOSE_STRICT_E2E_EVENT_STREAM_OUTPUT" in text
    assert "TAROAI_COMPOSE_STRICT_E2E_AUDIT_WRITE_OUTPUT" in text
    assert "TAROAI_COMPOSE_STRICT_E2E_SANDBOX_OUTPUT" in text
    assert "TAROAI_COMPOSE_STRICT_E2E_BROWSER_CONTROLLER_OUTPUT" in text
    assert "TAROAI_COMPOSE_STRICT_E2E_MODEL_GATEWAY_OUTPUT" in text
    assert "TAROAI_COMPOSE_STRICT_E2E_OBJECT_STORAGE_OUTPUT" in text
    assert "TAROAI_COMPOSE_STRICT_E2E_REDIS_QUEUE_OUTPUT" in text
    assert "TAROAI_COMPOSE_STRICT_E2E_MIGRATION_PLAN_OUTPUT" in text
    assert "TAROAI_COMPOSE_STRICT_E2E_SUPPORT_BUNDLE_OUTPUT" in text
    assert "TAROAI_COMPOSE_STRICT_E2E_SUPPORT_BUNDLE_REDACTED_OUTPUT" in text
    assert "TAROAI_COMPOSE_STRICT_E2E_SUPPORT_BUNDLE_REDACTION_OUTPUT" in text
    assert "TAROAI_COMPOSE_STRICT_E2E_RELEASE_PACKAGE_OUTPUT" in text
    assert "TAROAI_COMPOSE_STRICT_E2E_RELEASE_PACKAGE_SIGNATURE_OUTPUT" in text
    assert "TAROAI_COMPOSE_STRICT_E2E_RELEASE_TRANSFER_OUTPUT" in text
    assert "TAROAI_COMPOSE_STRICT_E2E_RELEASE_SIGNING_OUTPUT" in text
    assert "TAROAI_COMPOSE_STRICT_E2E_SECRET_MANAGER_VERIFICATION" in text
    assert "TAROAI_COMPOSE_STRICT_E2E_TRACE_COLLECTOR_VERIFICATION" in text
    assert "TAROAI_COMPOSE_STRICT_E2E_RESTORE_DRILL_VERIFICATION" in text
    assert "optional_evidence_arg" in text
    assert "TAROAI_COMPOSE_STRICT_E2E_DATABASE_URL" in text
    assert "scripts/validate-install.sh" in text
    assert "scripts/build-release-package.sh" in text
    assert "scripts/sign-release-package.sh" in text
    assert "scripts/build-release-transfer-evidence.sh" in text
    assert "scripts/build-migration-plan.sh" in text
    assert "scripts/redact-support-bundle.sh" in text
    assert "scripts/verify-event-stream.sh" in text
    assert "scripts/verify-audit-write.sh" in text
    assert "scripts/verify-sandbox-lifecycle.sh" in text
    assert "scripts/verify-browser-controller.sh" in text
    assert "scripts/verify-model-gateway.sh" in text
    assert "scripts/verify-object-storage.sh" in text
    assert "scripts/verify-redis-queue.sh" in text
    assert 'STRICT_E2E_RUN_ID=$(json_field "$STRICT_E2E_OUTPUT" run_id)' in text
    assert '--run-id "$STRICT_E2E_RUN_ID"' in text
    assert '--model-gateway-verification "$STRICT_E2E_MODEL_GATEWAY_OUTPUT"' in text
    assert '--object-storage-verification "$STRICT_E2E_OBJECT_STORAGE_OUTPUT"' in text
    assert '--redis-queue-verification "$STRICT_E2E_REDIS_QUEUE_OUTPUT"' in text
    assert '--migration-plan "$STRICT_E2E_MIGRATION_PLAN_OUTPUT"' in text
    assert '--release-transfer-evidence "$STRICT_E2E_RELEASE_TRANSFER_OUTPUT"' in text
    assert (
        '--support-bundle-redaction-evidence "$STRICT_E2E_SUPPORT_BUNDLE_REDACTION_OUTPUT"'
        in text
    )
    assert '--secret-manager-verification "$STRICT_E2E_SECRET_MANAGER_VERIFICATION"' in text
    assert '--trace-collector-verification "$STRICT_E2E_TRACE_COLLECTOR_VERIFICATION"' in text
    assert '--restore-drill-verification "$STRICT_E2E_RESTORE_DRILL_VERIFICATION"' in text
    assert '--event-stream-verification "$STRICT_E2E_EVENT_STREAM_OUTPUT"' in text
    assert '--audit-write-verification "$STRICT_E2E_AUDIT_WRITE_OUTPUT"' in text
    assert '--sandbox-verification "$STRICT_E2E_SANDBOX_OUTPUT"' in text
    assert '--browser-controller-verification "$STRICT_E2E_BROWSER_CONTROLLER_OUTPUT"' in text
    assert '--runtime-closed-loop-evidence "$STRICT_E2E_DEMO_GATE_OUTPUT"' in text
    assert '--output "$STRICT_E2E_INSTALL_VALIDATION_OUTPUT"' in text
    assert "TAROAI_RUNTIME_CLOSED_LOOP_EVIDENCE_PATH" in text


@pytest.mark.skipif(os.name == "nt", reason="strict Compose shell gate requires POSIX executable semantics")
def test_verify_compose_strict_e2e_requires_model_secret_when_env_file_is_used(
    tmp_path: Path,
):
    docker_bin = tmp_path / "docker"
    docker_bin.write_text(
        "#!/usr/bin/env sh\n"
        "printf '%s\\n' 'docker should not run before model credentials are validated' >&2\n"
        "exit 99\n"
    )
    docker_bin.chmod(docker_bin.stat().st_mode | stat.S_IXUSR)
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "TAROAI_COMPOSE_ENV_FILE": "infra/config/deepseek.env.example",
    }
    for key in [
        "TAROAI_MODEL_GATEWAY_API_KEY",
        "TAROAI_MODEL_GATEWAY_API_KEY_SECRET_REF_ID",
        "TAROAI_MODEL_GATEWAY_PROVIDERS",
    ]:
        env.pop(key, None)

    result = subprocess.run(
        ["scripts/verify-compose-strict-e2e.sh"],
        cwd=Path("."),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "strict Compose E2E host model verification requires" in result.stderr
    assert "docker should not run" not in result.stderr


@pytest.mark.skipif(os.name == "nt", reason="strict Compose shell gate requires POSIX executable semantics")
def test_verify_compose_strict_e2e_rejects_secret_ref_only_model_config(
    tmp_path: Path,
):
    docker_bin = tmp_path / "docker"
    docker_bin.write_text(
        "#!/usr/bin/env sh\n"
        "printf '%s\\n' 'docker should not run before host model verification is possible' >&2\n"
        "exit 99\n"
    )
    docker_bin.chmod(docker_bin.stat().st_mode | stat.S_IXUSR)
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "TAROAI_MODEL_GATEWAY_API_KEY_SECRET_REF_ID": "secret_model_key",
        "TAROAI_MODEL_GATEWAY_MODEL": "deepseek-v4-flash",
    }
    env.pop("TAROAI_MODEL_GATEWAY_API_KEY", None)
    env.pop("TAROAI_MODEL_GATEWAY_PROVIDERS", None)

    result = subprocess.run(
        ["scripts/verify-compose-strict-e2e.sh"],
        cwd=Path("."),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "strict Compose E2E host model verification requires" in result.stderr
    assert "docker should not run" not in result.stderr


@pytest.mark.skipif(os.name == "nt", reason="strict Compose shell gate requires POSIX executable semantics")
def test_verify_compose_strict_e2e_rejects_provider_secret_ref_without_host_value(
    tmp_path: Path,
):
    docker_bin = tmp_path / "docker"
    docker_bin.write_text(
        "#!/usr/bin/env sh\n"
        "printf '%s\\n' 'docker should not run before provider credentials are validated' >&2\n"
        "exit 99\n"
    )
    docker_bin.chmod(docker_bin.stat().st_mode | stat.S_IXUSR)
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "TAROAI_MODEL_GATEWAY_PROVIDERS": (
            '[{"id":"sales-openai","base_url":"https://model.example.com/v1",'
            '"api_key_secret_ref_id":"secret_sales_model_key",'
            '"default_model":"gpt-4.1"}]'
        ),
    }
    for key in [
        "TAROAI_MODEL_GATEWAY_API_KEY",
        "TAROAI_MODEL_GATEWAY_API_KEY_SECRET_REF_ID",
        "TAROAI_MODEL_GATEWAY_VERIFICATION_SECRET_VALUES",
        "TAROAI_MODEL_GATEWAY_VERIFICATION_SECRET_VALUE_ENV_JSON",
    ]:
        env.pop(key, None)

    result = subprocess.run(
        ["scripts/verify-compose-strict-e2e.sh"],
        cwd=Path("."),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "host model verification config is invalid" in result.stderr
    assert "verification secret value is not configured" in result.stderr
    assert "docker should not run" not in result.stderr
