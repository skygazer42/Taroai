from datetime import timedelta
from pathlib import Path

import pytest

from taroai.db import DatabaseConfig, MigrationRunner
from taroai.domain import utc_now
from taroai.sharing import ShareGrantCreate, ShareGrantStatus, SqlShareGrantStore
from taroai.store import NotFoundError


def test_sql_share_grant_store_persists_grants_and_revocation_across_instances(
    tmp_path: Path,
):
    database_url = f"sqlite:///{tmp_path / 'share-grants.sqlite3'}"
    config = DatabaseConfig(url=database_url)
    MigrationRunner(
        config=config,
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    now = utc_now()

    store = SqlShareGrantStore(config=config)
    created = store.create_grant(
        ShareGrantCreate(
            tenant_id="tenant_acme",
            resource_type="artifact",
            resource_id="artifact_1",
            subject_type="user",
            subject_id="user_2",
            permission="view",
            created_by_user_id="admin_1",
            reason="Share quarterly artifact.",
            expires_at=now + timedelta(days=1),
        )
    )

    restarted = SqlShareGrantStore(config=config)
    grants = restarted.list_grants(
        tenant_id="tenant_acme",
        resource_type="artifact",
        resource_id="artifact_1",
    )

    assert [grant.id for grant in grants] == [created.id]
    assert restarted.get_grant("tenant_acme", created.id) == created
    assert restarted.authorize(
        tenant_id="tenant_acme",
        resource_type="artifact",
        resource_id="artifact_1",
        permission="view",
        user_id="user_2",
        now=now,
    )
    assert not restarted.authorize(
        tenant_id="tenant_other",
        resource_type="artifact",
        resource_id="artifact_1",
        permission="view",
        user_id="user_2",
        now=now,
    )
    with pytest.raises(NotFoundError):
        restarted.get_grant("tenant_other", created.id)

    revoked = restarted.revoke_grant(
        tenant_id="tenant_acme",
        grant_id=created.id,
        revoked_by_user_id="admin_2",
        now=now,
    )
    after_revoke = SqlShareGrantStore(config=config)

    assert revoked.status == ShareGrantStatus.REVOKED
    assert (
        after_revoke.get_grant("tenant_acme", created.id).status
        == ShareGrantStatus.REVOKED
    )
    assert not after_revoke.authorize(
        tenant_id="tenant_acme",
        resource_type="artifact",
        resource_id="artifact_1",
        permission="view",
        user_id="user_2",
        now=now,
    )
