from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DeploymentTarget(str, Enum):
    CLOUD = "cloud"
    BYOC = "byoc"
    VPC = "vpc"
    PRIVATE = "private"
    AIR_GAPPED = "air_gapped"


class RequiredDeploymentService(str, Enum):
    API = "api"
    WORKER = "worker"
    DATABASE = "database"
    REDIS = "redis"
    OBJECT_STORAGE = "object_storage"
    SANDBOX_PROVIDER = "sandbox_provider"
    BROWSER_CONTROLLER = "browser_controller"
    WEB_WORKSPACE = "web_workspace"
    MODEL_GATEWAY = "model_gateway"
    SECRETS_MANAGER = "secrets_manager"


class ConfigKeySource(str, Enum):
    CONFIG = "config"
    SECRET = "secret"


class StrictManifestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeploymentImage(StrictManifestModel):
    name: str = Field(min_length=1, pattern=r"^[a-z0-9_.-]+$")
    repository: str = Field(min_length=1)
    tag: str | None = Field(default=None, min_length=1)
    digest: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_tag_or_digest(self) -> "DeploymentImage":
        if self.tag is None and self.digest is None:
            raise ValueError("deployment image must include tag or digest")
        return self


class DeploymentMigration(StrictManifestModel):
    id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_.-]+$")
    path: str = Field(min_length=1)
    checksum_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    from_app_version: str = Field(min_length=1)
    to_app_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_version_range(self) -> "DeploymentMigration":
        if _version_tuple(self.from_app_version) > _version_tuple(self.to_app_version):
            raise ValueError("migration from_app_version must not exceed to_app_version")
        return self


class DeploymentConfigKey(StrictManifestModel):
    name: str = Field(min_length=1, pattern=r"^[A-Z][A-Z0-9_]*$")
    source: ConfigKeySource
    required: bool = True
    description: str = ""


class DeploymentDependencyVersion(StrictManifestModel):
    name: str = Field(min_length=1, pattern=r"^[a-z0-9_.-]+$")
    version: str = Field(min_length=1)


class DeploymentCompatibilityRule(StrictManifestModel):
    component: str = Field(min_length=1, pattern=r"^[a-z0-9_.-]+$")
    min_version: str = Field(min_length=1)
    max_version: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_compatibility_range(self) -> "DeploymentCompatibilityRule":
        if self.max_version is not None and _version_tuple(self.min_version) > _version_tuple(
            self.max_version
        ):
            raise ValueError("compatibility min_version must not exceed max_version")
        return self


class DeploymentPackageManifest(StrictManifestModel):
    package_version: str = Field(min_length=1)
    app_version: str = Field(min_length=1)
    targets: list[DeploymentTarget] = Field(min_length=1)
    images: list[DeploymentImage] = Field(min_length=1)
    migrations: list[DeploymentMigration] = Field(default_factory=list)
    config_keys: list[DeploymentConfigKey] = Field(default_factory=list)
    dependency_versions: list[DeploymentDependencyVersion] = Field(default_factory=list)
    required_services: list[RequiredDeploymentService] = Field(min_length=1)
    compatibility_matrix: list[DeploymentCompatibilityRule] = Field(default_factory=list)

    @field_validator(
        "targets",
        "images",
        "migrations",
        "config_keys",
        "dependency_versions",
        "required_services",
    )
    @classmethod
    def validate_unique_named_entries(cls, value: list, info):
        seen: set[str] = set()
        duplicates: list[str] = []
        for item in value:
            key = getattr(item, "value", None) or getattr(item, "name", None) or getattr(
                item, "id", None
            ) or str(item)
            if key in seen:
                duplicates.append(key)
            seen.add(key)
        if duplicates:
            raise ValueError(f"{info.field_name} entries must be unique: {duplicates}")
        return value

    @model_validator(mode="after")
    def validate_package_contract(self) -> "DeploymentPackageManifest":
        required_images = {
            "api",
            "worker",
            "browser-controller",
            "sandbox-controller",
        }
        image_names = {image.name for image in self.images}
        missing_images = sorted(required_images - image_names)
        if missing_images:
            raise ValueError(f"deployment package images must include: {missing_images}")

        required_services = set(RequiredDeploymentService)
        provided_services = {RequiredDeploymentService(service) for service in self.required_services}
        missing_services = sorted(service.value for service in required_services - provided_services)
        if missing_services:
            raise ValueError(f"deployment package required_services must include: {missing_services}")
        return self


def _version_tuple(value: str) -> tuple[int, ...]:
    normalized = value.strip().lstrip("v")
    if not normalized:
        return (0,)
    parts = normalized.split(".")
    parsed: list[int] = []
    for part in parts:
        number = ""
        for character in part:
            if not character.isdigit():
                break
            number += character
        parsed.append(int(number or "0"))
    return tuple(parsed)
