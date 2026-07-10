import hashlib
import hmac
import re
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from taroai.domain import utc_now


class TriggerWebhookSignatureError(RuntimeError):
    pass


class TriggerWebhookVerificationResult(BaseModel):
    verified: bool
    algorithm: str
    timestamp: datetime | None = None
    body_sha256: str = Field(min_length=64, max_length=64)


class TriggerWebhookVerifier(BaseModel):
    signing_secrets: list[str] = Field(default_factory=list, repr=False)
    tolerance_seconds: int = Field(default=300, ge=1)
    allow_unsigned: bool = False

    def verify(
        self,
        body: bytes,
        timestamp_header: str | None,
        signature_header: str | None,
        now: datetime | None = None,
    ) -> TriggerWebhookVerificationResult:
        body_sha256 = hashlib.sha256(body).hexdigest()
        if self.allow_unsigned and not self.signing_secrets:
            return TriggerWebhookVerificationResult(
                verified=False,
                algorithm="unsigned",
                body_sha256=body_sha256,
            )

        if not self.signing_secrets:
            raise TriggerWebhookSignatureError("webhook signing secret is not configured")
        if not timestamp_header or not signature_header:
            raise TriggerWebhookSignatureError("webhook signature header is missing")

        timestamp = self._parse_timestamp(timestamp_header)
        self._validate_timestamp(timestamp, now or utc_now())
        expected_payload = timestamp_header.strip().encode("ascii") + b"." + body
        provided_signatures = self._extract_signatures(signature_header)
        if not provided_signatures:
            raise TriggerWebhookSignatureError("webhook signature is invalid")

        for secret in self.signing_secrets:
            expected_signature = hmac.new(
                key=secret.encode("utf-8"),
                msg=expected_payload,
                digestmod=hashlib.sha256,
            ).hexdigest()
            if any(
                hmac.compare_digest(expected_signature, provided)
                for provided in provided_signatures
            ):
                return TriggerWebhookVerificationResult(
                    verified=True,
                    algorithm="hmac-sha256",
                    timestamp=timestamp,
                    body_sha256=body_sha256,
                )

        raise TriggerWebhookSignatureError("webhook signature is invalid")

    def _parse_timestamp(self, value: str) -> datetime:
        normalized = value.strip()
        if not normalized.isdigit():
            raise TriggerWebhookSignatureError("webhook timestamp is invalid")
        return datetime.fromtimestamp(int(normalized), tz=timezone.utc)

    def _validate_timestamp(self, timestamp: datetime, now: datetime) -> None:
        resolved_now = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
        age_seconds = abs((resolved_now - timestamp).total_seconds())
        if age_seconds > self.tolerance_seconds:
            raise TriggerWebhookSignatureError("webhook timestamp is outside tolerance")

    def _extract_signatures(self, value: str) -> list[str]:
        signatures: list[str] = []
        for part in value.split(","):
            normalized = part.strip()
            if normalized.startswith("sha256="):
                normalized = normalized.removeprefix("sha256=")
            if re.fullmatch(r"[a-fA-F0-9]{64}", normalized):
                signatures.append(normalized.lower())
        return signatures
