import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from taroai.deployment import DeploymentPackageManifest, RequiredDeploymentService
from taroai.deployment.package_manifest import (
    DeploymentPackageManifestBuildConfig,
    build_deployment_package_manifest,
    main,
)


def valid_manifest_payload() -> dict:
    return {
        "package_version": "0.1.0",
        "app_version": "0.1.0",
        "targets": ["cloud", "byoc", "vpc", "private", "air_gapped"],
        "images": [
            {
                "name": "api",
                "repository": "ghcr.io/creao-ai/taroai-api",
                "tag": "0.1.0",
            },
            {
                "name": "worker",
                "repository": "ghcr.io/creao-ai/taroai-api",
                "tag": "0.1.0",
            },
            {
                "name": "browser-controller",
                "repository": "ghcr.io/creao-ai/taroai-browser-controller",
                "tag": "0.1.0",
            },
            {
                "name": "sandbox-controller",
                "repository": "ghcr.io/creao-ai/taroai-sandbox-controller",
                "tag": "0.1.0",
            },
            {
                "name": "web",
                "repository": "ghcr.io/creao-ai/taroai-web",
                "tag": "0.1.0",
            },
        ],
        "migrations": [
            {
                "id": "001_initial",
                "path": "apps/api/migrations/001_initial.sql",
                "checksum_sha256": "a" * 64,
                "from_app_version": "0.0.0",
                "to_app_version": "0.1.0",
            }
        ],
        "config_keys": [
            {
                "name": "TAROAI_DATABASE_URL",
                "source": "secret",
                "required": True,
            },
            {
                "name": "TAROAI_CONTROL_PLANE_STORE_BACKEND",
                "source": "config",
                "required": True,
            },
        ],
        "dependency_versions": [
            {"name": "postgresql", "version": "16"},
            {"name": "redis", "version": "7"},
            {"name": "kubernetes", "version": "1.26"},
        ],
        "required_services": [
            "api",
            "worker",
            "database",
            "redis",
            "object_storage",
            "sandbox_provider",
            "browser_controller",
            "web_workspace",
            "model_gateway",
            "secrets_manager",
        ],
        "compatibility_matrix": [
            {
                "component": "kubernetes",
                "min_version": "1.26.0",
                "max_version": "1.32.99",
            }
        ],
    }


def test_deployment_package_manifest_accepts_complete_private_package_contract():
    manifest = DeploymentPackageManifest.model_validate(valid_manifest_payload())

    assert manifest.package_version == "0.1.0"
    assert manifest.targets == ["cloud", "byoc", "vpc", "private", "air_gapped"]
    assert [image.name for image in manifest.images] == [
        "api",
        "worker",
        "browser-controller",
        "sandbox-controller",
        "web",
    ]
    assert set(manifest.required_services) == set(RequiredDeploymentService)
    assert manifest.config_keys[0].source == "secret"


def test_deployment_package_manifest_requires_api_and_worker_images():
    payload = valid_manifest_payload()
    payload["images"] = [
        image for image in payload["images"] if image["name"] != "sandbox-controller"
    ]

    with pytest.raises(ValidationError) as error:
        DeploymentPackageManifest.model_validate(payload)

    assert "deployment package images must include: ['sandbox-controller']" in str(
        error.value
    )


def test_deployment_package_manifest_rejects_incompatible_migration_range():
    payload = valid_manifest_payload()
    payload["migrations"][0]["from_app_version"] = "0.2.0"

    with pytest.raises(ValidationError) as error:
        DeploymentPackageManifest.model_validate(payload)

    assert "migration from_app_version must not exceed to_app_version" in str(error.value)


def test_deployment_package_manifest_rejects_duplicate_required_services():
    payload = valid_manifest_payload()
    payload["required_services"].append("api")

    with pytest.raises(ValidationError) as error:
        DeploymentPackageManifest.model_validate(payload)

    assert "required_services entries must be unique" in str(error.value)


def test_deployment_package_manifest_rejects_unsupported_target():
    payload = valid_manifest_payload()
    payload["targets"] = ["desktop"]

    with pytest.raises(ValidationError) as error:
        DeploymentPackageManifest.model_validate(payload)

    assert "Input should be" in str(error.value)


def test_deployment_package_manifest_schema_and_readme_are_committed():
    schema_path = Path("infra/package/manifest.schema.json")
    readme_path = Path("infra/package/README.md")

    schema = json.loads(schema_path.read_text())
    readme = readme_path.read_text()

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["title"] == "DeploymentPackageManifest"
    assert "package_version" in schema["properties"]
    assert "browser_controller" in json.dumps(schema)
    assert "required_services" in schema["required"]
    assert "api" in readme
    assert "worker" in readme
    assert "browser controller" in readme
    assert "sandbox controller" in readme
    assert "taroai-web" in readme
    assert "Web Workspace image" in readme
    assert "web workspace" in readme
    assert "secrets manager" in readme
    assert "--signature" in readme
    assert "trusted release package public key" in readme


