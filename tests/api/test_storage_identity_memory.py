import hashlib
from datetime import timedelta

import pytest
from pydantic import ValidationError

from taroai.domain import utc_now
from taroai.identity import (
    InMemoryIdentityService,
    PasswordHasher,
    Permission,
    Role,
    UserAccount,
    UserAccountCreate,
)
from taroai.memory import (
    InMemoryLongTermMemoryService,
    InMemoryShortTermMemoryService,
    MemoryScopeType,
    MemoryStatus,
    MemoryWriteRequest,
    ShortTermMemoryWrite,
)
from taroai.storage import InMemoryStorageCatalog, StorageObjectCreate, StoragePurpose


class RecordingRedisMemoryClient:
    def __init__(self):
        self.values: dict[str, str] = {}
        self.expirations: dict[str, int] = {}
        self.deleted: list[str] = []

    def set(self, name: str, value: str, ex: int | None = None) -> None:
        self.values[name] = value
        if ex is not None:
            self.expirations[name] = ex

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def delete(self, name: str) -> None:
        self.deleted.append(name)
        self.values.pop(name, None)

    def scan_iter(self, match: str):
        prefix = match.removesuffix("*")
        return [name for name in sorted(self.values) if name.startswith(prefix)]


def test_storage_catalog_builds_tenant_scoped_object_keys():
    catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")

    stored = catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_123",
            purpose=StoragePurpose.ARTIFACT,
            filename="agent-result.md",
            content_type="text/markdown",
            size_bytes=128,
        )
    )

    assert stored.uri == "s3://taroai-artifacts/tenant_acme/workspace_sales/runs/run_123/artifacts/agent-result.md"
    assert catalog.list_for_run("tenant_acme", "run_123") == [stored]
    assert catalog.list_for_run("tenant_other", "run_123") == []


def test_storage_catalog_lists_active_objects_by_tenant_workspace_and_run_scope():
    catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    sales_run_object = catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_123",
            purpose=StoragePurpose.ARTIFACT,
            filename="agent-result.md",
            content_type="text/markdown",
            size_bytes=128,
        )
    )
    sales_internal_object = catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id=None,
            purpose=StoragePurpose.KNOWLEDGE_DOCUMENT,
            filename="source.md",
            content_type="text/markdown",
            size_bytes=256,
        )
    )
    support_object = catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_support",
            run_id="run_456",
            purpose=StoragePurpose.UPLOAD,
            filename="support.csv",
            content_type="text/csv",
            size_bytes=512,
        )
    )
    deleted_object = catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_deleted",
            purpose=StoragePurpose.UPLOAD,
            filename="deleted.csv",
            content_type="text/csv",
            size_bytes=1024,
        )
    )
    catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_other",
            workspace_id="workspace_other",
            run_id="run_999",
            purpose=StoragePurpose.UPLOAD,
            filename="other.csv",
            content_type="text/csv",
            size_bytes=2048,
        )
    )
    catalog.mark_deleted("tenant_acme", deleted_object.id, utc_now())

    assert catalog.list_active("tenant_acme") == [
        sales_run_object,
        sales_internal_object,
        support_object,
    ]
    assert catalog.list_active("tenant_acme", workspace_id="workspace_sales") == [
        sales_run_object,
        sales_internal_object,
    ]
    assert catalog.list_active("tenant_acme", run_id="run_123") == [sales_run_object]
    assert catalog.list_active("tenant_other") != catalog.list_active("tenant_acme")


def test_short_term_memory_supports_ttl_and_run_scope():
    service = InMemoryShortTermMemoryService()
    now = utc_now()

    entry = service.put(
        ShortTermMemoryWrite(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_123",
            key="planner.scratchpad",
            value={"next": "call research tool"},
            ttl_seconds=60,
        ),
        now=now,
    )

    assert entry.expires_at == now + timedelta(seconds=60)
    assert service.get("tenant_acme", "run_123", "planner.scratchpad", now=now).value == {
        "next": "call research tool"
    }
    assert service.get(
        "tenant_acme",
        "run_123",
        "planner.scratchpad",
        now=now + timedelta(seconds=61),
    ) is None


