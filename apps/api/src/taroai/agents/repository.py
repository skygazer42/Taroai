from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from taroai.agents.models import AgentDefinition, AgentVersion, AgentVersionSpec
from taroai.db import DatabaseConfig
from taroai.db.connection import connect_database
from taroai.domain import new_id, utc_now
from taroai.store import NotFoundError


class AgentRegistry(BaseModel):
    def create(self, definition: AgentDefinition, version: AgentVersion):
        raise NotImplementedError


class InMemoryAgentRegistry(AgentRegistry):
    definitions: dict[str, AgentDefinition] = Field(default_factory=dict)
    versions: dict[str, list[AgentVersion]] = Field(default_factory=dict)

    def create(self, definition: AgentDefinition, version: AgentVersion):
        if definition.id in self.definitions:
            raise ValueError(f"Agent already exists: {definition.id}")
        self.definitions[definition.id] = definition.model_copy(deep=True)
        self.versions[definition.id] = [version.model_copy(deep=True)]
        return definition, version

    def get(self, tenant_id: str, agent_id: str) -> AgentDefinition:
        definition = self.definitions.get(agent_id)
        if definition is None or definition.tenant_id != tenant_id:
            raise NotFoundError(f"Agent not found: {agent_id}")
        return definition.model_copy(deep=True)

    def list(self, tenant_id: str, workspace_id: str | None = None):
        return sorted(
            [
                item.model_copy(deep=True)
                for item in self.definitions.values()
                if item.tenant_id == tenant_id
                and (workspace_id is None or item.workspace_id == workspace_id)
            ],
            key=lambda item: (item.updated_at, item.id),
            reverse=True,
        )

    def list_versions(self, tenant_id: str, agent_id: str):
        self.get(tenant_id, agent_id)
        return [item.model_copy(deep=True) for item in self.versions.get(agent_id, [])]

    def get_version(self, tenant_id: str, agent_id: str, version: int):
        for item in self.list_versions(tenant_id, agent_id):
            if item.version == version:
                return item
        raise NotFoundError(f"Agent version not found: {agent_id}@{version}")

    def add_version(self, version: AgentVersion):
        definition = self.get(version.tenant_id, version.agent_id)
        expected = definition.latest_version + 1
        if version.version != expected:
            raise ValueError(f"Agent version must be {expected}")
        self.versions.setdefault(version.agent_id, []).append(version.model_copy(deep=True))
        updated = definition.model_copy(
            update={"latest_version": version.version, "updated_at": utc_now()}
        )
        self.definitions[definition.id] = updated
        return version

    def publish(self, tenant_id: str, agent_id: str, version: int):
        definition = self.get(tenant_id, agent_id)
        target = self.get_version(tenant_id, agent_id, version)
        now = utc_now()
        published = target.model_copy(
            update={"status": "published", "published_at": now}
        )
        self.versions[agent_id] = [
            published
            if item.version == version
            else item.model_copy(update={"status": "superseded"})
            if item.status == "published"
            else item
            for item in self.versions[agent_id]
        ]
        updated = definition.model_copy(
            update={
                "status": "published",
                "published_version": version,
                "updated_at": now,
            }
        )
        self.definitions[agent_id] = updated
        return updated, published


