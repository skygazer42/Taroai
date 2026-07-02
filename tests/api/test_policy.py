from fastapi.testclient import TestClient
from pydantic import Field

from taroai.app import create_app
from taroai.identity import (
    InMemoryIdentityService,
    PasswordHasher,
    Permission,
    Role,
    UserAccountCreate,
)
from taroai.policy import (
    IdentityPolicyService,
    PolicyDecision,
    PolicyEffect,
    PolicyRequest,
    PolicyService,
)


def create_billing_reader_identity():
    identity = InMemoryIdentityService(password_hasher=PasswordHasher(salt="test_salt"))
    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="billing-reader@example.com",
            display_name="Billing Reader",
            password="correct horse battery staple",
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_billing_reader",
            name="Billing Reader",
            permissions=[
                Permission(action="billing.read", resource="tenant:tenant_acme"),
            ],
        )
    )
    identity.assign_role("tenant_acme", account.id, "role_billing_reader")
    return identity, account


def test_identity_policy_service_allows_identity_permission_and_denies_missing_permission():
    identity, account = create_billing_reader_identity()
    service = IdentityPolicyService(identity_service=identity)

    allowed = service.decide(
        PolicyRequest(
            tenant_id="tenant_acme",
            user_id=account.id,
            action="billing.read",
            resource="tenant:tenant_acme",
        )
    )
    denied = service.decide(
        PolicyRequest(
            tenant_id="tenant_acme",
            user_id=account.id,
            action="billing.admin",
            resource="tenant:tenant_acme",
        )
    )

    assert allowed.effect == PolicyEffect.ALLOW
    assert allowed.allowed is True
    assert denied.effect == PolicyEffect.DENY
    assert denied.allowed is False
    assert denied.missing_permissions == ["billing.admin"]


class RecordingPolicyService(PolicyService):
    requests: list[PolicyRequest] = Field(default_factory=list)

    def decide(self, request: PolicyRequest) -> PolicyDecision:
        self.requests.append(request)
        return PolicyDecision.allow()


class DenyingPolicyService(PolicyService):
    requests: list[PolicyRequest] = Field(default_factory=list)

    def decide(self, request: PolicyRequest) -> PolicyDecision:
        self.requests.append(request)
        return PolicyDecision.deny(
            reason="Policy denied: billing.read on tenant:tenant_acme",
            missing_permissions=["billing.read"],
        )


def test_app_authorizes_tenant_operations_through_policy_service():
    identity, account = create_billing_reader_identity()
    policy_service = RecordingPolicyService()
    client = TestClient(create_app(identity_service=identity, policy_service=policy_service))

    response = client.get(
        "/api/billing/meters",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id},
    )

    assert response.status_code == 200
    assert [request.action for request in policy_service.requests] == ["billing.read"]
    assert [request.resource for request in policy_service.requests] == ["tenant:tenant_acme"]
    assert [request.tenant_id for request in policy_service.requests] == ["tenant_acme"]
    assert [request.user_id for request in policy_service.requests] == [account.id]


def test_app_converts_policy_denial_to_tenant_access_error_shape():
    identity, account = create_billing_reader_identity()
    policy_service = DenyingPolicyService()
    client = TestClient(create_app(identity_service=identity, policy_service=policy_service))

    response = client.get(
        "/api/billing/meters",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "tenant_access_denied"
    assert [request.action for request in policy_service.requests] == ["billing.read"]
