import base64
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)

from taroai.deployment.release_package import (
    ReleasePackageBuildConfig,
    ReleasePackageSigningConfig,
    build_release_package,
    sign_release_package,
)
from taroai.deployment.transfer_evidence import (
    ReleaseTransferEvidenceBuildConfig,
    ReleaseTransferEvidenceReport,
    build_release_transfer_evidence,
)


def private_key_base64(private_key: Ed25519PrivateKey) -> str:
    return base64.b64encode(
        private_key.private_bytes(
            encoding=Encoding.Raw,
            format=PrivateFormat.Raw,
            encryption_algorithm=NoEncryption(),
        )
    ).decode("ascii")


def build_signed_release(tmp_path: Path):
    package_path = tmp_path / "taroai-release.zip"
    signature_path = tmp_path / "taroai-release.zip.sig.json"
    build_result = build_release_package(
        ReleasePackageBuildConfig(
            repository_root=Path("."),
            output_path=package_path,
            package_version="0.1.0",
            app_version="0.1.0",
            image_tag="0.1.0",
        )
    )
    signing_result = sign_release_package(
        ReleasePackageSigningConfig(
            package_path=package_path,
            signature_path=signature_path,
            key_id="creao-release-2026-01",
            private_key_base64=private_key_base64(Ed25519PrivateKey.generate()),
        )
    )
    return build_result, signing_result


def test_release_transfer_evidence_builder_writes_verified_packet(tmp_path: Path):
    build_result, signing_result = build_signed_release(tmp_path)
    evidence_path = tmp_path / "release-transfer-evidence.json"

    report = build_release_transfer_evidence(
        ReleaseTransferEvidenceBuildConfig(
            package_path=build_result.output_path,
            signature_path=signing_result.signature_path,
            key_id=signing_result.key_id,
            public_key_base64=signing_result.public_key_base64,
            output_path=evidence_path,
        )
    )

    assert report.valid is True
    assert report.package_path == Path("taroai-release.zip")
    assert report.package_sha256 == build_result.checksum_sha256
    assert report.signature_path == Path("taroai-release.zip.sig.json")
    assert report.signature_key_id == signing_result.key_id
    assert report.signature_valid is True
    assert report.public_key_base64 == signing_result.public_key_base64
    assert report.package_version == "0.1.0"
    assert report.app_version == "0.1.0"
    assert report.image_count == 5
    assert report.migration_count == 39
    assert report.required_service_count == 10
    assert evidence_path.exists()

    parsed = ReleaseTransferEvidenceReport.model_validate_json(evidence_path.read_text())
    assert parsed == report
    evidence_json = evidence_path.read_text()
    signature_envelope = json.loads(signing_result.signature_path.read_text())
    assert signature_envelope["signature"] not in evidence_json
    assert "private_key" not in evidence_json


def test_release_transfer_evidence_builder_preserves_existing_packet_when_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    build_result, signing_result = build_signed_release(tmp_path)
    evidence_path = tmp_path / "release-transfer-evidence.json"
    previous_bytes = b"previous transfer evidence"
    evidence_path.write_bytes(previous_bytes)
    original_write_text = Path.write_text

    def partial_evidence_write(path: Path, data: str, *args, **kwargs):
        if "release-transfer-evidence" in path.name:
            path.write_bytes(b"partial transfer evidence")
            raise RuntimeError("transfer evidence write failed")
        return original_write_text(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", partial_evidence_write)

    with pytest.raises(RuntimeError, match="transfer evidence write failed"):
        build_release_transfer_evidence(
            ReleaseTransferEvidenceBuildConfig(
                package_path=build_result.output_path,
                signature_path=signing_result.signature_path,
                key_id=signing_result.key_id,
                public_key_base64=signing_result.public_key_base64,
                output_path=evidence_path,
            )
        )

    assert evidence_path.read_bytes() == previous_bytes
    assert list(tmp_path.glob("*.tmp")) == []


def test_release_transfer_evidence_builder_rejects_invalid_signature_key(
    tmp_path: Path,
):
    build_result, signing_result = build_signed_release(tmp_path)
    other_key = private_key_base64(Ed25519PrivateKey.generate())

    with pytest.raises(ValueError, match="release package verification failed"):
        build_release_transfer_evidence(
            ReleaseTransferEvidenceBuildConfig(
                package_path=build_result.output_path,
                signature_path=signing_result.signature_path,
                key_id=signing_result.key_id,
                public_key_base64=other_key,
                output_path=tmp_path / "release-transfer-evidence.json",
            )
        )


def test_release_transfer_evidence_script_wraps_python_cli():
    script = Path("scripts/build-release-transfer-evidence.sh")

    text = script.read_text()

    assert "python -m taroai.deployment.transfer_evidence" in text
    assert "--package" in text
    assert "--signature" in text
    assert "--trusted-public-key" in text
    assert "--output" in text