class SqlAgentRegistry(AgentRegistry):
    config: DatabaseConfig

    def create(self, definition: AgentDefinition, version: AgentVersion):
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_definitions (
                    id, tenant_id, workspace_id, name, description, status,
                    latest_version, published_version, created_by_user_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    definition.id, definition.tenant_id, definition.workspace_id,
                    definition.name, definition.description, definition.status,
                    definition.latest_version, definition.published_version,
                    definition.created_by_user_id, self._dt(definition.created_at),
                    self._dt(definition.updated_at),
                ),
            )
            self._insert_version(connection, version)
        return definition, version

    def get(self, tenant_id: str, agent_id: str) -> AgentDefinition:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_definitions WHERE tenant_id = ? AND id = ?",
                (tenant_id, agent_id),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Agent not found: {agent_id}")
        return self._definition(row)

    def list(self, tenant_id: str, workspace_id: str | None = None):
        sql = "SELECT * FROM agent_definitions WHERE tenant_id = ?"
        params: list[Any] = [tenant_id]
        if workspace_id is not None:
            sql += " AND workspace_id = ?"
            params.append(workspace_id)
        sql += " ORDER BY updated_at DESC, id DESC"
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self._definition(row) for row in rows]

    def list_versions(self, tenant_id: str, agent_id: str):
        self.get(tenant_id, agent_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM agent_versions
                WHERE tenant_id = ? AND agent_id = ? ORDER BY version
                """,
                (tenant_id, agent_id),
            ).fetchall()
        return [self._version(row) for row in rows]

    def get_version(self, tenant_id: str, agent_id: str, version: int):
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM agent_versions
                WHERE tenant_id = ? AND agent_id = ? AND version = ?
                """,
                (tenant_id, agent_id, version),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Agent version not found: {agent_id}@{version}")
        return self._version(row)

    def add_version(self, version: AgentVersion):
        definition = self.get(version.tenant_id, version.agent_id)
        if version.version != definition.latest_version + 1:
            raise ValueError("Agent version is not the next immutable version")
        with self._connect() as connection:
            self._insert_version(connection, version)
            connection.execute(
                """
                UPDATE agent_definitions SET latest_version = ?, updated_at = ?
                WHERE tenant_id = ? AND id = ?
                """,
                (version.version, self._dt(utc_now()), version.tenant_id, version.agent_id),
            )
        return version

    def publish(self, tenant_id: str, agent_id: str, version: int):
        target = self.get_version(tenant_id, agent_id, version)
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE agent_versions SET status = 'superseded'
                WHERE tenant_id = ? AND agent_id = ? AND status = 'published'
                """,
                (tenant_id, agent_id),
            )
            connection.execute(
                """
                UPDATE agent_versions SET status = 'published', published_at = ?
                WHERE tenant_id = ? AND agent_id = ? AND version = ?
                """,
                (self._dt(now), tenant_id, agent_id, version),
            )
            connection.execute(
                """
                UPDATE agent_definitions
                SET status = 'published', published_version = ?, updated_at = ?
                WHERE tenant_id = ? AND id = ?
                """,
                (version, self._dt(now), tenant_id, agent_id),
            )
        return self.get(tenant_id, agent_id), target.model_copy(
            update={"status": "published", "published_at": now}
        )

    def _insert_version(self, connection, version: AgentVersion) -> None:
        spec = version.spec
        connection.execute(
            """
            INSERT INTO agent_versions (
                id, tenant_id, workspace_id, agent_id, version, status,
                input_schema, output_contract, instructions, skill_bindings,
                connector_bindings, knowledge_bindings, reference_files,
                model_policy, runtime_snapshot, source_thread_id, source_run_id,
                change_note, created_by_user_id, created_at, published_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version.id, version.tenant_id, version.workspace_id, version.agent_id,
                version.version, version.status, self._json(spec.input_schema),
                self._json(spec.output_contract), spec.instructions,
                self._json(spec.skill_bindings), self._json(spec.connector_bindings),
                self._json(spec.knowledge_bindings), self._json(spec.reference_files),
                self._json(spec.model_policy), self._json(spec.runtime_snapshot),
                spec.source_thread_id, spec.source_run_id, spec.change_note,
                version.created_by_user_id, self._dt(version.created_at),
                self._dt(version.published_at) if version.published_at else None,
            ),
        )

    def _definition(self, row) -> AgentDefinition:
        return AgentDefinition(
            id=row["id"], tenant_id=row["tenant_id"], workspace_id=row["workspace_id"],
            name=row["name"], description=row["description"], status=row["status"],
            latest_version=int(row["latest_version"]),
            published_version=(int(row["published_version"]) if row["published_version"] is not None else None),
            created_by_user_id=row["created_by_user_id"],
            created_at=self._parse_dt(row["created_at"]), updated_at=self._parse_dt(row["updated_at"]),
        )

    def _version(self, row) -> AgentVersion:
        return AgentVersion(
            id=row["id"], tenant_id=row["tenant_id"], workspace_id=row["workspace_id"],
            agent_id=row["agent_id"], version=int(row["version"]), status=row["status"],
            spec=AgentVersionSpec(
                input_schema=self._loads(row["input_schema"]),
                output_contract=self._loads(row["output_contract"]),
                instructions=row["instructions"],
                skill_bindings=self._loads(row["skill_bindings"]),
                connector_bindings=self._loads(row["connector_bindings"]),
                knowledge_bindings=self._loads(row["knowledge_bindings"]),
                reference_files=self._loads(row["reference_files"]),
                model_policy=self._loads(row["model_policy"]),
                runtime_snapshot=self._loads(row["runtime_snapshot"]),
                source_thread_id=row["source_thread_id"], source_run_id=row["source_run_id"],
                change_note=row["change_note"],
            ),
            created_by_user_id=row["created_by_user_id"],
            created_at=self._parse_dt(row["created_at"]),
            published_at=self._parse_dt(row["published_at"]) if row["published_at"] else None,
        )

    def _connect(self):
        return connect_database(self.config)

    def _json(self, value) -> str:
        return json.dumps(value, separators=(",", ":"))

    def _loads(self, value):
        return value if not isinstance(value, str) else json.loads(value)

    def _dt(self, value) -> str:
        return value.isoformat()

    def _parse_dt(self, value):
        from datetime import datetime
        return datetime.fromisoformat(value) if isinstance(value, str) else value

