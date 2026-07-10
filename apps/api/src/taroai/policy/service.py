from pydantic import BaseModel

from taroai.identity import InMemoryIdentityService, SqlIdentityService
from taroai.policy.models import PolicyDecision, PolicyRequest


class PolicyService(BaseModel):
    def decide(self, request: PolicyRequest) -> PolicyDecision:
        raise NotImplementedError

    def decide_runtime_execution(self, request: PolicyRequest) -> PolicyDecision:
        return PolicyDecision.allow()

    def decide_runtime_step(self, request: PolicyRequest) -> PolicyDecision:
        return PolicyDecision.allow()


class IdentityPolicyService(PolicyService):
    identity_service: InMemoryIdentityService | SqlIdentityService

    def decide(self, request: PolicyRequest) -> PolicyDecision:
        if self.identity_service.has_permission(
            request.tenant_id,
            request.user_id,
            request.action,
            request.resource,
        ):
            return PolicyDecision.allow()
        return PolicyDecision.deny(
            reason=f"Permission denied: {request.action} on {request.resource}",
            missing_permissions=[request.action],
        )
