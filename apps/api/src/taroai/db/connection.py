import re
import sqlite3
from typing import Any

from taroai.db.models import DatabaseConfig


class DatabaseConnectionError(RuntimeError):
    pass


class PostgresConnectionAdapter:
    def __init__(self, connection: Any):
        self.connection = connection

    def __enter__(self):
        entered = self.connection.__enter__()
        if entered is not None:
            self.connection = entered
        return self

    def __exit__(self, error_type, error, traceback):
        return self.connection.__exit__(error_type, error, traceback)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.connection, name)

    def execute(self, sql: str, params: tuple | list | None = None):
        resolved_params = tuple(params or ())
        tenant_id = tenant_context_value(sql, resolved_params)
        if tenant_id is not None:
            self.connection.execute(
                "SELECT set_config('taroai.tenant_id', %s, true)",
                (tenant_id,),
            )
        return self.connection.execute(
            postgres_sql(sql),
            resolved_params,
        )


class PooledPostgresConnection:
    def __init__(self, pool: Any):
        self.pool = pool
        self.context = None
        self.connection = None

    def __enter__(self):
        self.context = self.pool.connection()
        self.connection = self.context.__enter__()
        self.connection.execute("RESET taroai.tenant_id")
        return PostgresConnectionAdapter(self.connection)

    def __exit__(self, error_type, error, traceback):
        if error_type is None and self.connection is not None:
            self.connection.execute("RESET taroai.tenant_id")
        return self.context.__exit__(error_type, error, traceback)


_POSTGRES_POOLS: dict[tuple[str, int, int, int], Any] = {}


def close_database_pools() -> None:
    for pool in _POSTGRES_POOLS.values():
        pool.close()
    _POSTGRES_POOLS.clear()


def connect_database(config: DatabaseConfig):
    if config.dialect == "sqlite":
        path = config.sqlite_path
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        return connection
    if config.dialect == "postgresql":
        return PooledPostgresConnection(_postgres_pool(config))
    raise DatabaseConnectionError(f"Unsupported database dialect: {config.dialect}")


def _postgres_pool(config: DatabaseConfig):
    key = (
        config.url,
        config.pool_min_size,
        config.pool_max_size,
        config.pool_timeout_seconds,
    )
    existing_pool = _POSTGRES_POOLS.get(key)
    if existing_pool is not None:
        return existing_pool
    pool = _postgres_pool_factory()(
        config.url,
        min_size=config.pool_min_size,
        max_size=config.pool_max_size,
        timeout=config.pool_timeout_seconds,
    )
    _POSTGRES_POOLS[key] = pool
    return pool


def _postgres_pool_factory():
    try:
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool
    except ImportError as error:
        raise DatabaseConnectionError(
            "PostgreSQL database URLs require psycopg_pool to be installed"
        ) from error

    def create_pool(conninfo: str, **kwargs):
        return ConnectionPool(
            conninfo,
            kwargs={"row_factory": dict_row},
            **kwargs,
        )

    return create_pool


def postgres_sql(sql: str) -> str:
    translated = " ".join(sql.strip().split())
    translated = _translate_insert_or_replace(translated)
    translated = _translate_insert_or_ignore(translated)
    return translated.replace("?", "%s")


def tenant_context_value(sql: str, params: tuple | list) -> str | None:
    if not params:
        return None
    match = re.search(r"\btenant_id\b\s*=\s*\?", sql, flags=re.IGNORECASE)
    if match is not None:
        placeholder_index = sql[: match.start()].count("?")
        if placeholder_index < len(params):
            return str(params[placeholder_index])
    insert_match = re.search(
        r"INSERT\s+(?:OR\s+(?:IGNORE|REPLACE)\s+)?INTO\s+[A-Za-z_]+\s*\((?P<columns>[^)]*)\)",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if insert_match is None:
        return None
    columns = [
        column.strip().strip('"').lower()
        for column in insert_match.group("columns").split(",")
    ]
    if "tenant_id" not in columns:
        return None
    tenant_index = columns.index("tenant_id")
    if tenant_index >= len(params):
        return None
    return str(params[tenant_index])


def _translate_insert_or_ignore(sql: str) -> str:
    if not sql.upper().startswith("INSERT OR IGNORE INTO"):
        return sql
    translated = re.sub(
        r"^INSERT\s+OR\s+IGNORE\s+INTO",
        "INSERT INTO",
        sql,
        flags=re.IGNORECASE,
    )
    return f"{translated} ON CONFLICT DO NOTHING"


def _translate_insert_or_replace(sql: str) -> str:
    if not sql.upper().startswith("INSERT OR REPLACE INTO IDEMPOTENCY_RECORDS"):
        return sql
    translated = re.sub(
        r"^INSERT\s+OR\s+REPLACE\s+INTO",
        "INSERT INTO",
        sql,
        flags=re.IGNORECASE,
    )
    return (
        f"{translated} "
        "ON CONFLICT (tenant_id, key, method, path) DO UPDATE SET "
        "request_hash = EXCLUDED.request_hash, "
        "status_code = EXCLUDED.status_code, "
        "response_body = EXCLUDED.response_body, "
        "created_at = EXCLUDED.created_at"
    )
