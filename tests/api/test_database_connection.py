from pathlib import Path

import pytest

import taroai.db.connection as db_connection
from taroai.db import DatabaseConfig
from taroai.db.connection import (
    PooledPostgresConnection,
    PostgresConnectionAdapter,
    close_database_pools,
    connect_database,
)


class RecordingCursor:
    def fetchone(self):
        return None

    def fetchall(self):
        return []


class RecordingConnection:
    def __init__(self):
        self.executed: list[tuple[str, tuple]] = []
        self.entered = False
        self.exited = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, _error_type, _error, _traceback):
        self.exited = True
        return False

    def execute(self, sql: str, params: tuple | list | None = None):
        self.executed.append((sql, tuple(params or ())))
        return RecordingCursor()


class RecordingPool:
    def __init__(self, conninfo: str, **kwargs):
        self.conninfo = conninfo
        self.kwargs = kwargs
        self.closed = False
        self.connections: list[RecordingConnection] = []

    def connection(self):
        connection = RecordingConnection()
        self.connections.append(connection)
        return connection

    def close(self):
        self.closed = True


def test_database_config_resolves_sqlite_and_postgresql_urls(tmp_path: Path):
    sqlite_path = tmp_path / "taroai.sqlite3"
    sqlite_config = DatabaseConfig(url=f"sqlite:///{sqlite_path}")
    postgresql_config = DatabaseConfig(
        url="postgresql://taroai:taroai@postgres.internal:5432/taroai"
    )
    postgres_alias_config = DatabaseConfig(
        url="postgres://taroai:taroai@postgres.internal:5432/taroai"
    )

    assert sqlite_config.dialect == "sqlite"
    assert sqlite_config.sqlite_path == sqlite_path
    assert postgresql_config.dialect == "postgresql"
    assert postgresql_config.is_postgresql is True
    assert postgresql_config.pool_min_size == 1
    assert postgresql_config.pool_max_size == 10
    assert postgresql_config.pool_timeout_seconds == 30
    assert postgres_alias_config.dialect == "postgresql"
    with pytest.raises(ValueError, match="sqlite_path is only available"):
        _ = postgresql_config.sqlite_path
    with pytest.raises(ValueError, match="Unsupported database URL scheme"):
        DatabaseConfig(url="mysql://taroai:taroai@db/taroai")


def test_database_config_rejects_invalid_pool_bounds():
    with pytest.raises(ValueError, match="database pool max size"):
        DatabaseConfig(
            url="postgresql://taroai:taroai@postgres.internal:5432/taroai",
            pool_min_size=4,
            pool_max_size=2,
        )


