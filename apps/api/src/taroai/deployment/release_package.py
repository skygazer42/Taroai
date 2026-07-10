import argparse
import ast
import base64
import binascii
import hashlib
import json
import os
import re
import stat
import tempfile
import zipfile
from pathlib import Path
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from taroai.deployment.models import DeploymentPackageManifest
from taroai.deployment.package_manifest import (
    DeploymentPackageManifestBuildConfig,
    build_deployment_package_manifest,
    manifest_schema_json,
    manifest_json,
)


DEFAULT_RELEASE_INCLUDE_PATHS = [
    Path("apps"),
    Path("docs"),
    Path("infra"),
    Path("scripts"),
    Path(".env.example"),
    Path(".gitignore"),
    Path("README.md"),
    Path("pyproject.toml"),
]

EXCLUDED_NAMES = {
    ".aws",
    ".cache",
    ".coverage",
    ".git",
    ".direnv",
    ".gcloud",
    ".hypothesis",
    ".idea",
    ".ipynb_checkpoints",
    ".kube",
    ".netrc",
    ".nox",
    ".npmrc",
    ".pytest_cache",
    ".pypirc",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    ".vscode",
    "__pycache__",
    "__MACOSX",
    "application_default_credentials.json",
    "build",
    "client_secret.json",
    "credentials",
    "credentials.json",
    "coverage.xml",
    "desktop.ini",
    "htmlcov",
    "id_ed25519",
    "id_rsa",
    "kubeconfig",
    "service-account.json",
    "service_account.json",
    "venv",
    "node_modules",
    ".env",
    ".envrc",
    ".DS_Store",
    "a.md",
    "a.out",
    "dist",
    "Thumbs.db",
}

EXCLUDED_SUFFIXES = {
    ".bak",
    ".orig",
    ".pyc",
    ".pyo",
    ".rej",
    ".swo",
    ".swp",
    ".tmp",
}

CREDENTIAL_FILE_SUFFIXES = {
    ".cer",
    ".crt",
    ".der",
    ".jks",
    ".key",
    ".keystore",
    ".p12",
    ".pem",
    ".pfx",
}

EXCLUDED_ARCHIVE_SUFFIXES = (
    ".zip",
    ".tar",
    ".tar.gz",
    ".tgz",
    ".tar.bz2",
    ".tbz2",
    ".tar.xz",
    ".txz",
    ".7z",
    ".rar",
)

SECRET_VALUE_PATTERNS = [
    re.compile(rb"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(
        rb"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----",
    ),
    re.compile(
        rb"https?://[^\s\"'<>/@]+:[^\s\"'<>/@]+@[^\s\"'<>]+",
        flags=re.IGNORECASE,
    ),
    re.compile(
        rb"https?://[^\s\"'<>]*(?:[?&](?:access_token|refresh_token|"
        rb"id_token|api_key|x-api-key|token|signature|sig|client_secret|"
        rb"password)=)[^\s\"'<>]*",
        flags=re.IGNORECASE,
    ),
]

SCRIPT_PYTHON_MODULE_PATTERN = re.compile(
    r"\bpython(?:3(?:\.\d+)?)?(?:\s+-[A-Za-z][^\s]*)*\s+-m\s+"
    r"(taroai(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\b"
)


class StrictReleasePackageModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReleasePackageBuildConfig(StrictReleasePackageModel):
    repository_root: Path = Path(".")
    output_path: Path = Field(default=Path("dist/taroai-release.zip"))
    package_version: str = Field(default="0.1.0", min_length=1)
    app_version: str = Field(default="0.1.0", min_length=1)
    image_tag: str = Field(default="0.1.0", min_length=1)
    include_paths: list[Path] = Field(default_factory=lambda: list(DEFAULT_RELEASE_INCLUDE_PATHS))
    manifest_path: str = Field(default="infra/package/manifest.json", min_length=1)
    schema_path: str = Field(default="infra/package/manifest.schema.json", min_length=1)

    @model_validator(mode="after")
    def normalize_paths(self) -> "ReleasePackageBuildConfig":
        self.repository_root = self.repository_root.resolve()
        return self


class ReleasePackageBuildResult(StrictReleasePackageModel):
    output_path: Path
    file_count: int
    manifest_path: str
    checksum_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")


class ReleasePackageSigningConfig(StrictReleasePackageModel):
    package_path: Path
    signature_path: Path
    key_id: str = Field(min_length=1)
    private_key_base64: str = Field(min_length=1, exclude=True, repr=False)


class ReleasePackageSigningResult(StrictReleasePackageModel):
    signature_path: Path
    key_id: str = Field(min_length=1)
    package_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    public_key_base64: str = Field(min_length=1)

    @field_validator("package_sha256")
    @classmethod
    def normalize_package_checksum(cls, value: str) -> str:
        return value.lower()


