import json
import re
import threading
from typing import Any, ClassVar

import httpx
from pydantic import BaseModel, ConfigDict, Field

from taroai.guardrails.models import (
    GuardrailAction,
    GuardrailDetectorFinding,
    GuardrailEvaluationRequest,
    GuardrailRedaction,
    GuardrailSeverity,
    GuardrailStage,
)


_HTTP_CLIENT: httpx.Client | None = None
_HTTP_CLIENT_LOCK = threading.Lock()


def _shared_http_client() -> httpx.Client:
    """Process-wide pooled HTTP client for guardrail detector calls."""
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None:
        with _HTTP_CLIENT_LOCK:
            if _HTTP_CLIENT is None:
                _HTTP_CLIENT = httpx.Client(
                    timeout=httpx.Timeout(5.0),
                    follow_redirects=True,
                )
    return _HTTP_CLIENT


class GuardrailHttpDetectorClient(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def post_json(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        timeout_seconds: int,
    ) -> dict[str, Any]:
        response = _shared_http_client().post(
            url,
            content=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={
                **headers,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=timeout_seconds,
        )
        # urllib raised HTTPError on non-2xx; raise_for_status preserves the
        # "HTTP error => detector failure action" behavior.
        response.raise_for_status()
        raw = response.content.decode("utf-8")
        if not raw:
            return {}
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return {}
        return parsed


class GuardrailHttpDetector(BaseModel):
    id: str = "http_guardrail_detector"
    endpoint_url: str = Field(min_length=1)
    api_key: str = ""
    timeout_seconds: int = Field(default=5, ge=1)
    stages: list[GuardrailStage] = Field(
        default_factory=lambda: [
            GuardrailStage.INPUT,
            GuardrailStage.MODEL_REQUEST,
            GuardrailStage.MODEL_RESPONSE,
            GuardrailStage.TOOL_REQUEST,
            GuardrailStage.TOOL_RESPONSE,
            GuardrailStage.ARTIFACT,
            GuardrailStage.MEMORY_WRITE,
        ]
    )
    failure_action: GuardrailAction = GuardrailAction.ALLOW
    client: Any = Field(default_factory=GuardrailHttpDetectorClient)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def evaluate(self, request: GuardrailEvaluationRequest) -> list[GuardrailDetectorFinding]:
        if request.stage not in self.stages:
            return []
        try:
            response = self.client.post_json(
                url=self.endpoint_url,
                payload={
                    "tenant_id": request.tenant_id,
                    "workspace_id": request.workspace_id,
                    "stage": request.stage.value,
                    "content": request.content,
                    "attributes": request.attributes,
                },
                headers=self._headers(),
                timeout_seconds=self.timeout_seconds,
            )
        except Exception:
            return self._failure_findings()
        return self._findings_from_response(response)

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {"Authorization": f"Bearer {self.api_key}"}

    def _failure_findings(self) -> list[GuardrailDetectorFinding]:
        if self.failure_action == GuardrailAction.ALLOW:
            return []
        return [
            GuardrailDetectorFinding(
                id=f"{self.id}.unavailable",
                detector_id=self.id,
                label="detector_unavailable",
                action=self.failure_action,
                severity=GuardrailSeverity.HIGH,
                message="Guardrail detector unavailable",
            )
        ]

    def _findings_from_response(self, response: dict[str, Any]) -> list[GuardrailDetectorFinding]:
        raw_findings = response.get("findings", [])
        if not isinstance(raw_findings, list):
            return []
        findings: list[GuardrailDetectorFinding] = []
        for index, raw_finding in enumerate(raw_findings, start=1):
            if not isinstance(raw_finding, dict):
                continue
            label = str(raw_finding.get("label") or "external_guardrail")
            finding_id = str(raw_finding.get("id") or label or index)
            findings.append(
                GuardrailDetectorFinding(
                    id=f"{self.id}.{finding_id}",
                    detector_id=self.id,
                    label=label,
                    action=GuardrailAction(raw_finding.get("action", GuardrailAction.WARN.value)),
                    severity=GuardrailSeverity(
                        raw_finding.get("severity", GuardrailSeverity.HIGH.value)
                    ),
                    message=f"External guardrail finding: {label}",
                    audit_required=bool(raw_finding.get("audit_required", True)),
                    redactions=self._redactions(raw_finding.get("redactions", [])),
                )
            )
        return findings

    def _redactions(self, raw_redactions: Any) -> list[GuardrailRedaction]:
        if not isinstance(raw_redactions, list):
            return []
        redactions: list[GuardrailRedaction] = []
        for raw_redaction in raw_redactions:
            if not isinstance(raw_redaction, dict):
                continue
            text = raw_redaction.get("text")
            if not isinstance(text, str) or not text:
                continue
            redactions.append(
                GuardrailRedaction(
                    text=text,
                    replacement=str(raw_redaction.get("replacement") or "[REDACTED]"),
                    case_sensitive=bool(raw_redaction.get("case_sensitive", False)),
                )
            )
        return redactions


class GuardrailPromptThreatDetector(BaseModel):
    id: str = "builtin_prompt_threat"
    stages: list[GuardrailStage] = Field(
        default_factory=lambda: [
            GuardrailStage.INPUT,
            GuardrailStage.MODEL_REQUEST,
            GuardrailStage.TOOL_REQUEST,
            GuardrailStage.MEMORY_WRITE,
        ]
    )
    action: GuardrailAction = GuardrailAction.BLOCK
    severity: GuardrailSeverity = GuardrailSeverity.HIGH
    replacement: str = "[REDACTED]"

    PROMPT_INJECTION_PATTERNS: ClassVar[tuple[str, ...]] = (
        r"\bignore\s+(?:all\s+)?previous\s+instructions\b",
        r"\bdisregard\s+(?:all\s+|previous\s+)?instructions\b",
        r"\breveal\s+(?:the\s+)?system\s+prompt\b",
        r"\bshow\s+(?:the\s+)?(?:system|developer)\s+(?:prompt|message)\b",
        r"\bdeveloper\s+message\b",
        r"\bjailbreak\b",
        r"\boverride\s+(?:the\s+)?(?:safety|policy|instructions)\b",
        r"\bbypass\s+(?:the\s+)?(?:safety|policy|guardrails?)\b",
    )
    DATA_EXFILTRATION_PATTERNS: ClassVar[tuple[str, ...]] = (
        r"\bsend\b.{0,120}\bto\s+https?://[^\s]+",
        r"\bpost\b.{0,120}\bto\s+(?:a\s+)?webhook\b",
        r"\bexport\b.{0,80}\bsecrets?\b",
        r"\bupload\b.{0,80}\bcredentials?\b",
        r"\bexfiltrat(?:e|ion)\b",
        r"\bpastebin\b",
    )

    def evaluate(self, request: GuardrailEvaluationRequest) -> list[GuardrailDetectorFinding]:
        if request.stage not in self.stages:
            return []
        findings: list[GuardrailDetectorFinding] = []
        injection_redactions = self._pattern_redactions(
            request.content,
            self.PROMPT_INJECTION_PATTERNS,
        )
        if injection_redactions:
            findings.append(
                self._finding(
                    label="prompt_injection",
                    message="Prompt-injection pattern detected",
                    redactions=injection_redactions,
                )
            )
        exfiltration_redactions = self._pattern_redactions(
            request.content,
            self.DATA_EXFILTRATION_PATTERNS,
        )
        if exfiltration_redactions:
            findings.append(
                self._finding(
                    label="data_exfiltration",
                    message="Data-exfiltration pattern detected",
                    redactions=exfiltration_redactions,
                )
            )
        return findings

    def _finding(
        self,
        label: str,
        message: str,
        redactions: list[GuardrailRedaction],
    ) -> GuardrailDetectorFinding:
        return GuardrailDetectorFinding(
            id=f"{self.id}.{label}",
            detector_id=self.id,
            label=label,
            action=self.action,
            severity=self.severity,
            message=message,
            redactions=redactions,
        )

    def _pattern_redactions(
        self,
        content: str,
        patterns: tuple[str, ...],
    ) -> list[GuardrailRedaction]:
        redactions: list[GuardrailRedaction] = []
        for pattern in patterns:
            for match in re.finditer(pattern, content, flags=re.IGNORECASE):
                text = match.group(0).strip().rstrip(".,;")
                if not text:
                    continue
                redactions.append(
                    GuardrailRedaction(
                        text=text,
                        replacement=self.replacement,
                        case_sensitive=False,
                    )
                )
        return self._dedupe_redactions(redactions)

    def _dedupe_redactions(self, redactions: list[GuardrailRedaction]) -> list[GuardrailRedaction]:
        deduped: list[GuardrailRedaction] = []
        seen: set[str] = set()
        for redaction in redactions:
            key = redaction.text.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(redaction)
        return deduped


class GuardrailSecretPatternDetector(BaseModel):
    id: str = "builtin_secret_pattern"
    stages: list[GuardrailStage] = Field(
        default_factory=lambda: [
            GuardrailStage.INPUT,
            GuardrailStage.MODEL_REQUEST,
            GuardrailStage.MODEL_RESPONSE,
            GuardrailStage.TOOL_REQUEST,
            GuardrailStage.TOOL_RESPONSE,
            GuardrailStage.ARTIFACT,
            GuardrailStage.MEMORY_WRITE,
        ]
    )
    action: GuardrailAction = GuardrailAction.REDACT
    severity: GuardrailSeverity = GuardrailSeverity.HIGH
    replacement: str = "[REDACTED]"

    def evaluate(self, request: GuardrailEvaluationRequest) -> list[GuardrailDetectorFinding]:
        if request.stage not in self.stages:
            return []
        redactions = self._secret_assignment_redactions(request.content)
        if not redactions:
            return []
        return [
            GuardrailDetectorFinding(
                id=f"{self.id}.secret_assignment",
                detector_id=self.id,
                label="secret_assignment",
                action=self.action,
                severity=self.severity,
                message="Secret-like credential material detected",
                redactions=redactions,
            )
        ]

    def _secret_assignment_redactions(self, content: str) -> list[GuardrailRedaction]:
        redactions: list[GuardrailRedaction] = []
        patterns = [
            r"(?i)\b(?:api[_-]?key|access[_-]?token|secret|password|credential)\s*[:=]\s*['\"]?[A-Za-z0-9._/\-+=]{12,}['\"]?",
            r"(?i)\bbearer\s+[A-Za-z0-9._/\-+=]{20,}",
            r"\bAKIA[0-9A-Z]{16}\b",
            r"\bsk-[A-Za-z0-9][A-Za-z0-9._\-]{18,}\b",
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, content):
                text = match.group(0).rstrip(".,;")
                if not text:
                    continue
                redactions.append(
                    GuardrailRedaction(
                        text=text,
                        replacement=self.replacement,
                        case_sensitive=True,
                    )
                )
        return self._dedupe_redactions(redactions)

    def _dedupe_redactions(self, redactions: list[GuardrailRedaction]) -> list[GuardrailRedaction]:
        deduped: list[GuardrailRedaction] = []
        seen: set[str] = set()
        for redaction in redactions:
            if redaction.text in seen:
                continue
            seen.add(redaction.text)
            deduped.append(redaction)
        return deduped
