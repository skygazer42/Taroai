from typing import Any

from taroai.guardrails.models import GuardrailStage


class _RuntimeGuardrailViolation(RuntimeError):
    def __init__(self, event_type: str, reason: str, metadata: dict[str, Any]):
        super().__init__(metadata.get("message") or event_type)
        self.event_type = event_type
        self.reason = reason
        self.metadata = metadata


class _RuntimeGuardrailApprovalRequired(RuntimeError):
    def __init__(
        self,
        event_type: str,
        stage: GuardrailStage,
        guardrail_key: str,
        reason: str,
        metadata: dict[str, Any],
    ):
        super().__init__(reason)
        self.event_type = event_type
        self.stage = stage
        self.guardrail_key = guardrail_key
        self.reason = reason
        self.metadata = metadata


class _RuntimeStorageContentRejected(RuntimeError):
    def __init__(self, metadata: dict[str, Any]):
        super().__init__("storage content rejected by scan policy")
        self.metadata = metadata


class _RuntimeSandboxArtifactPathRejected(RuntimeError):
    def __init__(self, metadata: dict[str, Any]):
        super().__init__("sandbox artifact path must be under /workspace/artifacts/")
        self.metadata = metadata
