import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from taroai.deployment.models import (
    ConfigKeySource,
    DeploymentCompatibilityRule,
    DeploymentConfigKey,
    DeploymentDependencyVersion,
    DeploymentImage,
    DeploymentMigration,
    DeploymentPackageManifest,
    DeploymentTarget,
    RequiredDeploymentService,
)


class DeploymentPackageManifestBuildConfig(BaseModel):
    package_version: str = Field(min_length=1)
    app_version: str = Field(min_length=1)
    image_tag: str = Field(default="latest", min_length=1)
    api_repository: str = Field(default="ghcr.io/creao-ai/taroai-api", min_length=1)
    browser_controller_repository: str = Field(
        default="ghcr.io/creao-ai/taroai-browser-controller",
        min_length=1,
    )
    sandbox_controller_repository: str = Field(
        default="ghcr.io/creao-ai/taroai-sandbox-controller",
        min_length=1,
    )
    web_repository: str = Field(default="ghcr.io/creao-ai/taroai-web", min_length=1)
    repository_root: Path = Path(".")
    migrations_path: Path = Path("apps/api/migrations")
    env_example_path: Path = Path(".env.example")
    k8s_secret_example_path: Path = Path("infra/k8s/secrets.example.yaml")
    output_path: str | None = None
    minimum_kubernetes_version: str = Field(default="1.26.0", min_length=1)
    maximum_kubernetes_version: str = Field(default="1.32.99", min_length=1)

    @model_validator(mode="after")
    def normalize_paths(self) -> "DeploymentPackageManifestBuildConfig":
        self.repository_root = self.repository_root.resolve()
        return self


class DeploymentPackageSchemaBuildConfig(BaseModel):
    output_path: str | None = None


def build_deployment_package_manifest(
    config: DeploymentPackageManifestBuildConfig,
) -> DeploymentPackageManifest:
    return DeploymentPackageManifest(
        package_version=config.package_version,
        app_version=config.app_version,
        targets=[target for target in DeploymentTarget],
        images=build_deployment_images(config),
        migrations=build_deployment_migrations(config),
        config_keys=build_deployment_config_keys(config),
        dependency_versions=build_dependency_versions(),
        required_services=[service for service in RequiredDeploymentService],
        compatibility_matrix=[
            DeploymentCompatibilityRule(
                component="kubernetes",
                min_version=config.minimum_kubernetes_version,
                max_version=config.maximum_kubernetes_version,
            )
        ],
    )


def build_deployment_images(
    config: DeploymentPackageManifestBuildConfig,
) -> list[DeploymentImage]:
    return [
        DeploymentImage(
            name="api",
            repository=config.api_repository,
            tag=config.image_tag,
        ),
        DeploymentImage(
            name="worker",
            repository=config.api_repository,
            tag=config.image_tag,
        ),
        DeploymentImage(
            name="browser-controller",
            repository=config.browser_controller_repository,
            tag=config.image_tag,
        ),
        DeploymentImage(
            name="sandbox-controller",
            repository=config.sandbox_controller_repository,
            tag=config.image_tag,
        ),
        DeploymentImage(
            name="web",
            repository=config.web_repository,
            tag=config.image_tag,
        ),
    ]


def build_deployment_migrations(
    config: DeploymentPackageManifestBuildConfig,
) -> list[DeploymentMigration]:
    migrations_dir = resolve_repo_path(config.repository_root, config.migrations_path)
    reject_symlink_source(migrations_dir, "migration")
    migrations: list[DeploymentMigration] = []
    for migration_path in sorted(migrations_dir.glob("*.sql")):
        reject_symlink_source(migration_path, "migration")
        relative_path = migration_path.relative_to(config.repository_root).as_posix()
        migrations.append(
            DeploymentMigration(
                id=migration_path.stem,
                path=relative_path,
                checksum_sha256=sha256_file(migration_path),
                from_app_version="0.0.0",
                to_app_version=config.app_version,
            )
        )
    if migrations == []:
        raise ValueError(f"no migration files found under {migrations_dir}")
    return migrations


def build_deployment_config_keys(
    config: DeploymentPackageManifestBuildConfig,
) -> list[DeploymentConfigKey]:
    env_keys = parse_env_example_keys(
        resolve_repo_path(config.repository_root, config.env_example_path)
    )
    secret_keys = parse_k8s_secret_example_keys(
        resolve_repo_path(config.repository_root, config.k8s_secret_example_path)
    )
    config_keys: list[DeploymentConfigKey] = []
    for key in sorted(env_keys | secret_keys):
        source = (
            ConfigKeySource.SECRET
            if key in secret_keys
            else ConfigKeySource.CONFIG
        )
        config_keys.append(
            DeploymentConfigKey(
                name=key,
                source=source,
                required=True,
                description="generated from repository deployment configuration",
            )
        )
    return config_keys