def test_short_term_memory_lists_and_deletes_run_entries():
    service = InMemoryShortTermMemoryService()
    now = utc_now()
    service.put(
        ShortTermMemoryWrite(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_123",
            key="planner.scratchpad",
            value={"next": "call research tool"},
            ttl_seconds=60,
        ),
        now=now,
    )
    service.put(
        ShortTermMemoryWrite(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_123",
            key="tool.last_result",
            value={"count": 3},
            ttl_seconds=60,
        ),
        now=now,
    )
    service.put(
        ShortTermMemoryWrite(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_other",
            key="planner.scratchpad",
            value={"next": "other run"},
            ttl_seconds=60,
        ),
        now=now,
    )
    service.put(
        ShortTermMemoryWrite(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_123",
            key="expired",
            value={"old": True},
            ttl_seconds=1,
        ),
        now=now,
    )

    listed = service.list_for_run("tenant_acme", "run_123", now=now + timedelta(seconds=2))
    deleted = service.delete("tenant_acme", "run_123", "planner.scratchpad")

    assert [entry.key for entry in listed] == ["planner.scratchpad", "tool.last_result"]
    assert deleted is True
    assert service.get("tenant_acme", "run_123", "planner.scratchpad", now=now) is None
    assert [entry.key for entry in service.list_for_run("tenant_acme", "run_123", now=now)] == [
        "tool.last_result"
    ]
    assert service.delete("tenant_acme", "run_123", "missing") is False


def test_short_term_memory_deletes_all_entries_for_tenant():
    service = InMemoryShortTermMemoryService()
    now = utc_now()
    service.put(
        ShortTermMemoryWrite(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_123",
            key="planner.scratchpad",
            value={"next": "call research tool"},
            ttl_seconds=60,
        ),
        now=now,
    )
    service.put(
        ShortTermMemoryWrite(
            tenant_id="tenant_acme",
            workspace_id="workspace_support",
            run_id="run_456",
            key="tool.last_result",
            value={"count": 3},
            ttl_seconds=60,
        ),
        now=now,
    )
    service.put(
        ShortTermMemoryWrite(
            tenant_id="tenant_other",
            workspace_id="workspace_sales",
            run_id="run_123",
            key="planner.scratchpad",
            value={"next": "other tenant"},
            ttl_seconds=60,
        ),
        now=now,
    )

    deleted_count = service.delete_for_tenant("tenant_acme")

    assert deleted_count == 2
    assert service.list_for_run("tenant_acme", "run_123", now=now) == []
    assert service.list_for_run("tenant_acme", "run_456", now=now) == []
    assert [entry.key for entry in service.list_for_run("tenant_other", "run_123", now=now)] == [
        "planner.scratchpad"
    ]


def test_redis_short_term_memory_uses_ttl_and_tenant_run_scope():
    from taroai.memory import RedisShortTermMemoryService

    client = RecordingRedisMemoryClient()
    service = RedisShortTermMemoryService(
        url="redis://localhost:6379/0",
        key_prefix="taroai:test:memory",
        client=client,
    )
    now = utc_now()

    entry = service.put(
        ShortTermMemoryWrite(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_123",
            key="planner.scratchpad",
            value={"next": "call approved research tool"},
            ttl_seconds=60,
        ),
        now=now,
    )
    stored_key = "taroai:test:memory:tenant:tenant_acme:run:run_123:key:planner.scratchpad"

    assert entry.expires_at == now + timedelta(seconds=60)
    assert client.expirations[stored_key] == 60
    assert service.get("tenant_acme", "run_123", "planner.scratchpad", now=now).value == {
        "next": "call approved research tool"
    }
    assert service.get("tenant_other", "run_123", "planner.scratchpad", now=now) is None
    assert service.get(
        "tenant_acme",
        "run_123",
        "planner.scratchpad",
        now=now + timedelta(seconds=61),
    ) is None
    assert stored_key in client.deleted


def test_redis_short_term_memory_lists_and_deletes_run_entries():
    from taroai.memory import RedisShortTermMemoryService

    client = RecordingRedisMemoryClient()
    service = RedisShortTermMemoryService(
        url="redis://localhost:6379/0",
        key_prefix="taroai:test:memory",
        client=client,
    )
    now = utc_now()
    service.put(
        ShortTermMemoryWrite(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_123",
            key="planner.scratchpad",
            value={"next": "call approved research tool"},
            ttl_seconds=60,
        ),
        now=now,
    )
    service.put(
        ShortTermMemoryWrite(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_123",
            key="tool.last_result",
            value={"count": 3},
            ttl_seconds=60,
        ),
        now=now,
    )
    service.put(
        ShortTermMemoryWrite(
            tenant_id="tenant_other",
            workspace_id="workspace_sales",
            run_id="run_123",
            key="planner.scratchpad",
            value={"next": "other tenant"},
            ttl_seconds=60,
        ),
        now=now,
    )

    listed = service.list_for_run("tenant_acme", "run_123", now=now)
    deleted = service.delete("tenant_acme", "run_123", "planner.scratchpad")

    assert [entry.key for entry in listed] == ["planner.scratchpad", "tool.last_result"]
    assert deleted is True
    assert service.get("tenant_acme", "run_123", "planner.scratchpad", now=now) is None
    assert [entry.key for entry in service.list_for_run("tenant_acme", "run_123", now=now)] == [
        "tool.last_result"
    ]
    assert service.delete("tenant_acme", "run_123", "missing") is False


