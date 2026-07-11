from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from taroai.db import DatabaseConfig
from taroai.db.connection import connect_database
from taroai.domain import new_id, utc_now
from taroai.store import NotFoundError


class ThreadShareCreate(BaseModel):
    expires_in_seconds: int = Field(default=604800, ge=300, le=31_536_000)
    include_attachments: bool = False
    include_artifacts: bool = True
    redact_resource_refs: bool = True
    model_config = ConfigDict(extra="forbid")


class ThreadShareLink(BaseModel):
    id: str
    public_id: str
    tenant_id: str
    workspace_id: str
    thread_id: str
    token_hash: str = Field(exclude=True, repr=False)
    status: str = "active"
    redaction_policy: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime
    created_by_user_id: str
    created_at: datetime
    revoked_by_user_id: str | None = None
    revoked_at: datetime | None = None

    def active(self, now: datetime | None = None) -> bool:
        return self.status == "active" and self.expires_at > (now or utc_now())


class ThreadShareStore(BaseModel):
    def create(self, link: ThreadShareLink) -> ThreadShareLink:
        raise NotImplementedError


class InMemoryThreadShareStore(ThreadShareStore):
    links: dict[str, ThreadShareLink] = Field(default_factory=dict)

    def create(self, link: ThreadShareLink):
        self.links[link.id] = link.model_copy(deep=True)
        return link

    def get(self, tenant_id: str, link_id: str):
        link = self.links.get(link_id)
        if link is None or link.tenant_id != tenant_id:
            raise NotFoundError(f"Thread share link not found: {link_id}")
        return link.model_copy(deep=True)

    def get_public(self, public_id: str):
        for link in self.links.values():
            if link.public_id == public_id:
                return link.model_copy(deep=True)
        raise NotFoundError("Thread share link not found")

    def list(self, tenant_id: str, thread_id: str):
        return [
            item.model_copy(deep=True) for item in self.links.values()
            if item.tenant_id == tenant_id and item.thread_id == thread_id
        ]

    def revoke(self, tenant_id: str, link_id: str, user_id: str):
        link = self.get(tenant_id, link_id)
        revoked = link.model_copy(
            update={"status": "revoked", "revoked_by_user_id": user_id, "revoked_at": utc_now()}
        )
        self.links[link_id] = revoked
        return revoked


class SqlThreadShareStore(ThreadShareStore):
    config: DatabaseConfig

    def create(self, link: ThreadShareLink):
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO thread_share_links (
                    id, public_id, tenant_id, workspace_id, thread_id, token_hash,
                    status, redaction_policy, expires_at, created_by_user_id,
                    created_at, revoked_by_user_id, revoked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    link.id, link.public_id, link.tenant_id, link.workspace_id,
                    link.thread_id, link.token_hash, link.status,
                    self._json(link.redaction_policy), self._dt(link.expires_at),
                    link.created_by_user_id, self._dt(link.created_at),
                    link.revoked_by_user_id,
                    self._dt(link.revoked_at) if link.revoked_at else None,
                ),
            )
        return link

    def get(self, tenant_id: str, link_id: str):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM thread_share_links WHERE tenant_id = ? AND id = ?",
                (tenant_id, link_id),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Thread share link not found: {link_id}")
        return self._from_row(row)

    def get_public(self, public_id: str):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM thread_share_links WHERE public_id = ?",
                (public_id,),
            ).fetchone()
        if row is None:
            raise NotFoundError("Thread share link not found")
        return self._from_row(row)

    def list(self, tenant_id: str, thread_id: str):
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM thread_share_links
                WHERE tenant_id = ? AND thread_id = ? ORDER BY created_at DESC
                """,
                (tenant_id, thread_id),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def revoke(self, tenant_id: str, link_id: str, user_id: str):
        link = self.get(tenant_id, link_id)
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE thread_share_links
                SET status = 'revoked', revoked_by_user_id = ?, revoked_at = ?
                WHERE tenant_id = ? AND id = ?
                """,
                (user_id, self._dt(now), tenant_id, link_id),
            )
        return link.model_copy(
            update={"status": "revoked", "revoked_by_user_id": user_id, "revoked_at": now}
        )

    def _from_row(self, row):
        return ThreadShareLink(
            id=row["id"], public_id=row["public_id"], tenant_id=row["tenant_id"],
            workspace_id=row["workspace_id"], thread_id=row["thread_id"],
            token_hash=row["token_hash"], status=row["status"],
            redaction_policy=self._loads(row["redaction_policy"]),
            expires_at=self._parse(row["expires_at"]),
            created_by_user_id=row["created_by_user_id"],
            created_at=self._parse(row["created_at"]),
            revoked_by_user_id=row["revoked_by_user_id"],
            revoked_at=self._parse(row["revoked_at"]) if row["revoked_at"] else None,
        )

    def _connect(self): return connect_database(self.config)
    def _json(self, value):
        import json
        return json.dumps(value, separators=(",", ":"))
    def _loads(self, value):
        import json
        return value if not isinstance(value, str) else json.loads(value)
    def _dt(self, value): return value.isoformat()
    def _parse(self, value):
        return datetime.fromisoformat(value) if isinstance(value, str) else value


