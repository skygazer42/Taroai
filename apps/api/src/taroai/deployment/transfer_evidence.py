import argparse
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from taroai.deployment.models import DeploymentPackageManifest
from taroai.deployment.release_package import (
    ReleasePackageVerificationConfig,
    atomic_write_text,
    verify_release_package,
)


class ReleaseTransferEvidenceBuildConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package_path: Path
    signature_path: Path
    key_id: str = Field(min_length=1)
    public_key_base64: str = Field(min_length=1)
    output_path: Path | None = None
    manifest_path: str = Field(default="infra/package/manifest.json", min_length=1)


class ReleaseTransferEvidenceReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generated_at: datetime
    package_path: Path
    package_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    package_version: str = Field(min_length=1)
    app_version: str = Field(min_length=1)
    signature_path: Path
    signature_key_id: str = Field(min_length=1)
    signature_valid: bool
    public_key_base64: str = Field(min_length=1)
    image_count: int = Field(ge=0)
    migration_count: int = Field(ge=0)
    required_service_count: int = Field(ge=0)
    valid: bool
    verification_valid: bool

    @field_validator("package_sha256")
    @classmethod
    def normalize_package_checksum(cls, value: str) -> str:
        return value.lower()

    @field_validator("generated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("generated_at must be timezone aware")
        return value


def build_release_transfer_evidence(
    config: ReleaseTransferEvidenceBuildConfig,
) -> ReleaseTransferEvidenceReport:
    verification = verify_release_package(
        ReleasePackageVerificationConfig(
            package_path=config.package_path,
            expected_checksum_sha256=None,
            signature_path=config.signature_path,
            trusted_public_keys={config.key_id: config.public_key_base64},
        )
    )
    if not verification.valid:
        raise ValueError("release package verification failed before transfer evidence build")
    if verification.checksum_sha256 is None:
        raise ValueError("release package checksum is missing")
    if verification.signature_valid is not True:
        raise ValueError("release package signature verification failed")

    manifest = read_manifest_from_package(config.package_path, config.manifest_path)
    report = ReleaseTransferEvidenceReport(
        generated_at=datetime.now(timezone.utc),
        package_path=portable_transfer_evidence_path(
            config.package_path,
            config.output_path,
        ),
        package_sha256=verification.checksum_sha256,
        package_version=manifest.package_version,
        app_version=manifest.app_version,
        signature_path=portable_transfer_evidence_path(
            config.signature_path,
            config.output_path,
        ),
        signature_key_id=verification.signature_key_id or config.key_id,
        signature_valid=True,
        public_key_base64=config.public_key_base64,
        image_count=len(manifest.images),
        migration_count=len(manifest.migrations),
        required_service_count=len(manifest.required_services),
        valid=True,
        verification_valid=True,
    )
    if config.output_path is not None:
        atomic_write_text(
            config.output_path,
            report.model_dump_json(indent=2),
            encoding="utf-8",
        )
    return report


def portable_transfer_evidence_path(path: Path, output_path: Path | None) -> Path:
    if output_path is None:
        return path
    output_parent = output_path.resolve(strict=False).parent
    resolved_path = path.resolve(strict=False)
    try:
        relative_path = resolved_path.relative_to(output_parent)
    except ValueError:
        return resolved_path
    if relative_path.parent == Path("."):
        return relative_path
    return resolved_path


def read_manifest_from_package(
    package_path: Path,
    manifest_path: str,
) -> DeploymentPackageManifest:
    with zipfile.ZipFile(package_path) as archive:
        return DeploymentPackageManifest.model_validate(
            json.loads(archive.read(manifest_path))
        )


def parse_trusted_public_key(value: str) -> tuple[str, str]:
    key_id, separator, public_key = value.partition("=")
    if not separator or not key_id.strip() or not public_key.strip():
        raise ValueError("--trusted-public-key must use key_id=base64_public_key")
    return key_id.strip(), public_key.strip()


def parse_args(argv: list[str] | None = None) -> ReleaseTransferEvidenceBuildConfig:
    parser = argparse.ArgumentParser(
        description="Build release transfer evidence for a signed Taroai package."
    )
    parser.add_argument("--package", required=True)
    parser.add_argument("--signature", required=True)
    parser.add_argument("--trusted-public-key", required=True)
    parser.add_argument("--output", required=True)
    parsed = parser.parse_args(argv)
    key_id, public_key = parse_trusted_public_key(parsed.trusted_public_key)
    return ReleaseTransferEvidenceBuildConfig(
        package_path=Path(parsed.package),
        signature_path=Path(parsed.signature),
        key_id=key_id,
        public_key_base64=public_key,
        output_path=Path(parsed.output),
    )


def main(argv: list[str] | None = None) -> int:
    config = parse_args(argv)
    report = build_release_transfer_evidence(config)
    print(report.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
