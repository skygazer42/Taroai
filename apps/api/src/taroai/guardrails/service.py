import re
from typing import Any

from pydantic import BaseModel, Field

from taroai.guardrails.models import (
    GuardrailAction,
    GuardrailDecision,
    GuardrailDetectorFinding,
    GuardrailEvaluationRequest,
    GuardrailRedaction,
    GuardrailRule,
    GuardrailSeverity,
)


ACTION_PRIORITY = {
    GuardrailAction.ALLOW: 0,
    GuardrailAction.WARN: 1,
    GuardrailAction.REDACT: 2,
    GuardrailAction.REQUIRE_APPROVAL: 3,
    GuardrailAction.BLOCK: 4,
    GuardrailAction.QUARANTINE_RUN: 5,
}

SEVERITY_PRIORITY = {
    GuardrailSeverity.LOW: 0,
    GuardrailSeverity.MEDIUM: 1,
    GuardrailSeverity.HIGH: 2,
    GuardrailSeverity.CRITICAL: 3,
}


class InMemoryGuardrailService(BaseModel):
    rules: list[GuardrailRule] = Field(default_factory=list)
    detectors: list[Any] = Field(default_factory=list)

    def add_rule(self, rule: GuardrailRule) -> GuardrailRule:
        stored = rule.model_copy(deep=True)
        self.rules.append(stored)
        return stored.model_copy(deep=True)

    def evaluate(self, request: GuardrailEvaluationRequest) -> GuardrailDecision:
        matched_rules = [rule for rule in self.rules if self._matches(rule, request)]
        detector_findings = [
            finding
            for detector in self.detectors
            for finding in detector.evaluate(request)
        ]
        if not matched_rules and not detector_findings:
            return GuardrailDecision.allow()

        selected_action, selected_severity, selected_message = self._selected_outcome(
            matched_rules,
            detector_findings,
        )
        redacted_content = self._redacted_content(request, matched_rules, detector_findings)
        return GuardrailDecision(
            action=selected_action,
            allowed=selected_action not in {GuardrailAction.BLOCK, GuardrailAction.QUARANTINE_RUN},
            approval_required=selected_action == GuardrailAction.REQUIRE_APPROVAL,
            blocked=selected_action in {GuardrailAction.BLOCK, GuardrailAction.QUARANTINE_RUN},
            redacted_content=redacted_content,
            redactions=self._redactions(matched_rules, detector_findings),
            warnings=[
                rule.message for rule in matched_rules if rule.action == GuardrailAction.WARN
            ] + [
                finding.message
                for finding in detector_findings
                if finding.action == GuardrailAction.WARN
            ],
            matched_rule_ids=[rule.id for rule in matched_rules],
            detector_finding_ids=[finding.id for finding in detector_findings],
            detector_findings=detector_findings,
            severity=selected_severity,
            message=selected_message,
            audit_required=any(rule.audit_required for rule in matched_rules)
            or any(finding.audit_required for finding in detector_findings),
        )

    def _selected_outcome(
        self,
        matched_rules: list[GuardrailRule],
        detector_findings: list[GuardrailDetectorFinding],
    ) -> tuple[GuardrailAction, GuardrailSeverity | None, str | None]:
        outcomes = [
            (rule.action, rule.severity, rule.message)
            for rule in matched_rules
        ] + [
            (finding.action, finding.severity, finding.message)
            for finding in detector_findings
        ]
        selected = max(
            outcomes,
            key=lambda outcome: (
                ACTION_PRIORITY[outcome[0]],
                SEVERITY_PRIORITY[outcome[1]],
            ),
        )
        return selected

    def _matches(
        self,
        rule: GuardrailRule,
        request: GuardrailEvaluationRequest,
    ) -> bool:
        if rule.tenant_id is not None and rule.tenant_id != request.tenant_id:
            return False
        if rule.workspace_id is not None and rule.workspace_id != request.workspace_id:
            return False
        if rule.stage != request.stage:
            return False
        return rule.condition.matches(request.content, request.attributes)

    def _redacted_content(
        self,
        request: GuardrailEvaluationRequest,
        matched_rules: list[GuardrailRule],
        detector_findings: list[GuardrailDetectorFinding],
    ) -> str | None:
        redacted = request.content
        changed = False
        for rule in matched_rules:
            if rule.action != GuardrailAction.REDACT:
                continue
            redacted = rule.condition.redact(redacted, rule.redaction_replacement)
            changed = True
        for finding in detector_findings:
            if finding.action != GuardrailAction.REDACT:
                continue
            redacted = self._apply_redactions(redacted, finding.redactions)
            changed = True
        if not changed:
            return None
        return redacted

    def _redactions(
        self,
        matched_rules: list[GuardrailRule],
        detector_findings: list[GuardrailDetectorFinding],
    ) -> list[GuardrailRedaction]:
        redactions: list[GuardrailRedaction] = []
        for rule in matched_rules:
            if rule.action != GuardrailAction.REDACT:
                continue
            redactions.extend(
                GuardrailRedaction(
                    text=term,
                    replacement=rule.redaction_replacement,
                    case_sensitive=rule.condition.case_sensitive,
                )
                for term in rule.condition.text_contains
            )
        for finding in detector_findings:
            if finding.action == GuardrailAction.REDACT:
                redactions.extend(finding.redactions)
        return redactions

    def _apply_redactions(self, content: str, redactions: list[GuardrailRedaction]) -> str:
        redacted = content
        for redaction in redactions:
            flags = 0 if redaction.case_sensitive else re.IGNORECASE
            redacted = re.sub(
                re.escape(redaction.text),
                redaction.replacement,
                redacted,
                flags=flags,
            )
        return redacted