class ReleasePackageVerificationConfig(StrictReleasePackageModel):
    package_path: Path
    manifest_path: str = Field(default="infra/package/manifest.json", min_length=1)
    schema_path: str = Field(default="infra/package/manifest.schema.json", min_length=1)
    expected_checksum_sha256: str | None = Field(
        default=None,
        pattern=r"^[a-fA-F0-9]{64}$",
    )
    signature_path: Path | None = None
    trusted_public_keys: dict[str, str] = Field(default_factory=dict)
    signature_required: bool = False

    @field_validator("expected_checksum_sha256")
    @classmethod
    def normalize_expected_checksum(cls, value: str | None) -> str | None:
        return normalize_checksum_sha256(value)


class ReleasePackageSignatureEnvelope(StrictReleasePackageModel):
    algorithm: Literal["ed25519"] = "ed25519"
    key_id: str = Field(min_length=1)
    package_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    signature: str = Field(min_length=1)

    @field_validator("package_sha256")
    @classmethod
    def normalize_package_checksum(cls, value: str) -> str:
        return value.lower()


class ReleaseImageBaseline(StrictReleasePackageModel):
    name: str = Field(min_length=1)
    repository: str = Field(min_length=1)


EXPECTED_RELEASE_IMAGE_BASELINE = [
    ReleaseImageBaseline(
        name="api",
        repository="ghcr.io/creao-ai/taroai-api",
    ),
    ReleaseImageBaseline(
        name="worker",
        repository="ghcr.io/creao-ai/taroai-api",
    ),
    ReleaseImageBaseline(
        name="browser-controller",
        repository="ghcr.io/creao-ai/taroai-browser-controller",
    ),
    ReleaseImageBaseline(
        name="sandbox-controller",
        repository="ghcr.io/creao-ai/taroai-sandbox-controller",
    ),
    ReleaseImageBaseline(
        name="web",
        repository="ghcr.io/creao-ai/taroai-web",
    ),
]


class ReleasePackageVerificationReport(StrictReleasePackageModel):
    package_path: Path
    valid: bool
    file_count: int
    manifest_valid: bool
    checksum_sha256: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")
    expected_checksum_sha256: str | None = Field(
        default=None,
        pattern=r"^[a-fA-F0-9]{64}$",
    )
    signature_valid: bool | None = None
    signature_key_id: str | None = None
    signature_errors: list[str] = Field(default_factory=list)
    checksum_mismatch_errors: list[str] = Field(default_factory=list)
    manifest_image_errors: list[str] = Field(default_factory=list)
    manifest_schema_errors: list[str] = Field(default_factory=list)
    upgrade_matrix_errors: list[str] = Field(default_factory=list)
    duplicate_entries: list[str] = Field(default_factory=list)
    unsafe_entries: list[str] = Field(default_factory=list)
    symlink_entries: list[str] = Field(default_factory=list)
    forbidden_entries: list[str] = Field(default_factory=list)
    non_executable_script_entries: list[str] = Field(default_factory=list)
    invalid_python_entries: list[str] = Field(default_factory=list)
    missing_required_entries: list[str] = Field(default_factory=list)
    missing_import_dependency_entries: list[str] = Field(default_factory=list)
    missing_script_module_entries: list[str] = Field(default_factory=list)
    missing_migration_entries: list[str] = Field(default_factory=list)
    migration_checksum_mismatches: list[str] = Field(default_factory=list)
    secret_pattern_entries: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @field_validator("checksum_sha256", "expected_checksum_sha256")
    @classmethod
    def normalize_report_checksum(cls, value: str | None) -> str | None:
        return normalize_checksum_sha256(value)


def normalize_checksum_sha256(value: str | None) -> str | None:
    if value is None:
        return None
    return value.lower()