def build_dependency_versions() -> list[DeploymentDependencyVersion]:
    return [
        DeploymentDependencyVersion(name="postgresql", version="16"),
        DeploymentDependencyVersion(name="redis", version="7"),
        DeploymentDependencyVersion(name="kubernetes", version="1.26"),
        DeploymentDependencyVersion(name="minio", version="s3-compatible"),
    ]


def parse_env_example_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    reject_symlink_source(path, "config")
    if not path.exists():
        return keys
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if key:
            keys.add(key)
    return keys


def parse_k8s_secret_example_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    reject_symlink_source(path, "config")
    if not path.exists():
        return keys
    inside_string_data = False
    for raw_line in path.read_text().splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped == "stringData:":
            inside_string_data = True
            continue
        if not inside_string_data:
            continue
        if line and not line.startswith(" "):
            inside_string_data = False
            continue
        if ":" not in stripped or stripped.startswith("#"):
            continue
        key = stripped.split(":", 1)[0].strip()
        if key:
            keys.add(key)
    return keys


def reject_symlink_source(path: Path, source_type: str) -> None:
    if path.is_symlink():
        raise ValueError(f"{source_type} source must not be a symlink: {path}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_repo_path(repository_root: Path, path: Path) -> Path:
    if path.is_absolute():
        return path
    return repository_root / path


def manifest_json(manifest: DeploymentPackageManifest) -> str:
    return json.dumps(
        manifest.model_dump(mode="json"),
        indent=2,
        sort_keys=True,
    )


def manifest_schema_json() -> str:
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **DeploymentPackageManifest.model_json_schema(mode="validation"),
    }
    return json.dumps(
        schema,
        indent=2,
        sort_keys=True,
    )


def parse_args(
    argv: list[str] | None = None,
) -> DeploymentPackageManifestBuildConfig | DeploymentPackageSchemaBuildConfig:
    parser = argparse.ArgumentParser(
        description="Build the Taroai deployment package manifest."
    )
    parser.add_argument("--schema", action="store_true")
    parser.add_argument("--package-version", default=os.environ.get("TAROAI_PACKAGE_VERSION", "0.1.0"))
    parser.add_argument("--app-version", default=os.environ.get("TAROAI_APP_VERSION", "0.1.0"))
    parser.add_argument("--image-tag", default=os.environ.get("TAROAI_IMAGE_TAG", "0.1.0"))
    parser.add_argument("--api-repository", default="ghcr.io/creao-ai/taroai-api")
    parser.add_argument(
        "--browser-controller-repository",
        default="ghcr.io/creao-ai/taroai-browser-controller",
    )
    parser.add_argument(
        "--sandbox-controller-repository",
        default="ghcr.io/creao-ai/taroai-sandbox-controller",
    )
    parser.add_argument("--web-repository", default="ghcr.io/creao-ai/taroai-web")
    parser.add_argument("--repository-root", default=".")
    parser.add_argument("--output", default=None)
    parsed = parser.parse_args(argv)
    if parsed.schema:
        return DeploymentPackageSchemaBuildConfig(output_path=parsed.output)
    return DeploymentPackageManifestBuildConfig(
        package_version=parsed.package_version,
        app_version=parsed.app_version,
        image_tag=parsed.image_tag,
        api_repository=parsed.api_repository,
        browser_controller_repository=parsed.browser_controller_repository,
        sandbox_controller_repository=parsed.sandbox_controller_repository,
        web_repository=parsed.web_repository,
        repository_root=Path(parsed.repository_root),
        output_path=parsed.output,
    )


def main(argv: list[str] | None = None) -> int:
    config = parse_args(argv)
    if isinstance(config, DeploymentPackageSchemaBuildConfig):
        output = manifest_schema_json() + "\n"
        if config.output_path:
            write_cli_output(Path(config.output_path), output)
        else:
            print(output, end="")
        return 0

    manifest = build_deployment_package_manifest(config)
    output = manifest_json(manifest) + "\n"
    if config.output_path:
        write_cli_output(Path(config.output_path), output)
    else:
        print(output, end="")
    return 0


def write_cli_output(output_path: Path, output: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            delete=False,
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
        ) as temp_file:
            temp_path = Path(temp_file.name)
        temp_path.write_text(output, encoding="utf-8")
        os.replace(temp_path, output_path)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
