from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from taroai.audit import AuditEventCreate
from taroai.domain import utc_now
from taroai.licensing.models import (
    Entitlement,
    EntitlementDecision,
    LicenseEntitlementDeniedError,
    LicenseKey,
    LicenseStatus,
    LicenseValidationResult,
    LicensedFeature,
)
from taroai.licensing.signing import (
    LicenseSignatureVerificationError,
    LicenseSignatureVerifier,
    SignedLicenseEnvelope,
)


class LicenseService(BaseModel):
    audit_service: Any | None = None
    signature_verifier: LicenseSignatureVerifier | None = None
    runtime_enforcement_enabled: bool = False
    validation_store: Any | None = None

    model_config = ConfigDict(arbitrary_types_allowed=True)

    _last_status_by_license: dict[str, LicenseStatus] = PrivateAttr(default_factory=dict)
    _active_validation_by_tenant: dict[str, LicenseValidationResult] = PrivateAttr(
        default_factory=dict
    )

    def validate_license(
        self,
        license_key: LicenseKey,
        deployment_mode: str,
        now: datetime | None = None,
        source: str = "document",
    ) -> LicenseValidationResult:
        checked_at = now or utc_now()
        status = LicenseStatus.ACTIVE
        reason = ""
        if deployment_mode not in license_key.deployment_modes:
            status = LicenseStatus.INVALID
            reason = f"license does not allow {deployment_mode} deployment"
        elif checked_at >= license_key.expires_at:
            status = LicenseStatus.EXPIRED
            reason = "license expired"

        result = LicenseValidationResult(
            license=license_key,
            status=status,
            deployment_mode=deployment_mode,
            source=source,
            reason=reason,
        )
        self._record_status_change(result)
        return result

    def validate_offline_file(
        self,
        path: str | Path,
        deployment_mode: str,
        now: datetime | None = None,
    ) -> LicenseValidationResult:
        license_key = LicenseKey.model_validate_json(Path(path).read_text())
        if not license_key.offline_validation_allowed:
            result = LicenseValidationResult(
                license=license_key,
                status=LicenseStatus.INVALID,
                deployment_mode=deployment_mode,
                source="offline_file",
                reason="license does not allow offline validation",
            )
            self._record_status_change(result)
            return result
        return self.validate_license(
            license_key,
            deployment_mode=deployment_mode,
            now=now,
            source="offline_file",
        )

    def validate_signed_offline_file(
        self,
        path: str | Path,
        deployment_mode: str,
        now: datetime | None = None,
    ) -> LicenseValidationResult:
        license_key = self.verify_signed_offline_envelope(
            SignedLicenseEnvelope.model_validate_json(Path(path).read_text())
        )
        return self.validate_signed_offline_license_key(
            license_key,
            deployment_mode=deployment_mode,
            now=now,
        )

    def validate_signed_offline_envelope(
        self,
        envelope: SignedLicenseEnvelope,
        deployment_mode: str,
        now: datetime | None = None,
    ) -> LicenseValidationResult:
        license_key = self.verify_signed_offline_envelope(envelope)
        return self.validate_signed_offline_license_key(
            license_key,
            deployment_mode=deployment_mode,
            now=now,
        )

    def verify_signed_offline_envelope(
        self,
        envelope: SignedLicenseEnvelope,
    ) -> LicenseKey:
        if self.signature_verifier is None:
            raise LicenseSignatureVerificationError(
                "license signature verifier is not configured"
            )
        return self.signature_verifier.verify_envelope(envelope)

    def validate_signed_offline_license_key(
        self,
        license_key: LicenseKey,
        deployment_mode: str,
        now: datetime | None = None,
    ) -> LicenseValidationResult:
        if not license_key.offline_validation_allowed:
            result = LicenseValidationResult(
                license=license_key,
                status=LicenseStatus.INVALID,
                deployment_mode=deployment_mode,
                source="signed_offline_file",
                reason="license does not allow offline validation",
            )
            self._record_status_change(result)
            return result
        return self.validate_license(
            license_key,
            deployment_mode=deployment_mode,
            now=now,
            source="signed_offline_file",
        )

    def check_entitlement(
        self,
        validation: LicenseValidationResult,
        feature: LicensedFeature,
        requested_amount: int = 1,
        now: datetime | None = None,
    ) -> EntitlementDecision:
        if validation.status != LicenseStatus.ACTIVE:
            return EntitlementDecision(
                feature=feature,
                allowed=False,
                requested_amount=requested_amount,
                reason=f"license status is {validation.status.value}",
            )

        entitlement = self._find_entitlement(validation.license.entitlements, feature)
        if entitlement is None or not entitlement.enabled:
            return EntitlementDecision(
                feature=feature,
                allowed=False,
                requested_amount=requested_amount,
                reason=f"missing entitlement for {feature.value}",
            )

        checked_at = now or utc_now()
        if entitlement.expires_at is not None and checked_at >= entitlement.expires_at:
            return EntitlementDecision(
                feature=feature,
                allowed=False,
                requested_amount=requested_amount,
                limit=entitlement.limit,
                reason=f"{feature.value} entitlement expired",
            )

        if entitlement.limit is not None and requested_amount > entitlement.limit:
            return EntitlementDecision(
                feature=feature,
                allowed=False,
                requested_amount=requested_amount,
                limit=entitlement.limit,
                reason=f"{feature.value} entitlement limit exceeded",
            )

        return EntitlementDecision(
            feature=feature,
            allowed=True,
            requested_amount=requested_amount,
            limit=entitlement.limit,
        )

    def activate_validation(self, validation: LicenseValidationResult) -> LicenseValidationResult:
        self._active_validation_by_tenant[validation.license.tenant_id] = validation
        if self.validation_store is not None:
            self.validation_store.save_license_validation(validation)
        return validation

    def get_active_validation(self, tenant_id: str) -> LicenseValidationResult | None:
        validation = self._active_validation_by_tenant.get(tenant_id)
        if validation is not None:
            return validation
        if self.validation_store is None:
            return None
        validation = self.validation_store.get_active_license_validation(tenant_id)
        if validation is not None:
            self._active_validation_by_tenant[tenant_id] = validation
        return validation

    def require_entitlement(
        self,
        tenant_id: str,
        feature: LicensedFeature,
        requested_amount: int = 1,
        now: datetime | None = None,
    ) -> EntitlementDecision:
        if not self.runtime_enforcement_enabled:
            return EntitlementDecision(
                feature=feature,
                allowed=True,
                requested_amount=requested_amount,
                reason="license runtime enforcement disabled",
            )

        validation = self.get_active_validation(tenant_id)
        if validation is None:
            raise LicenseEntitlementDeniedError(
                f"active license is required for {feature.value}",
                metadata={
                    "tenant_id": tenant_id,
                    "feature": feature.value,
                    "requested_amount": requested_amount,
                },
            )

        decision = self.check_entitlement(
            validation,
            feature,
            requested_amount=requested_amount,
            now=now,
        )
        if decision.allowed:
            return decision
        raise LicenseEntitlementDeniedError(
            decision.reason or f"license entitlement denied for {feature.value}",
            metadata={
                "tenant_id": tenant_id,
                "license_id": validation.license.id,
                "feature": feature.value,
                "requested_amount": requested_amount,
                "limit": decision.limit,
                "reason": decision.reason,
            },
        )

    def _find_entitlement(
        self,
        entitlements: list[Entitlement],
        feature: LicensedFeature,
    ) -> Entitlement | None:
        for entitlement in entitlements:
            if entitlement.feature == feature:
                return entitlement
        return None

    def _record_status_change(self, result: LicenseValidationResult) -> None:
        previous_status = self._last_status_by_license.get(result.license.id)
        if previous_status == result.status:
            return
        self._last_status_by_license[result.license.id] = result.status
        if self.audit_service is None:
            return
        self.audit_service.record(
            AuditEventCreate(
                tenant_id=result.license.tenant_id,
                event_type="license.status_changed",
                metadata={
                    "license_id": result.license.id,
                    "customer_name": result.license.customer_name,
                    "previous_status": previous_status.value if previous_status is not None else None,
                    "status": result.status.value,
                    "deployment_mode": result.deployment_mode,
                    "source": result.source,
                    "reason": result.reason,
                    "entitlements_count": len(result.license.entitlements),
                },
            )
        )