def build_release_package(config: ReleasePackageBuildConfig) -> ReleasePackageBuildResult:
    output_path = resolve_repo_path(config.repository_root, config.output_path)
    reject_release_output_path_symlink(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    validate_generated_archive_path(config.manifest_path)
    validate_generated_archive_path(config.schema_path)

    manifest = build_deployment_package_manifest(
        DeploymentPackageManifestBuildConfig(
            package_version=config.package_version,
            app_version=config.app_version,
            image_tag=config.image_tag,
            repository_root=config.repository_root,
        )
    )
    manifest_content = manifest_json(manifest).encode("utf-8")
    schema_content = manifest_schema_json().encode("utf-8")

    files = collect_release_files(config)
    source_secret_entries = find_secret_pattern_source_entries(
        files,
        config.repository_root,
    )
    if source_secret_entries:
        raise ValueError(
            "release source contains secret-shaped content: "
            + ", ".join(source_secret_entries)
        )
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            delete=False,
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
        ) as temp_file:
            temp_path = Path(temp_file.name)
        with zipfile.ZipFile(temp_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            write_bytes_to_zip(
                archive,
                config.manifest_path,
                manifest_content,
                mode=0o644,
            )
            write_bytes_to_zip(
                archive,
                config.schema_path,
                schema_content,
                mode=0o644,
            )
            for path in files:
                arcname = path.relative_to(config.repository_root).as_posix()
                archive.write(path, arcname)
        os.replace(temp_path, output_path)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

    return ReleasePackageBuildResult(
        output_path=output_path,
        file_count=len(files) + 2,
        manifest_path=config.manifest_path,
        checksum_sha256=file_sha256(output_path),
    )


def sign_release_package(config: ReleasePackageSigningConfig) -> ReleasePackageSigningResult:
    package_sha256 = file_sha256(config.package_path)
    private_key = Ed25519PrivateKey.from_private_bytes(
        decode_base64_value(
            config.private_key_base64,
            "release package private signing key is invalid",
        )
    )
    signature = private_key.sign(
        canonical_release_signature_payload_fields(
            algorithm="ed25519",
            key_id=config.key_id,
            package_sha256=package_sha256,
        )
    )
    envelope = ReleasePackageSignatureEnvelope(
        algorithm="ed25519",
        key_id=config.key_id,
        package_sha256=package_sha256,
        signature=base64.b64encode(signature).decode("ascii"),
    )
    atomic_write_text(
        config.signature_path,
        envelope.model_dump_json(indent=2),
        encoding="utf-8",
    )
    public_key = private_key.public_key().public_bytes(
        encoding=Encoding.Raw,
        format=PublicFormat.Raw,
    )
    return ReleasePackageSigningResult(
        signature_path=config.signature_path,
        key_id=config.key_id,
        package_sha256=package_sha256,
        public_key_base64=base64.b64encode(public_key).decode("ascii"),
    )


def verify_release_package(
    config: ReleasePackageVerificationConfig,
) -> ReleasePackageVerificationReport:
    package_path = config.package_path
    errors: list[str] = []
    duplicate_entries: list[str] = []
    unsafe_entries: list[str] = []
    symlink_entries: list[str] = []
    forbidden_entries: list[str] = []
    non_executable_script_entries: list[str] = []
    invalid_python_entries: list[str] = []
    missing_required_entries: list[str] = []
    missing_import_dependency_entries: list[str] = []
    missing_script_module_entries: list[str] = []
    missing_migration_entries: list[str] = []
    migration_checksum_mismatches: list[str] = []
    secret_pattern_entries: list[str] = []
    manifest_image_errors: list[str] = []
    manifest_schema_errors: list[str] = []
    upgrade_matrix_errors: list[str] = []
    checksum_mismatch_errors: list[str] = []
    signature_errors: list[str] = []
    signature_valid: bool | None = None
    signature_key_id: str | None = None
    manifest_valid = False
    file_count = 0
    checksum_sha256: str | None = None

    try:
        checksum_sha256 = file_sha256(package_path)
        signature_valid, signature_key_id, signature_errors = validate_release_signature(
            config,
            checksum_sha256,
        )
        checksum_mismatch_errors = validate_expected_checksum(
            checksum_sha256,
            config.expected_checksum_sha256,
        )
        with zipfile.ZipFile(package_path) as archive:
            names = sorted(archive.namelist())
            name_set = set(names)
            file_count = len(names)
            duplicate_entries = find_duplicate_entries(names)
            unsafe_entries = [name for name in names if archive_entry_is_unsafe(name)]
            symlink_entries = find_symlink_entries(archive)
            forbidden_entries = [
                name for name in names if should_exclude_archive_entry(name)
            ]
            non_executable_script_entries = find_non_executable_script_entries(archive)
            secret_pattern_entries = find_secret_pattern_entries(archive, names)
            invalid_python_entries = find_invalid_python_entries(archive, names)
            missing_required_entries = [
                entry
                for entry in required_archive_entries(config.manifest_path, config.schema_path)
                if entry not in name_set
            ]
            missing_import_dependency_entries = (
                find_missing_first_party_import_dependency_entries(
                    archive,
                    names,
                    name_set,
                )
            )
            missing_script_module_entries = find_missing_script_module_entries(
                archive,
                names,
                name_set,
            )
            manifest_schema_errors = validate_manifest_schema_entry(
                archive,
                name_set,
                config.schema_path,
            )

            manifest: DeploymentPackageManifest | None = None
            if config.manifest_path in name_set:
                try:
                    manifest = DeploymentPackageManifest.model_validate(
                        json.loads(archive.read(config.manifest_path))
                    )
                    manifest_valid = True
                    manifest_image_errors = validate_manifest_image_baseline(manifest)
                except Exception as error:
                    errors.append(f"deployment package manifest is invalid: {error}")
            else:
                errors.append(f"deployment package manifest is missing: {config.manifest_path}")

            if manifest is not None:
                upgrade_matrix_errors = validate_upgrade_matrix_entry(
                    archive,
                    name_set,
                    manifest,
                )
                for migration in manifest.migrations:
                    if migration.path not in name_set:
                        missing_migration_entries.append(migration.path)
                        continue
                    checksum = archive_entry_sha256(archive, migration.path)
                    if checksum != migration.checksum_sha256:
                        migration_checksum_mismatches.append(migration.path)
    except zipfile.BadZipFile as error:
        errors.append(f"release package is not a readable zip file: {error}")
    except FileNotFoundError as error:
        errors.append(f"release package file is missing: {error}")

    valid = not (
        errors
        or duplicate_entries
        or unsafe_entries
        or symlink_entries
        or forbidden_entries
        or non_executable_script_entries
        or invalid_python_entries
        or missing_required_entries
        or missing_import_dependency_entries
        or missing_script_module_entries
        or missing_migration_entries
        or migration_checksum_mismatches
        or secret_pattern_entries
        or manifest_image_errors
        or manifest_schema_errors
        or upgrade_matrix_errors
        or checksum_mismatch_errors
        or signature_errors
        or not manifest_valid
    )
    return ReleasePackageVerificationReport(
        package_path=package_path,
        valid=valid,
        file_count=file_count,
        manifest_valid=manifest_valid,
        checksum_sha256=checksum_sha256,
        expected_checksum_sha256=config.expected_checksum_sha256,
        signature_valid=signature_valid,
        signature_key_id=signature_key_id,
        signature_errors=signature_errors,
        checksum_mismatch_errors=checksum_mismatch_errors,
        manifest_image_errors=manifest_image_errors,
        manifest_schema_errors=manifest_schema_errors,
        upgrade_matrix_errors=upgrade_matrix_errors,
        duplicate_entries=duplicate_entries,
        unsafe_entries=unsafe_entries,
        symlink_entries=symlink_entries,
        forbidden_entries=forbidden_entries,
        non_executable_script_entries=non_executable_script_entries,
        invalid_python_entries=invalid_python_entries,
        missing_required_entries=missing_required_entries,
        missing_import_dependency_entries=missing_import_dependency_entries,
        missing_script_module_entries=missing_script_module_entries,
        missing_migration_entries=missing_migration_entries,
        migration_checksum_mismatches=migration_checksum_mismatches,
        secret_pattern_entries=secret_pattern_entries,
        errors=errors,
    )


def reject_release_output_path_symlink(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"release output path must not be a symlink: {path}")


def validate_generated_archive_path(path: str) -> None:
    if archive_entry_is_unsafe(path) or should_exclude_archive_entry(path):
        raise ValueError(f"release generated archive path must be relative and safe: {path}")


def validate_expected_checksum(
    checksum_sha256: str,
    expected_checksum_sha256: str | None,
) -> list[str]:
    if expected_checksum_sha256 is None:
        return []
    if checksum_sha256 != expected_checksum_sha256:
        return ["release package checksum does not match expected SHA256"]
    return []


def validate_release_signature(
    config: ReleasePackageVerificationConfig,
    checksum_sha256: str,
) -> tuple[bool | None, str | None, list[str]]:
    if config.signature_path is None:
        if config.signature_required:
            return False, None, ["release package signature is required"]
        return None, None, []
    try:
        envelope = ReleasePackageSignatureEnvelope.model_validate_json(
            config.signature_path.read_text()
        )
    except Exception as error:
        return False, None, [f"release package signature envelope is invalid: {error}"]

    errors: list[str] = []
    if envelope.package_sha256 != checksum_sha256:
        errors.append("release package signature package_sha256 does not match archive SHA256")

    public_key_value = config.trusted_public_keys.get(envelope.key_id)
    if public_key_value is None:
        errors.append("release package signing key is not trusted")
    else:
        try:
            public_key = Ed25519PublicKey.from_public_bytes(
                decode_base64_value(
                    public_key_value,
                    "trusted release package signing key is invalid",
                )
            )
            public_key.verify(
                decode_base64_value(
                    envelope.signature,
                    "release package signature value is invalid",
                ),
                canonical_release_signature_payload(envelope),
            )
        except InvalidSignature:
            errors.append("release package signature verification failed")
        except ValueError as error:
            errors.append(str(error))

    return len(errors) == 0, envelope.key_id, errors


def decode_base64_value(value: str, error_message: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError(error_message) from error


def canonical_release_signature_payload(
    envelope: ReleasePackageSignatureEnvelope,
) -> bytes:
    return canonical_release_signature_payload_fields(
        algorithm=envelope.algorithm,
        key_id=envelope.key_id,
        package_sha256=envelope.package_sha256,
    )


def canonical_release_signature_payload_fields(
    algorithm: str,
    key_id: str,
    package_sha256: str,
) -> bytes:
    return json.dumps(
        {
            "algorithm": algorithm,
            "key_id": key_id,
            "package_sha256": package_sha256,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def validate_manifest_schema_entry(
    archive: zipfile.ZipFile,
    name_set: set[str],
    schema_path: str,
) -> list[str]:
    if schema_path not in name_set:
        return []
    try:
        packaged_schema = json.loads(archive.read(schema_path))
    except Exception as error:
        return [f"deployment package schema is invalid JSON: {error}"]
    expected_schema = json.loads(manifest_schema_json())
    if packaged_schema != expected_schema:
        return ["deployment package schema must match DeploymentPackageManifest"]
    return []


def validate_manifest_image_baseline(manifest: DeploymentPackageManifest) -> list[str]:
    images_by_name = {image.name: image for image in manifest.images}
    errors: list[str] = []
    for expected in EXPECTED_RELEASE_IMAGE_BASELINE:
        image = images_by_name.get(expected.name)
        if image is None:
            errors.append(f"deployment image {expected.name} is missing")
            continue
        if image.repository != expected.repository:
            errors.append(
                f"deployment image {expected.name} repository must be {expected.repository}"
            )
    return errors


def validate_upgrade_matrix_entry(
    archive: zipfile.ZipFile,
    name_set: set[str],
    manifest: DeploymentPackageManifest,
) -> list[str]:
    upgrade_matrix_path = "infra/package/upgrade-matrix.md"
    if upgrade_matrix_path not in name_set:
        return []
    if not manifest.migrations:
        return ["upgrade matrix cannot be checked without manifest migrations"]
    expected_range = f"{manifest.migrations[0].id} to {manifest.migrations[-1].id}"
    try:
        content = archive.read(upgrade_matrix_path).decode("utf-8")
    except UnicodeDecodeError as error:
        return [f"upgrade matrix must be UTF-8 text: {error}"]
    if expected_range not in content:
        return [f"upgrade matrix must cover migration range {expected_range}"]
    return []


def collect_release_files(config: ReleasePackageBuildConfig) -> list[Path]:
    files: list[Path] = []
    output_path = resolve_repo_path(config.repository_root, config.output_path).resolve()
    generated_archive_paths = {
        Path(config.manifest_path),
        Path(config.schema_path),
    }
    for include_path in config.include_paths:
        path = resolve_repo_path(config.repository_root, include_path)
        reject_release_include_path_symlink(path)
        reject_release_include_path_outside_repository(config.repository_root, path)
        if not path.exists():
            continue
        if path.resolve() == output_path:
            continue
        if should_exclude_path(config.repository_root, path):
            continue
        if path.is_file():
            if path.relative_to(config.repository_root) not in generated_archive_paths:
                files.append(path)
            continue
        for candidate in sorted(path.rglob("*")):
            if (
                candidate.is_file()
                and candidate.relative_to(config.repository_root) not in generated_archive_paths
                and candidate.resolve() != output_path
                and not should_exclude_path(config.repository_root, candidate)
            ):
                files.append(candidate)
    return sorted(files, key=lambda item: item.relative_to(config.repository_root).as_posix())


def reject_release_include_path_outside_repository(
    repository_root: Path,
    path: Path,
) -> None:
    resolved_root = repository_root.resolve()
    resolved_path = path.resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as error:
        raise ValueError(
            f"release include path must stay under repository root: {path}"
        ) from error


def reject_release_include_path_symlink(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"release include path must not be a symlink: {path}")


def should_exclude_path(repository_root: Path, path: Path) -> bool:
    if path.is_symlink():
        return True
    relative = path.relative_to(repository_root)
    parts = relative.parts
    if any(part in EXCLUDED_NAMES for part in parts):
        return True
    if archive_name_is_local_env_file(relative.as_posix()):
        return True
    if archive_name_has_excluded_archive_suffix(relative.as_posix()):
        return True
    if archive_name_is_local_editor_file(relative.as_posix()):
        return True
    if archive_name_is_local_coverage_file(relative.as_posix()):
        return True
    if archive_name_is_credential_file(relative.as_posix()):
        return True
    if path.suffix in EXCLUDED_SUFFIXES:
        return True
    if parts and parts[0] == "tests":
        return True
    return False


def should_exclude_archive_entry(name: str) -> bool:
    path = Path(name)
    parts = path.parts
    if any(part in EXCLUDED_NAMES for part in parts):
        return True
    if archive_name_is_local_env_file(name):
        return True
    if archive_name_has_excluded_archive_suffix(name):
        return True
    if archive_name_is_local_editor_file(name):
        return True
    if archive_name_is_local_coverage_file(name):
        return True
    if archive_name_is_credential_file(name):
        return True
    if path.suffix in EXCLUDED_SUFFIXES:
        return True
    if parts and parts[0] == "tests":
        return True
    return False


def archive_name_is_local_env_file(name: str) -> bool:
    filename = Path(name).name
    if filename == ".env.example" or filename.endswith(".env.example"):
        return False
    if filename == ".env" or filename.startswith(".env."):
        return True
    return filename.endswith(".env")


def archive_name_has_excluded_archive_suffix(name: str) -> bool:
    lowered = name.lower()
    return any(lowered.endswith(suffix) for suffix in EXCLUDED_ARCHIVE_SUFFIXES)


def archive_name_is_local_editor_file(name: str) -> bool:
    filename = Path(name).name
    return filename.endswith("~") or (
        filename.startswith("#") and filename.endswith("#")
    )


def archive_name_is_local_coverage_file(name: str) -> bool:
    filename = Path(name).name
    return filename.startswith(".coverage.")


def archive_name_is_credential_file(name: str) -> bool:
    filename = Path(name).name.lower()
    return any(filename.endswith(suffix) for suffix in CREDENTIAL_FILE_SUFFIXES)


def find_duplicate_entries(names: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for name in names:
        if name in seen and name not in duplicates:
            duplicates.append(name)
        seen.add(name)
    return duplicates


def archive_entry_is_unsafe(name: str) -> bool:
    path = Path(name)
    if path.is_absolute():
        return True
    return ".." in path.parts


def find_symlink_entries(archive: zipfile.ZipFile) -> list[str]:
    entries: list[str] = []
    for item in archive.infolist():
        if archive_entry_is_symlink(item):
            entries.append(item.filename)
    return sorted(entries)


def archive_entry_is_symlink(item: zipfile.ZipInfo) -> bool:
    mode = (item.external_attr >> 16) & 0o170000
    return stat.S_ISLNK(mode)


def find_non_executable_script_entries(archive: zipfile.ZipFile) -> list[str]:
    entries: list[str] = []
    for item in archive.infolist():
        if not item.filename.startswith("scripts/") or not item.filename.endswith(".sh"):
            continue
        mode = (item.external_attr >> 16) & 0o777
        if mode & 0o111 == 0:
            entries.append(item.filename)
    return sorted(entries)


def required_archive_entries(manifest_path: str, schema_path: str) -> list[str]:
    return [
        manifest_path,
        schema_path,
        "apps/api/Dockerfile",
        "apps/api/Dockerfile.browser",
        "apps/api/Dockerfile.sandbox",
        "apps/api/entrypoint.sh",
        "apps/api/requirements.txt",
        "apps/api/requirements-browser.txt",
        "apps/api/migrations/001_initial.sql",
        "apps/api/src/taroai/app.py",
        "apps/api/src/taroai/config.py",
        "apps/api/src/taroai/db/models.py",
        "apps/api/src/taroai/db/migration_cli.py",
        "apps/api/src/taroai/domain.py",
        "apps/api/src/taroai/deployment/api_verification.py",
        "apps/api/src/taroai/deployment/install_evidence.py",
        "apps/api/src/taroai/deployment/install_validation.py",
        "apps/api/src/taroai/deployment/local_cloud_poc_demo_gate.py",
        "apps/api/src/taroai/deployment/local_cloud_poc_verification.py",
        "apps/api/src/taroai/deployment/models.py",
        "apps/api/src/taroai/deployment/package_manifest.py",
        "apps/api/src/taroai/deployment/release_package.py",
        "apps/api/src/taroai/deployment/restore_drill_verification.py",
        "apps/api/src/taroai/deployment/transfer_evidence.py",
        "apps/api/src/taroai/deployment/validation.py",
        "apps/api/src/taroai/deployment_evidence.py",
        "apps/api/src/taroai/errors.py",
        "apps/api/src/taroai/lifecycle/backup.py",
        "apps/api/src/taroai/model_gateway/gateway.py",
        "apps/api/src/taroai/model_gateway/models.py",
        "apps/api/src/taroai/model_gateway/providers.py",
        "apps/api/src/taroai/model_gateway/verification.py",
        "apps/api/src/taroai/observability/exporter.py",
        "apps/api/src/taroai/observability/models.py",
        "apps/api/src/taroai/observability/verification.py",
        "apps/api/src/taroai/sandbox/adapter.py",
        "apps/api/src/taroai/sandbox/browser.py",
        "apps/api/src/taroai/sandbox/browser_verification.py",
        "apps/api/src/taroai/sandbox/controller_service.py",
        "apps/api/src/taroai/sandbox/docker.py",
        "apps/api/src/taroai/sandbox/http.py",
        "apps/api/src/taroai/sandbox/image_policy.py",
        "apps/api/src/taroai/sandbox/kubernetes.py",
        "apps/api/src/taroai/sandbox/kubernetes_verification.py",
        "apps/api/src/taroai/sandbox/lifecycle_verification.py",
        "apps/api/src/taroai/sandbox/models.py",
        "apps/api/src/taroai/sandbox/playwright_service.py",
        "apps/api/src/taroai/secrets/models.py",
        "apps/api/src/taroai/secrets/service.py",
        "apps/api/src/taroai/secrets/verification.py",
        "apps/api/src/taroai/storage/adapter.py",
        "apps/api/src/taroai/storage/models.py",
        "apps/api/src/taroai/storage/object_storage_verification.py",
        "apps/api/src/taroai/support/redaction.py",
        "apps/api/src/taroai/workers/models.py",
        "apps/api/src/taroai/workers/queue.py",
        "apps/api/src/taroai/workers/redis_verification.py",
        "apps/web/Dockerfile",
        "apps/web/index.html",
        "apps/web/assets/main.js",
        "apps/web/assets/styles.css",
        "docs/customer-success/admin-training.md",
        "docs/customer-success/employee-training.md",
        "docs/customer-success/rollout-playbook.md",
        "docs/customer-success/solution-engineer-checklist.md",
        "docs/operations/air-gapped-install.md",
        "docs/operations/alert-routing.md",
        "docs/operations/disaster-recovery.md",
        "docs/operations/mvp-local-cloud-poc.md",
        "docs/operations/postmortem-template.md",
        "docs/operations/private-install-validation.md",
        "docs/operations/private-upgrade-rollback.md",
        "docs/operations/tenant-offboarding-runbook.md",
        "docs/operations/triggers-runbook.md",
        "docs/solution-packs/ecommerce.md",
        "docs/solution-packs/operations.md",
        "docs/solution-packs/sales.md",
        "docs/solution-packs/support.md",
        "infra/config/byoc.env.example",
        "infra/config/cloud.env.example",
        "infra/config/deepseek.env.example",
        "infra/config/private.env.example",
        "infra/docker-compose.yml",
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
        "infra/package/README.md",
        "infra/package/upgrade-matrix.md",
        "README.md",
        "pyproject.toml",
        "scripts/build-package-manifest.sh",
        "scripts/build-package-schema.sh",
        "scripts/build-release-package.sh",
        "scripts/build-migration-plan.sh",
        "scripts/validate-install.sh",
        "scripts/verify-object-storage.sh",
        "scripts/verify-redis-queue.sh",
        "scripts/verify-secret-manager.sh",
        "scripts/verify-event-stream.sh",
        "scripts/verify-audit-write.sh",
        "scripts/verify-model-gateway.sh",
        "scripts/verify-sandbox-lifecycle.sh",
        "scripts/verify-kubernetes-sandbox.sh",
        "scripts/verify-browser-controller.sh",
        "scripts/verify-local-cloud-poc.sh",
        "scripts/verify-local-cloud-demo-ready.sh",
        "scripts/verify-compose-strict-e2e.sh",
        "scripts/verify-trace-collector.sh",
        "scripts/verify-restore-drill.sh",
        "scripts/sign-release-package.sh",
        "scripts/build-release-transfer-evidence.sh",
        "scripts/redact-support-bundle.sh",
        "scripts/verify-release-package.sh",
        ".env.example",
    ]


def archive_entry_sha256(archive: zipfile.ZipFile, name: str) -> str:
    digest = hashlib.sha256()
    with archive.open(name) as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_secret_pattern_entries(
    archive: zipfile.ZipFile,
    names: list[str],
) -> list[str]:
    matches: list[str] = []
    for name in names:
        if name.endswith("/"):
            continue
        content = archive.read(name)
        if any(pattern.search(content) for pattern in SECRET_VALUE_PATTERNS):
            matches.append(name)
    return matches


def find_secret_pattern_source_entries(
    paths: list[Path],
    repository_root: Path,
) -> list[str]:
    matches: list[str] = []
    for path in paths:
        content = path.read_bytes()
        if any(pattern.search(content) for pattern in SECRET_VALUE_PATTERNS):
            matches.append(path.relative_to(repository_root).as_posix())
    return sorted(matches)


def find_invalid_python_entries(
    archive: zipfile.ZipFile,
    names: list[str],
) -> list[str]:
    invalid: list[str] = []
    for name in names:
        if not release_python_source_entry(name):
            continue
        try:
            ast.parse(archive.read(name).decode("utf-8"), filename=name)
        except (SyntaxError, UnicodeDecodeError):
            invalid.append(name)
    return sorted(invalid)


def find_missing_first_party_import_dependency_entries(
    archive: zipfile.ZipFile,
    names: list[str],
    name_set: set[str],
) -> list[str]:
    missing: set[str] = set()
    for name in names:
        if not release_python_source_entry(name):
            continue
        try:
            tree = ast.parse(archive.read(name).decode("utf-8"), filename=name)
        except (SyntaxError, UnicodeDecodeError):
            continue
        for module in first_party_import_modules(tree):
            expected_entry = missing_first_party_module_entry(module, name_set)
            if expected_entry is not None:
                missing.add(expected_entry)
    return sorted(missing)


def find_missing_script_module_entries(
    archive: zipfile.ZipFile,
    names: list[str],
    name_set: set[str],
) -> list[str]:
    missing: set[str] = set()
    for name in names:
        if not release_shell_script_entry(name):
            continue
        try:
            content = archive.read(name).decode("utf-8")
        except UnicodeDecodeError:
            continue
        for module in script_python_module_targets(content):
            expected_entry = missing_first_party_module_entry(module, name_set)
            if expected_entry is not None:
                missing.add(expected_entry)
    return sorted(missing)


def release_python_source_entry(name: str) -> bool:
    return (
        name.startswith("apps/api/src/taroai/")
        and name.endswith(".py")
        and not archive_entry_is_unsafe(name)
    )


def release_shell_script_entry(name: str) -> bool:
    return (
        name.startswith("scripts/")
        and name.endswith(".sh")
        and not archive_entry_is_unsafe(name)
    )


def script_python_module_targets(content: str) -> set[str]:
    return {match.group(1) for match in SCRIPT_PYTHON_MODULE_PATTERN.finditer(content)}


def first_party_import_modules(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "taroai" or alias.name.startswith("taroai."):
                    modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            if node.module == "taroai" or node.module.startswith("taroai."):
                modules.add(node.module)
                if node.module == "taroai":
                    for alias in node.names:
                        if alias.name != "*":
                            modules.add(f"taroai.{alias.name}")
    return modules


def missing_first_party_module_entry(
    module: str,
    name_set: set[str],
) -> str | None:
    module_path = module.replace(".", "/")
    module_file_entry = f"apps/api/src/{module_path}.py"
    module_package_entry = f"apps/api/src/{module_path}/__init__.py"
    if module_file_entry in name_set or module_package_entry in name_set:
        return None
    module_package_prefix = f"apps/api/src/{module_path}/"
    if any(name.startswith(module_package_prefix) for name in name_set):
        return module_package_entry
    return module_file_entry


def write_bytes_to_zip(
    archive: zipfile.ZipFile,
    arcname: str,
    content: bytes,
    mode: int,
) -> None:
    info = zipfile.ZipInfo(arcname)
    info.external_attr = (mode & 0xFFFF) << 16
    archive.writestr(info, content)


def atomic_write_text(path: Path, content: str, encoding: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            delete=False,
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        ) as temp_file:
            temp_path = Path(temp_file.name)
        temp_path.write_text(content, encoding=encoding)
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def resolve_repo_path(repository_root: Path, path: Path) -> Path:
    if path.is_absolute():
        return path
    return repository_root / path


def parse_args(
    argv: list[str] | None = None,
) -> ReleasePackageBuildConfig | ReleasePackageSigningConfig | ReleasePackageVerificationConfig:
    parser = argparse.ArgumentParser(
        description="Build a clean Taroai release zip package."
    )
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--sign", action="store_true")
    parser.add_argument("--repository-root", default=".")
    parser.add_argument("--output", required=True)
    parser.add_argument("--expected-sha256", default=None)
    parser.add_argument("--signature", default=None)
    parser.add_argument("--signature-output", default=None)
    parser.add_argument("--key-id", default=os.environ.get("TAROAI_RELEASE_SIGNING_KEY_ID"))
    parser.add_argument("--private-key-env", default="TAROAI_RELEASE_SIGNING_PRIVATE_KEY")
    parser.add_argument("--trusted-public-key", action="append", default=[])
    parser.add_argument("--package-version", default=os.environ.get("TAROAI_PACKAGE_VERSION", "0.1.0"))
    parser.add_argument("--app-version", default=os.environ.get("TAROAI_APP_VERSION", "0.1.0"))
    parser.add_argument("--image-tag", default=os.environ.get("TAROAI_IMAGE_TAG", "0.1.0"))
    parsed = parser.parse_args(argv)
    if parsed.sign:
        if not parsed.key_id:
            raise ValueError("--key-id or TAROAI_RELEASE_SIGNING_KEY_ID is required")
        private_key_base64 = os.environ.get(parsed.private_key_env)
        if not private_key_base64:
            raise ValueError(
                f"{parsed.private_key_env} must contain the base64 Ed25519 private key"
            )
        return ReleasePackageSigningConfig(
            package_path=Path(parsed.output),
            signature_path=(
                Path(parsed.signature_output)
                if parsed.signature_output
                else Path(parsed.output).with_suffix(".zip.sig.json")
            ),
            key_id=parsed.key_id,
            private_key_base64=private_key_base64,
        )
    if parsed.verify:
        return ReleasePackageVerificationConfig(
            package_path=Path(parsed.output),
            expected_checksum_sha256=parsed.expected_sha256,
            signature_path=Path(parsed.signature) if parsed.signature else None,
            trusted_public_keys=parse_trusted_public_keys(parsed.trusted_public_key),
        )
    return ReleasePackageBuildConfig(
        repository_root=Path(parsed.repository_root),
        output_path=Path(parsed.output),
        package_version=parsed.package_version,
        app_version=parsed.app_version,
        image_tag=parsed.image_tag,
    )


def parse_trusted_public_keys(values: list[str]) -> dict[str, str]:
    trusted_public_keys: dict[str, str] = {}
    for value in values:
        key_id, separator, public_key = value.partition("=")
        if not separator or not key_id.strip() or not public_key.strip():
            raise ValueError("--trusted-public-key must use key_id=base64_public_key")
        trusted_public_keys[key_id.strip()] = public_key.strip()
    return trusted_public_keys


def main(argv: list[str] | None = None) -> int:
    config = parse_args(argv)
    if isinstance(config, ReleasePackageSigningConfig):
        result = sign_release_package(config)
        print(result.model_dump_json(indent=2))
        return 0

    if isinstance(config, ReleasePackageVerificationConfig):
        result = verify_release_package(config)
        print(result.model_dump_json(indent=2))
        return 0 if result.valid else 1

    result = build_release_package(config)
    print(
        result.model_dump_json(
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