def test_redis_short_term_memory_deletes_all_entries_for_tenant():
    from taroai.memory import RedisShortTermMemoryService

    client = RecordingRedisMemoryClient()
    service = RedisShortTermMemoryService(
        url="redis://localhost:6379/0",
        key_prefix="taroai:test:memory",
        client=client,
    )
    now = utc_now()
    service.put(
        ShortTermMemoryWrite(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_123",
            key="planner.scratchpad",
            value={"next": "call approved research tool"},
            ttl_seconds=60,
        ),
        now=now,
    )
    service.put(
        ShortTermMemoryWrite(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_456",
            key="tool.last_result",
            value={"count": 3},
            ttl_seconds=60,
        ),
        now=now,
    )
    service.put(
        ShortTermMemoryWrite(
            tenant_id="tenant_other",
            workspace_id="workspace_sales",
            run_id="run_123",
            key="planner.scratchpad",
            value={"next": "other tenant"},
            ttl_seconds=60,
        ),
        now=now,
    )

    deleted_count = service.delete_for_tenant("tenant_acme")

    assert deleted_count == 2
    assert sorted(client.deleted) == [
        "taroai:test:memory:tenant:tenant_acme:run:run_123:key:planner.scratchpad",
        "taroai:test:memory:tenant:tenant_acme:run:run_456:key:tool.last_result",
    ]
    assert service.get("tenant_acme", "run_123", "planner.scratchpad", now=now) is None
    assert service.get("tenant_acme", "run_456", "tool.last_result", now=now) is None
    assert service.get("tenant_other", "run_123", "planner.scratchpad", now=now) is not None


def test_long_term_memory_keeps_enterprise_scoped_records():
    service = InMemoryLongTermMemoryService()

    memory = service.write(
        MemoryWriteRequest(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            scope_type=MemoryScopeType.COMPANY,
            scope_id="tenant_acme",
            source_run_id="run_123",
            content="Use the approved enterprise tone.",
            created_by="user_1",
        )
    )

    assert service.list_by_scope("tenant_acme", MemoryScopeType.COMPANY, "tenant_acme") == [memory]
    assert service.list_by_scope("tenant_other", MemoryScopeType.COMPANY, "tenant_acme") == []


def test_long_term_memory_delete_for_tenant_expires_and_redacts_records():
    service = InMemoryLongTermMemoryService()
    active = service.write(
        MemoryWriteRequest(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            scope_type=MemoryScopeType.COMPANY,
            scope_id="tenant_acme",
            source_run_id="run_123",
            content="Customer-specific guidance.",
            created_by="user_1",
            metadata={"source": "conversation"},
        )
    )
    candidate = service.propose_candidate(
        MemoryWriteRequest(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            scope_type=MemoryScopeType.TEAM,
            scope_id="team_sales",
            source_run_id="run_456",
            content="Candidate guidance.",
            created_by="user_1",
            metadata={"source": "review"},
        )
    )
    other = service.write(
        MemoryWriteRequest(
            tenant_id="tenant_other",
            workspace_id="workspace_other",
            scope_type=MemoryScopeType.COMPANY,
            scope_id="tenant_other",
            source_run_id="run_other",
            content="Other tenant guidance.",
            created_by="user_2",
        )
    )

    deleted_ids = service.delete_for_tenant("tenant_acme")

    assert deleted_ids == [active.id, candidate.id]
    expired_active = service.get("tenant_acme", active.id)
    expired_candidate = service.get("tenant_acme", candidate.id)
    assert expired_active.status == MemoryStatus.EXPIRED
    assert expired_candidate.status == MemoryStatus.EXPIRED
    assert expired_active.content == ""
    assert expired_candidate.content == ""
    assert expired_active.metadata == {}
    assert expired_candidate.metadata == {}
    assert service.get("tenant_other", other.id) == other
    assert service.list_by_scope("tenant_acme", MemoryScopeType.COMPANY, "tenant_acme") == []