class ThreadShareService:
    def __init__(self, *, store: Any, link_store: ThreadShareStore, hash_secret: str) -> None:
        self.store = store
        self.link_store = link_store
        self.hash_secret = hash_secret.encode("utf-8")

    def create(self, tenant_id: str, user_id: str, thread_id: str, payload: ThreadShareCreate):
        thread = self.store.get_chat_thread(tenant_id, thread_id)
        token = secrets.token_urlsafe(32)
        link = ThreadShareLink(
            id=new_id("thread_share"), public_id=secrets.token_urlsafe(12),
            tenant_id=tenant_id, workspace_id=thread.workspace_id, thread_id=thread.id,
            token_hash=self._hash(token), redaction_policy={
                "include_attachments": payload.include_attachments,
                "include_artifacts": payload.include_artifacts,
                "redact_resource_refs": payload.redact_resource_refs,
            },
            expires_at=utc_now() + timedelta(seconds=payload.expires_in_seconds),
            created_by_user_id=user_id, created_at=utc_now(),
        )
        self.link_store.create(link)
        return link, token

    def read_public(self, public_id: str, token: str):
        link = self.link_store.get_public(public_id)
        if not link.active() or not hmac.compare_digest(link.token_hash, self._hash(token)):
            raise NotFoundError("Thread share link not found")
        thread = self.store.get_chat_thread(link.tenant_id, link.thread_id)
        messages = self.store.list_chat_messages(link.tenant_id, link.thread_id)
        policy = link.redaction_policy
        public_messages = []
        for message in messages:
            if message.role.value not in {"user", "assistant"}:
                continue
            item = {
                "id": message.id, "sequence": message.sequence,
                "role": message.role.value, "content": message.content,
                "created_at": message.created_at.isoformat(),
            }
            if policy.get("include_attachments"):
                item["attachments"] = message.attachments
            if not policy.get("redact_resource_refs", True):
                item["resource_refs"] = [ref.model_dump(mode="json") for ref in message.resource_refs]
            public_messages.append(item)
        artifacts = []
        if policy.get("include_artifacts", True):
            for run in self.store.list_runs(link.tenant_id, thread.workspace_id):
                if run.thread_id != thread.id:
                    continue
                artifacts.extend(
                    {
                        "id": artifact.id, "name": artifact.name,
                        "artifact_type": artifact.artifact_type,
                        "content_type": artifact.content_type,
                    }
                    for artifact in self.store.list_artifacts(link.tenant_id, run.id)
                )
        return {
            "thread": {"id": thread.id, "title": thread.title, "created_at": thread.created_at.isoformat()},
            "messages": public_messages, "artifacts": artifacts,
            "expires_at": link.expires_at.isoformat(),
        }

    def _hash(self, token: str) -> str:
        return "hmac-sha256:" + hmac.new(
            self.hash_secret, token.encode("utf-8"), hashlib.sha256
        ).hexdigest()