def test_postgres_connection_adapter_translates_repository_sql():
    recording = RecordingConnection()
    adapter = PostgresConnectionAdapter(connection=recording)

    with adapter as connection:
        connection.execute(
            "INSERT OR IGNORE INTO tenants (id, name) VALUES (?, ?)",
            ("tenant_acme", "Acme"),
        )
        connection.execute(
            "SELECT * FROM runs WHERE tenant_id = ? AND status = ?",
            ("tenant_acme", "created"),
        )
        connection.execute(
            """
            INSERT OR REPLACE INTO idempotency_records (
                tenant_id, key, method, path, request_hash, status_code,
                response_body, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "tenant_acme",
                "run-create-001",
                "POST",
                "/api/runs",
                "hash_1",
                201,
                "{}",
                "2026-07-02T00:00:00+00:00",
            ),
        )

    assert recording.entered is True
    assert recording.exited is True
    assert recording.executed[0] == (
        "INSERT INTO tenants (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        ("tenant_acme", "Acme"),
    )
    assert recording.executed[1] == (
        "SELECT set_config('taroai.tenant_id', %s, true)",
        ("tenant_acme",),
    )
    assert recording.executed[2] == (
        "SELECT * FROM runs WHERE tenant_id = %s AND status = %s",
        ("tenant_acme", "created"),
    )
    assert recording.executed[3] == (
        "SELECT set_config('taroai.tenant_id', %s, true)",
        ("tenant_acme",),
    )
    assert "INSERT INTO idempotency_records" in recording.executed[4][0]
    assert "ON CONFLICT (tenant_id, key, method, path) DO UPDATE SET" in recording.executed[4][0]
    assert "request_hash = EXCLUDED.request_hash" in recording.executed[4][0]


def test_postgres_connection_adapter_sets_tenant_context_before_tenant_scoped_sql():
    recording = RecordingConnection()
    adapter = PostgresConnectionAdapter(connection=recording)

    with adapter as connection:
        connection.execute(
            "SELECT * FROM runs WHERE tenant_id = ? AND id = ?",
            ("tenant_acme", "run_123"),
        )
        connection.execute(
            "UPDATE runs SET status = ?, updated_at = ? WHERE tenant_id = ? AND id = ?",
            ("completed", "2026-07-02T00:00:00+00:00", "tenant_beta", "run_456"),
        )

    assert recording.executed[0] == (
        "SELECT set_config('taroai.tenant_id', %s, true)",
        ("tenant_acme",),
    )
    assert recording.executed[1] == (
        "SELECT * FROM runs WHERE tenant_id = %s AND id = %s",
        ("tenant_acme", "run_123"),
    )
    assert recording.executed[2] == (
        "SELECT set_config('taroai.tenant_id', %s, true)",
        ("tenant_beta",),
    )
    assert recording.executed[3] == (
        "UPDATE runs SET status = %s, updated_at = %s WHERE tenant_id = %s AND id = %s",
        ("completed", "2026-07-02T00:00:00+00:00", "tenant_beta", "run_456"),
    )


def test_postgres_connections_use_process_pool_until_closed(monkeypatch):
    pools: list[RecordingPool] = []

    def pool_factory(conninfo: str, **kwargs):
        pool = RecordingPool(conninfo, **kwargs)
        pools.append(pool)
        return pool

    close_database_pools()
    monkeypatch.setattr(db_connection, "_postgres_pool_factory", lambda: pool_factory)
    try:
        config = DatabaseConfig(
            url="postgresql://taroai:taroai@postgres.internal:5432/taroai",
            pool_min_size=2,
            pool_max_size=5,
            pool_timeout_seconds=7,
        )
        with connect_database(config) as first:
            first.execute("SELECT * FROM runs WHERE tenant_id = ?", ("tenant_acme",))
        with connect_database(config) as second:
            second.execute("SELECT * FROM runs WHERE tenant_id = ?", ("tenant_acme",))

        assert len(pools) == 1
        assert pools[0].conninfo == config.url
        assert pools[0].kwargs["min_size"] == 2
        assert pools[0].kwargs["max_size"] == 5
        assert pools[0].kwargs["timeout"] == 7
        assert [connection.executed[0] for connection in pools[0].connections] == [
            ("RESET taroai.tenant_id", ()),
            ("RESET taroai.tenant_id", ()),
        ]
        assert [connection.executed[1] for connection in pools[0].connections] == [
            ("SELECT set_config('taroai.tenant_id', %s, true)", ("tenant_acme",)),
            ("SELECT set_config('taroai.tenant_id', %s, true)", ("tenant_acme",)),
        ]
        assert [connection.executed[2][0] for connection in pools[0].connections] == [
            "SELECT * FROM runs WHERE tenant_id = %s",
            "SELECT * FROM runs WHERE tenant_id = %s",
        ]
        assert [connection.executed[3] for connection in pools[0].connections] == [
            ("RESET taroai.tenant_id", ()),
            ("RESET taroai.tenant_id", ()),
        ]

        close_database_pools()

        assert pools[0].closed is True
    finally:
        close_database_pools()


def test_pooled_postgres_connection_leaves_failed_context_to_pool_rollback():
    pool = RecordingPool("postgresql://taroai_app:taroai_app@postgres:5432/taroai")

    with pytest.raises(RuntimeError, match="query failed"):
        with PooledPostgresConnection(pool):
            raise RuntimeError("query failed")

    assert pool.connections[0].executed == [("RESET taroai.tenant_id", ())]
    assert pool.connections[0].exited is True


def test_sql_repository_implementations_use_common_connection_factory():
    repository_paths = [
        Path("apps/api/src/taroai/auth/sessions.py"),
        Path("apps/api/src/taroai/customer_success/repository.py"),
        Path("apps/api/src/taroai/db/migrations.py"),
        Path("apps/api/src/taroai/db/repository.py"),
        Path("apps/api/src/taroai/identity/repository.py"),
        Path("apps/api/src/taroai/knowledge/repository.py"),
        Path("apps/api/src/taroai/lifecycle/repository.py"),
        Path("apps/api/src/taroai/memory/repository.py"),
        Path("apps/api/src/taroai/model_gateway/repository.py"),
        Path("apps/api/src/taroai/skills/repository.py"),
        Path("apps/api/src/taroai/storage/repository.py"),
    ]

    for path in repository_paths:
        source = path.read_text()
        assert "sqlite3.connect" not in source
        assert "connect_database" in source