def test_identity_service_hashes_passwords_and_verifies_login():
    service = InMemoryIdentityService(password_hasher=PasswordHasher(salt="test_salt"))

    account = service.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="luke@example.com",
            display_name="Luke",
            password="correct horse battery staple",
        )
    )

    assert account.password_hash != "correct horse battery staple"
    assert account.password_hash.startswith("pbkdf2_sha256$")
    assert service.verify_password("tenant_acme", "luke@example.com", "correct horse battery staple")
    assert not service.verify_password("tenant_acme", "luke@example.com", "wrong password")


def test_user_account_models_normalize_email_addresses():
    request = UserAccountCreate(
        tenant_id="tenant_acme",
        email="  Luke@Example.COM  ",
        display_name="Luke",
        password="correct horse battery staple",
    )
    account = UserAccount(
        tenant_id="tenant_acme",
        email="  Luke@Example.COM  ",
        display_name="Luke",
        password_hash="hash",
    )

    assert request.email == "luke@example.com"
    assert account.email == "luke@example.com"


def test_password_hasher_uses_unique_salt_for_each_new_hash():
    hasher = PasswordHasher(salt="pepper_secret")

    first_hash = hasher.hash_password("correct horse battery staple")
    second_hash = hasher.hash_password("correct horse battery staple")

    assert first_hash != second_hash
    assert hasher.verify_password("correct horse battery staple", first_hash)
    assert hasher.verify_password("correct horse battery staple", second_hash)
    assert not hasher.verify_password("wrong password", first_hash)


def test_password_hasher_verifies_legacy_static_salt_hashes():
    legacy_salt = "test_salt"
    password = "correct horse battery staple"
    iterations = 600000
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        legacy_salt.encode("utf-8"),
        iterations,
    ).hex()
    legacy_hash = f"pbkdf2_sha256${iterations}${legacy_salt}${digest}"

    hasher = PasswordHasher(salt=legacy_salt)

    assert hasher.verify_password(password, legacy_hash)


def test_rbac_grants_permissions_through_roles():
    service = InMemoryIdentityService(password_hasher=PasswordHasher(salt="test_salt"))
    account = service.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="admin@example.com",
            display_name="Admin",
            password="admin password",
        )
    )
    service.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_admin",
            name="Admin",
            permissions=[
                Permission(action="runs.read", resource="workspace:*"),
                Permission(action="skills.publish", resource="tenant:tenant_acme"),
            ],
        )
    )
    service.assign_role("tenant_acme", account.id, "role_admin")

    assert service.has_permission("tenant_acme", account.id, "runs.read", "workspace:workspace_sales")
    assert service.has_permission("tenant_acme", account.id, "skills.publish", "tenant:tenant_acme")
    assert not service.has_permission("tenant_acme", account.id, "billing.admin", "tenant:tenant_acme")


def test_user_account_rejects_unknown_status_values():
    with pytest.raises(ValidationError):
        UserAccount(
            tenant_id="tenant_acme",
            email="luke@example.com",
            display_name="Luke",
            password_hash="hash",
            status="suspended",
        )


def test_identity_service_denies_permissions_for_inactive_users():
    service = InMemoryIdentityService(password_hasher=PasswordHasher(salt="test_salt"))
    account = service.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="admin@example.com",
            display_name="Admin",
            password="admin password",
        )
    )
    service.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_admin",
            name="Admin",
            permissions=[Permission(action="runs.read", resource="workspace:*")],
        )
    )
    service.assign_role("tenant_acme", account.id, "role_admin")

    assert service.has_permission("tenant_acme", account.id, "runs.read", "workspace:workspace_sales")

    pending = service.mark_user_pending("tenant_acme", account.id)
    assert pending.status == "pending"
    assert not service.has_permission("tenant_acme", account.id, "runs.read", "workspace:workspace_sales")

    active = service.activate_user("tenant_acme", account.id)
    assert active.status == "active"
    assert service.has_permission("tenant_acme", account.id, "runs.read", "workspace:workspace_sales")

    deleted = service.delete_user("tenant_acme", account.id)
    assert deleted.status == "deleted"
    assert not service.has_permission("tenant_acme", account.id, "runs.read", "workspace:workspace_sales")
