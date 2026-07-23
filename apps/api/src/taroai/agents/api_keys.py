import base64
import binascii
import hashlib
import hmac
import secrets
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from taroai.auth import AuthRequiredError
from taroai.db import DatabaseConfig
from taroai.db.connection import connect_database
from taroai.domain import new_id, utc_now
from taroai.store import NotFoundError, TenantAccessError


class AgentApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    agent_id: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class PublicAgentRunRequest(BaseModel):
    inputs: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class PublicAgentRunCreated(BaseModel):
    run_id: str
    agent_id: str
    agent_version: int
    status: str
    status_url: str
    events_url: str


class PublicAgentRunResult(BaseModel):
    run_id: str
    agent_id: str
    status: str
    output: str | None = None
    created_at: datetime
    updated_at: datetime


class AgentApiKey(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    agent_id: str
    name: str
    token_prefix: str
    token_hash: str = Field(exclude=True, repr=False)
    created_by_user_id: str
    created_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class AgentApiKeyStore(BaseModel):
    def create(self, api_key: AgentApiKey) -> AgentApiKey:
        raise NotImplementedError


class InMemoryAgentApiKeyStore(AgentApiKeyStore):
    keys: dict[str, AgentApiKey] = Field(default_factory=dict)

    def create(self, api_key: AgentApiKey) -> AgentApiKey:
        self.keys[api_key.id] = api_key.model_copy(deep=True)
        return api_key

    def get(self, tenant_id: str, key_id: str) -> AgentApiKey:
        api_key = self.keys.get(key_id)
        if api_key is None or api_key.tenant_id != tenant_id:
            raise NotFoundError("Agent API key not found")
        return api_key.model_copy(deep=True)

    def list(
        self,
        tenant_id: str,
        agent_id: str | None,
        created_by_user_id: str,
    ) -> list[AgentApiKey]:
        return sorted(
            [
                item.model_copy(deep=True)
                for item in self.keys.values()
                if item.tenant_id == tenant_id
                and (agent_id is None or item.agent_id == agent_id)
                and item.created_by_user_id == created_by_user_id
            ],
            key=lambda item: (item.created_at, item.id),
            reverse=True,
        )

    def touch(self, tenant_id: str, key_id: str, used_at: datetime) -> AgentApiKey:
        api_key = self.get(tenant_id, key_id).model_copy(
            update={"last_used_at": used_at}
        )
        self.keys[key_id] = api_key
        return api_key.model_copy(deep=True)

    def revoke(self, tenant_id: str, key_id: str, revoked_at: datetime) -> AgentApiKey:
        api_key = self.get(tenant_id, key_id).model_copy(
            update={"revoked_at": revoked_at}
        )
        self.keys[key_id] = api_key
        return api_key.model_copy(deep=True)


class SqlAgentApiKeyStore(AgentApiKeyStore):
    config: DatabaseConfig

    def create(self, api_key: AgentApiKey) -> AgentApiKey:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_api_keys (
                    id, tenant_id, workspace_id, agent_id, name, token_prefix,
                    token_hash, created_by_user_id, created_at, last_used_at,
                    revoked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    api_key.id,
                    api_key.tenant_id,
                    api_key.workspace_id,
                    api_key.agent_id,
                    api_key.name,
                    api_key.token_prefix,
                    api_key.token_hash,
                    api_key.created_by_user_id,
                    self._dt(api_key.created_at),
                    None,
                    None,
                ),
            )
        return api_key

    def get(self, tenant_id: str, key_id: str) -> AgentApiKey:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_api_keys WHERE tenant_id = ? AND id = ?",
                (tenant_id, key_id),
            ).fetchone()
        if row is None:
            raise NotFoundError("Agent API key not found")
        return self._from_row(row)

    def list(
        self,
        tenant_id: str,
        agent_id: str | None,
        created_by_user_id: str,
    ) -> list[AgentApiKey]:
        sql = """
            SELECT * FROM agent_api_keys
            WHERE tenant_id = ? AND created_by_user_id = ?
        """
        params = [tenant_id, created_by_user_id]
        if agent_id is not None:
            sql += " AND agent_id = ?"
            params.append(agent_id)
        sql += " ORDER BY created_at DESC, id DESC"
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self._from_row(row) for row in rows]

    def touch(self, tenant_id: str, key_id: str, used_at: datetime) -> AgentApiKey:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE agent_api_keys SET last_used_at = ?
                WHERE tenant_id = ? AND id = ?
                """,
                (self._dt(used_at), tenant_id, key_id),
            )
        return self.get(tenant_id, key_id)

    def revoke(self, tenant_id: str, key_id: str, revoked_at: datetime) -> AgentApiKey:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE agent_api_keys SET revoked_at = ?
                WHERE tenant_id = ? AND id = ? AND revoked_at IS NULL
                """,
                (self._dt(revoked_at), tenant_id, key_id),
            )
        return self.get(tenant_id, key_id)

    def _connect(self):
        return connect_database(self.config)

    def _from_row(self, row) -> AgentApiKey:
        return AgentApiKey(
            id=row["id"],
            tenant_id=row["tenant_id"],
            workspace_id=row["workspace_id"],
            agent_id=row["agent_id"],
            name=row["name"],
            token_prefix=row["token_prefix"],
            token_hash=row["token_hash"],
            created_by_user_id=row["created_by_user_id"],
            created_at=self._parse(row["created_at"]),
            last_used_at=(
                self._parse(row["last_used_at"]) if row["last_used_at"] else None
            ),
            revoked_at=self._parse(row["revoked_at"]) if row["revoked_at"] else None,
        )

    @staticmethod
    def _dt(value: datetime) -> str:
        return value.isoformat()

    @staticmethod
    def _parse(value: datetime | str) -> datetime:
        return datetime.fromisoformat(value) if isinstance(value, str) else value