def test_deployment_package_manifest_schema_matches_pydantic_model():
    schema_path = Path("infra/package/manifest.schema.json")
    committed_schema = json.loads(schema_path.read_text())
    generated_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **DeploymentPackageManifest.model_json_schema(mode="validation"),
    }

    assert committed_schema == generated_schema


def test_private_upgrade_matrix_covers_current_migration_range():
    matrix = Path("infra/package/upgrade-matrix.md").read_text()

    assert (
        "001_initial to 049_billing_meter_run_index"
        in matrix
    )
    assert "model policy version history" in matrix
    assert "browser controller" in matrix
    assert "taroai-browser-controller" in matrix
    assert "Web Workspace" in matrix
    assert "taroai-web" in matrix


def test_deployment_package_manifest_builder_generates_release_artifact_contract():
    manifest = build_deployment_package_manifest(
        DeploymentPackageManifestBuildConfig(
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
            repository_root=Path("."),
        )
    )

    images = {image.name: image for image in manifest.images}
    migrations = {migration.id: migration for migration in manifest.migrations}
    config_keys = {key.name: key for key in manifest.config_keys}

    assert set(images) == {
        "api",
        "worker",
        "browser-controller",
        "sandbox-controller",
        "web",
    }
    assert images["api"].repository == "ghcr.io/creao-ai/taroai-api"
    assert images["worker"].repository == "ghcr.io/creao-ai/taroai-api"
    assert images["browser-controller"].repository == (
        "ghcr.io/creao-ai/taroai-browser-controller"
    )
    assert images["sandbox-controller"].repository == (
        "ghcr.io/creao-ai/taroai-sandbox-controller"
    )
    assert images["web"].repository == "ghcr.io/creao-ai/taroai-web"
    assert all(image.tag == "0.1.0" for image in images.values())

    assert "001_initial" in migrations
    assert "023_restore_drill_schedule_store" in migrations
    assert migrations["023_restore_drill_schedule_store"].path == (
        "apps/api/migrations/023_restore_drill_schedule_store.sql"
    )
    assert re.fullmatch(
        r"[a-f0-9]{64}",
        migrations["023_restore_drill_schedule_store"].checksum_sha256,
    )

    assert config_keys["TAROAI_DATABASE_URL"].source == "secret"
    assert config_keys["TAROAI_SANDBOX_CONTROLLER_API_KEY"].source == "secret"
    assert config_keys["TAROAI_BROWSER_CONTROLLER_API_KEY"].source == "secret"
    assert config_keys["TAROAI_BROWSER_CONTROLLER_BASE_URL"].source == "config"
    assert set(manifest.required_services) == set(RequiredDeploymentService)


def test_deployment_package_manifest_builder_rejects_symlink_migration_sources(
    tmp_path: Path,
):
    repository_root = tmp_path / "repo"
    migrations_dir = repository_root / "apps/api/migrations"
    migrations_dir.mkdir(parents=True)
    outside_migration = tmp_path / "outside_001_initial.sql"
    outside_migration.write_text("create table outside_release_check(id text);\n")
    (migrations_dir / "001_initial.sql").symlink_to(outside_migration)

    with pytest.raises(ValueError, match="migration source must not be a symlink"):
        build_deployment_package_manifest(
            DeploymentPackageManifestBuildConfig(
                package_version="0.1.0",
                app_version="0.1.0",
                image_tag="0.1.0",
                repository_root=repository_root,
            )
        )


def test_deployment_package_manifest_builder_rejects_symlink_migration_directory(
    tmp_path: Path,
):
    repository_root = tmp_path / "repo"
    app_dir = repository_root / "apps/api"
    outside_migrations_dir = tmp_path / "outside-migrations"
    app_dir.mkdir(parents=True)
    outside_migrations_dir.mkdir()
    (outside_migrations_dir / "001_initial.sql").write_text(
        "create table outside_release_check(id text);\n"
    )
    (app_dir / "migrations").symlink_to(outside_migrations_dir, target_is_directory=True)

    with pytest.raises(ValueError, match="migration source must not be a symlink"):
        build_deployment_package_manifest(
            DeploymentPackageManifestBuildConfig(
                package_version="0.1.0",
                app_version="0.1.0",
                image_tag="0.1.0",
                repository_root=repository_root,
            )
        )


