import base64
import binascii
import json
from pathlib import Path
from typing import Any, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field

from taroai.licensing.models import LicenseKey


class LicenseSignatureVerificationError(ValueError):
    pass


class SignedLicenseEnvelope(BaseModel):
    algorithm: Literal["ed25519"] = "ed25519"
    key_id: str = Field(min_length=1)
    payload: dict[str, Any] = Field(min_length=1)
    signature: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class LicenseSignatureVerifier(BaseModel):
    trusted_public_keys: dict[str, str] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")

    def verify_file(self, path: str | Path) -> LicenseKey:
        envelope = SignedLicenseEnvelope.model_validate_json(Path(path).read_text())
        return self.verify_envelope(envelope)

    def verify_envelope(self, envelope: SignedLicenseEnvelope) -> LicenseKey:
        public_key_value = self.trusted_public_keys.get(envelope.key_id)
        if public_key_value is None:
            raise LicenseSignatureVerificationError("license signing key is not trusted")

        public_key = self._public_key_from_value(public_key_value)
        signature = self._decode_base64(
            envelope.signature,
            error_message="license signature material is invalid",
        )
        try:
            public_key.verify(signature, self._canonical_payload(envelope.payload))
        except InvalidSignature as error:
            raise LicenseSignatureVerificationError(
                "license signature verification failed"
            ) from error
        return LicenseKey.model_validate(envelope.payload)

    def _public_key_from_value(self, public_key_value: str) -> Ed25519PublicKey:
        public_key_bytes = self._decode_base64(
            public_key_value,
            error_message="trusted license signing key is invalid",
        )
        try:
            return Ed25519PublicKey.from_public_bytes(public_key_bytes)
        except ValueError as error:
            raise LicenseSignatureVerificationError(
                "trusted license signing key is invalid"
            ) from error

    def _decode_base64(self, value: str, error_message: str) -> bytes:
        try:
            return base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as error:
            raise LicenseSignatureVerificationError(error_message) from error

    def _canonical_payload(self, payload: dict[str, Any]) -> bytes:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