class AgentApiKeyService:
    def __init__(
        self,
        *,
        store: AgentApiKeyStore,
        agent_registry: Any,
        identity_service: Any,
        hash_secret: str,
    ) -> None:
        self.store = store
        self.agent_registry = agent_registry
        self.identity_service = identity_service
        self.hash_secret = hash_secret.encode("utf-8")

    def create(
        self,
        tenant_id: str,
        user_id: str,
        payload: AgentApiKeyCreate,
    ) -> tuple[AgentApiKey, str]:
        definition = self.agent_registry.get(tenant_id, payload.agent_id)
        if definition.created_by_user_id != user_id:
            raise TenantAccessError("Only the Agent owner can create API keys")
        if definition.published_version is None:
            raise ValueError("Publish this Agent before creating an API key")
        key_id = new_id("agent_key")
        locator = self._encode_locator(tenant_id, key_id)
        token = f"taak_{locator}.{secrets.token_urlsafe(32)}"
        api_key = AgentApiKey(
            id=key_id,
            tenant_id=tenant_id,
            workspace_id=definition.workspace_id,
            agent_id=definition.id,
            name=payload.name,
            token_prefix=f"{token.split('.', 1)[0][:40]}…",
            token_hash=self._hash(token),
            created_by_user_id=user_id,
            created_at=utc_now(),
        )
        return self.store.create(api_key), token

    def authenticate(
        self,
        authorization: str | None,
        agent_id: str,
    ) -> AgentApiKey:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise AuthRequiredError("Agent API key required")
        tenant_id, key_id = self._decode_locator(token)
        try:
            api_key = self.store.get(tenant_id, key_id)
            account = self.identity_service.get_user(
                api_key.tenant_id, api_key.created_by_user_id
            )
        except (NotFoundError, ValueError) as error:
            raise AuthRequiredError("Invalid Agent API key") from error
        if (
            api_key.agent_id != agent_id
            or api_key.revoked_at is not None
            or account.status != "active"
            or not hmac.compare_digest(api_key.token_hash, self._hash(token))
        ):
            raise AuthRequiredError("Invalid Agent API key")
        return api_key

    def revoke(
        self, tenant_id: str, user_id: str, key_id: str
    ) -> AgentApiKey:
        api_key = self.store.get(tenant_id, key_id)
        if api_key.created_by_user_id != user_id:
            raise NotFoundError("Agent API key not found")
        return self.store.revoke(tenant_id, key_id, utc_now())

    def record_use(self, api_key: AgentApiKey) -> AgentApiKey:
        return self.store.touch(api_key.tenant_id, api_key.id, utc_now())

    def _hash(self, token: str) -> str:
        return "hmac-sha256:" + hmac.new(
            self.hash_secret, token.encode("utf-8"), hashlib.sha256
        ).hexdigest()

    @staticmethod
    def _encode_locator(tenant_id: str, key_id: str) -> str:
        value = f"{tenant_id}\0{key_id}".encode("utf-8")
        return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_locator(token: str) -> tuple[str, str]:
        try:
            encoded = token.split(".", 1)[0].removeprefix("taak_")
            value = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            tenant_id, key_id = value.decode("utf-8").split("\0", 1)
            if not tenant_id or not key_id or not token.startswith("taak_"):
                raise ValueError
            return tenant_id, key_id
        except (binascii.Error, ValueError, UnicodeDecodeError) as error:
            raise AuthRequiredError("Invalid Agent API key") from error