def test_deployment_package_manifest_builder_rejects_broken_migration_directory_symlink(
    tmp_path: Path,
):
    repository_root = tmp_path / "repo"
    app_dir = repository_root / "apps/api"
    app_dir.mkdir(parents=True)
    (app_dir / "migrations").symlink_to(
        tmp_path / "missing-migrations",
        target_is_directory=True,
    )

    with pytest.raises(ValueError, match="migration source must not be a symlink"):
        build_deployment_package_manifest(
            DeploymentPackageManifestBuildConfig(
                package_version="0.1.0",
                app_version="0.1.0",
                image_tag="0.1.0",
                repository_root=repository_root,
            )
        )


def test_deployment_package_manifest_builder_rejects_symlink_config_sources(
    tmp_path: Path,
):
    repository_root = tmp_path / "repo"
    migrations_dir = repository_root / "apps/api/migrations"
    migrations_dir.mkdir(parents=True)
    (migrations_dir / "001_initial.sql").write_text("create table release_check(id text);\n")
    outside_env = tmp_path / "outside.env.example"
    outside_env.write_text("TAROAI_DATABASE_URL=postgresql://operator-local\n")
    (repository_root / ".env.example").symlink_to(outside_env)

    with pytest.raises(ValueError, match="config source must not be a symlink"):
        build_deployment_package_manifest(
            DeploymentPackageManifestBuildConfig(
                package_version="0.1.0",
                app_version="0.1.0",
                image_tag="0.1.0",
                repository_root=repository_root,
            )
        )


def test_deployment_package_manifest_builder_rejects_broken_env_symlink_source(
    tmp_path: Path,
):
    repository_root = tmp_path / "repo"
    migrations_dir = repository_root / "apps/api/migrations"
    migrations_dir.mkdir(parents=True)
    (migrations_dir / "001_initial.sql").write_text("create table release_check(id text);\n")
    (repository_root / ".env.example").symlink_to(tmp_path / "missing.env.example")

    with pytest.raises(ValueError, match="config source must not be a symlink"):
        build_deployment_package_manifest(
            DeploymentPackageManifestBuildConfig(
                package_version="0.1.0",
                app_version="0.1.0",
                image_tag="0.1.0",
                repository_root=repository_root,
            )
        )


def test_deployment_package_manifest_builder_rejects_broken_secret_symlink_source(
    tmp_path: Path,
):
    repository_root = tmp_path / "repo"
    migrations_dir = repository_root / "apps/api/migrations"
    secrets_dir = repository_root / "infra/k8s"
    migrations_dir.mkdir(parents=True)
    secrets_dir.mkdir(parents=True)
    (migrations_dir / "001_initial.sql").write_text("create table release_check(id text);\n")
    (repository_root / ".env.example").write_text("TAROAI_ENVIRONMENT=local\n")
    (secrets_dir / "secrets.example.yaml").symlink_to(
        tmp_path / "missing-secrets.example.yaml"
    )

    with pytest.raises(ValueError, match="config source must not be a symlink"):
        build_deployment_package_manifest(
            DeploymentPackageManifestBuildConfig(
                package_version="0.1.0",
                app_version="0.1.0",
                image_tag="0.1.0",
                repository_root=repository_root,
            )
        )


def test_build_package_manifest_script_wraps_python_cli():
    script = Path("scripts/build-package-manifest.sh")

    text = script.read_text()

    assert "python -m taroai.deployment.package_manifest" in text
    assert "--output" in text


def test_package_manifest_cli_writes_schema_from_pydantic_model(tmp_path: Path):
    output_path = tmp_path / "manifest.schema.json"

    exit_code = main(["--schema", "--output", str(output_path)])

    schema = json.loads(output_path.read_text())
    assert exit_code == 0
    assert schema == {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **DeploymentPackageManifest.model_json_schema(mode="validation"),
    }


def test_package_manifest_cli_preserves_existing_output_when_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    output_path = tmp_path / "manifest.schema.json"
    original_schema = '{"existing": true}\n'
    output_path.write_text(original_schema, encoding="utf-8")

    original_write_text = Path.write_text

    def failing_write_text(self, data, *args, **kwargs):
        if self.parent == tmp_path and output_path.name in self.name:
            original_write_text(self, '{"partial": ', *args, **kwargs)
            raise OSError("manifest write failed")
        return original_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", failing_write_text)

    with pytest.raises(OSError, match="manifest write failed"):
        main(["--schema", "--output", str(output_path)])

    assert output_path.read_text(encoding="utf-8") == original_schema
    assert not list(tmp_path.glob(f".{output_path.name}*.tmp"))


def test_build_package_schema_script_wraps_python_cli():
    script = Path("scripts/build-package-schema.sh")

    text = script.read_text()

    assert "python -m taroai.deployment.package_manifest" in text
    assert "--schema" in text
    assert "--output" in text
