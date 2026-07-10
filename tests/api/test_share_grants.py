from datetime import timedelta

import pytest

from taroai.domain import utc_now
from taroai.sharing import (
    InMemoryShareGrantStore,
    ShareGrantCreate,
    ShareGrantStatus,
    share_grant_audit_metadata,
)


def create_share_grant(**overrides) -> ShareGrantCreate:
    payload = {
        "tenant_id": "tenant_acme",
        "resource_type": "artifact",
        "resource_id": "artifact_1",
        "subject_type": "user",
        "subject_id": "user_2",
        "permission": "view",
        "created_by_user_id": "admin_1",
        "reason": "Share generated report.",
        "expires_at": utc_now() + timedelta(days=7),
    }
    payload.update(overrides)
    return ShareGrantCreate(**payload)


def test_share_grant_create_validates_external_link_expiration_and_resource_permissions():
    grant = create_share_grant(resource_type="skill", permission="use")

    assert grant.resource_type == "skill"
    assert grant.permission == "use"

    with pytest.raises(ValueError, match="external_link grants require expires_at"):
        create_share_grant(subject_type="external_link", expires_at=None)

    with pytest.raises(
        ValueError,
        match="external_link subject_id must be at least 32 characters",
    ):
        create_share_grant(
            subject_type="external_link",
            subject_id="short-token",
            expires_at=utc_now() + timedelta(days=1),
        )

    with pytest.raises(
        ValueError,
        match="external_link grants only support view permission",
    ):
        create_share_grant(
            subject_type="external_link",
            subject_id="external_link_secret_with_enough_entropy",
            permission="admin",
            expires_at=utc_now() + timedelta(days=1),
        )

    with pytest.raises(
        ValueError,
        match="external_link grants only support artifact resources",
    ):
        create_share_grant(
            resource_type="run",
            subject_type="external_link",
            subject_id="external_link_secret_with_enough_entropy",
            expires_at=utc_now() + timedelta(days=1),
        )

    with pytest.raises(ValueError, match="publish is not supported for run"):
        create_share_grant(resource_type="run", permission="publish")


def test_share_grant_audit_metadata_redacts_external_link_subject_id():
    store = InMemoryShareGrantStore()
    grant = store.create_grant(
        create_share_grant(
            subject_type="external_link",
            subject_id="external_link_secret_001_with_enough_entropy",
            expires_at=utc_now() + timedelta(days=1),
        )
    )

    metadata = share_grant_audit_metadata(grant)

    assert metadata["subject_id"] == "[REDACTED]"
    assert metadata["external_link_id_present"] is True
    assert "external_link_secret_001_with_enough_entropy" not in str(metadata)


def test_in_memory_share_grants_authorize_active_subjects_and_reject_revoked_or_expired():
    store = InMemoryShareGrantStore()
    now = utc_now()

    active = store.create_grant(
        create_share_grant(
            subject_type="user",
            subject_id="user_2",
            expires_at=now + timedelta(hours=1),
        )
    )
    expired = store.create_grant(
        create_share_grant(
            resource_id="artifact_expired",
            subject_type="tenant",
            subject_id="tenant_acme",
            expires_at=now - timedelta(seconds=1),
        )
    )

    assert active.id.startswith("share_")
    assert store.authorize(
        tenant_id="tenant_acme",
        resource_type="artifact",
        resource_id="artifact_1",
        permission="view",
        user_id="user_2",
        now=now,
    )
    assert not store.authorize(
        tenant_id="tenant_acme",
        resource_type="artifact",
        resource_id="artifact_1",
        permission="view",
        user_id="user_3",
        now=now,
    )
    assert not store.authorize(
        tenant_id="tenant_other",
        resource_type="artifact",
        resource_id="artifact_1",
        permission="view",
        user_id="user_2",
        now=now,
    )
    assert not store.authorize(
        tenant_id="tenant_acme",
        resource_type="artifact",
        resource_id=expired.resource_id,
        permission="view",
        user_id="anyone",
        now=now,
    )

    revoked = store.revoke_grant(
        tenant_id="tenant_acme",
        grant_id=active.id,
        revoked_by_user_id="admin_1",
        now=now,
    )

    assert revoked.status == ShareGrantStatus.REVOKED
    assert revoked.revoked_by_user_id == "admin_1"
    assert not store.authorize(
        tenant_id="tenant_acme",
        resource_type="artifact",
        resource_id="artifact_1",
        permission="view",
        user_id="user_2",
        now=now,
    )


def test_in_memory_share_grants_match_group_workspace_and_tenant_subjects():
    store = InMemoryShareGrantStore()
    now = utc_now()

    store.create_grant(
        create_share_grant(
            resource_id="artifact_group",
            subject_type="group",
            subject_id="group_sales",
        )
    )
    store.create_grant(
        create_share_grant(
            resource_id="artifact_workspace",
            subject_type="workspace",
            subject_id="workspace_sales",
        )
    )
    store.create_grant(
        create_share_grant(
            resource_id="artifact_tenant",
            subject_type="tenant",
            subject_id="tenant_acme",
        )
    )

    assert store.authorize(
        tenant_id="tenant_acme",
        resource_type="artifact",
        resource_id="artifact_group",
        permission="view",
        user_id="user_2",
        group_ids=["group_sales"],
        now=now,
    )
    assert store.authorize(
        tenant_id="tenant_acme",
        resource_type="artifact",
        resource_id="artifact_workspace",
        permission="view",
        user_id="user_2",
        workspace_id="workspace_sales",
        now=now,
    )
    assert store.authorize(
        tenant_id="tenant_acme",
        resource_type="artifact",
        resource_id="artifact_tenant",
        permission="view",
        user_id="user_2",
        now=now,
    )
