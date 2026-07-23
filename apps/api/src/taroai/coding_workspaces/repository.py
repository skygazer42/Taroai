import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from taroai.coding_workspaces.models import (
    CodingChange,
    CodingCheckpoint,
    CodingDelivery,
    CodingTestResult,
    CodingWorkspace,
    RepositoryBinding,
)
from taroai.db import DatabaseConfig
from taroai.db.connection import connect_database
from taroai.store import NotFoundError


class CodingWorkspaceRegistry(BaseModel):
    repositories: dict[str, RepositoryBinding] = Field(default_factory=dict)
    workspaces: dict[str, CodingWorkspace] = Field(default_factory=dict)
    changes: dict[str, list[CodingChange]] = Field(default_factory=dict)
    tests: dict[str, list[CodingTestResult]] = Field(default_factory=dict)
    checkpoints: dict[str, list[CodingCheckpoint]] = Field(default_factory=dict)
    deliveries: dict[str, list[CodingDelivery]] = Field(default_factory=dict)

    def save_repository(self, item: RepositoryBinding) -> RepositoryBinding:
        self.repositories[item.id] = item.model_copy(deep=True)
        return item

    def get_repository(self, tenant_id: str, item_id: str) -> RepositoryBinding:
        item = self.repositories.get(item_id)
        if item is None or item.tenant_id != tenant_id:
            raise NotFoundError(f"Repository binding not found: {item_id}")
        return item.model_copy(deep=True)

    def list_repositories(
        self, tenant_id: str, workspace_id: str
    ) -> list[RepositoryBinding]:
        return sorted(
            [
                item.model_copy(deep=True)
                for item in self.repositories.values()
                if item.tenant_id == tenant_id and item.workspace_id == workspace_id
            ],
            key=lambda item: (item.name.casefold(), item.id),
        )

    def save_workspace(self, item: CodingWorkspace) -> CodingWorkspace:
        self.workspaces[item.id] = item.model_copy(deep=True)
        return item

    def get_workspace(self, tenant_id: str, item_id: str) -> CodingWorkspace:
        item = self.workspaces.get(item_id)
        if item is None or item.tenant_id != tenant_id:
            raise NotFoundError(f"Coding Workspace not found: {item_id}")
        return item.model_copy(deep=True)

    def list_workspaces(
        self, tenant_id: str, workspace_id: str
    ) -> list[CodingWorkspace]:
        return sorted(
            [
                item.model_copy(deep=True)
                for item in self.workspaces.values()
                if item.tenant_id == tenant_id and item.workspace_id == workspace_id
            ],
            key=lambda item: (item.created_at, item.id),
            reverse=True,
        )

    def replace_changes(
        self, tenant_id: str, item_id: str, values: list[CodingChange]
    ) -> list[CodingChange]:
        self.get_workspace(tenant_id, item_id)
        self.changes[item_id] = [item.model_copy(deep=True) for item in values]
        return values

    def append_test(self, item: CodingTestResult) -> CodingTestResult:
        self.tests.setdefault(item.coding_workspace_id, []).append(
            item.model_copy(deep=True)
        )
        return item

    def append_checkpoint(self, item: CodingCheckpoint) -> CodingCheckpoint:
        self.checkpoints.setdefault(item.coding_workspace_id, []).append(
            item.model_copy(deep=True)
        )
        return item

    def append_delivery(self, item: CodingDelivery) -> CodingDelivery:
        self.deliveries.setdefault(item.coding_workspace_id, []).append(
            item.model_copy(deep=True)
        )
        return item

    def evidence(self, tenant_id: str, item_id: str) -> dict:
        self.get_workspace(tenant_id, item_id)
        return {
            "changes": self.changes.get(item_id, []),
            "tests": self.tests.get(item_id, []),
            "checkpoints": self.checkpoints.get(item_id, []),
            "deliveries": self.deliveries.get(item_id, []),
        }


class SqlCodingWorkspaceRegistry(CodingWorkspaceRegistry):
    config: DatabaseConfig

    def save_repository(self, item: RepositoryBinding) -> RepositoryBinding:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO repository_bindings (
                    id, tenant_id, workspace_id, name, provider, repository_url,
                    default_branch, connector_id, status, created_by_user_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET name = excluded.name,
                    default_branch = excluded.default_branch,
                    connector_id = excluded.connector_id, status = excluded.status,
                    updated_at = excluded.updated_at""",
                (
                    item.id,
                    item.tenant_id,
                    item.workspace_id,
                    item.name,
                    item.provider,
                    item.repository_url,
                    item.default_branch,
                    item.connector_id,
                    item.status,
                    item.created_by_user_id,
                    self._dt(item.created_at),
                    self._dt(item.updated_at),
                ),
            )
        return item

    def get_repository(self, tenant_id: str, item_id: str) -> RepositoryBinding:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM repository_bindings WHERE tenant_id = ? AND id = ?",
                (tenant_id, item_id),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Repository binding not found: {item_id}")
        return self._repository(row)

    def list_repositories(
        self, tenant_id: str, workspace_id: str
    ) -> list[RepositoryBinding]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM repository_bindings WHERE tenant_id = ? AND workspace_id = ? ORDER BY name, id",
                (tenant_id, workspace_id),
            ).fetchall()
        return [self._repository(row) for row in rows]

    def save_workspace(self, item: CodingWorkspace) -> CodingWorkspace:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO coding_workspaces (
                    id, tenant_id, workspace_id, repository_id, run_id,
                    engine_session_id, branch, worktree_path, base_revision,
                    head_revision, status, created_by_user_id, metadata,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET engine_session_id = excluded.engine_session_id,
                    head_revision = excluded.head_revision, status = excluded.status,
                    metadata = excluded.metadata, updated_at = excluded.updated_at""",
                (
                    item.id,
                    item.tenant_id,
                    item.workspace_id,
                    item.repository_id,
                    item.run_id,
                    item.engine_session_id,
                    item.branch,
                    item.worktree_path,
                    item.base_revision,
                    item.head_revision,
                    item.status,
                    item.created_by_user_id,
                    self._json(item.metadata),
                    self._dt(item.created_at),
                    self._dt(item.updated_at),
                ),
            )
        return item

    def get_workspace(self, tenant_id: str, item_id: str) -> CodingWorkspace:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM coding_workspaces WHERE tenant_id = ? AND id = ?",
                (tenant_id, item_id),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Coding Workspace not found: {item_id}")
        return self._workspace(row)

    def list_workspaces(
        self, tenant_id: str, workspace_id: str
    ) -> list[CodingWorkspace]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM coding_workspaces WHERE tenant_id = ? AND workspace_id = ? ORDER BY created_at DESC, id DESC",
                (tenant_id, workspace_id),
            ).fetchall()
        return [self._workspace(row) for row in rows]

    def replace_changes(
        self, tenant_id: str, item_id: str, values: list[CodingChange]
    ) -> list[CodingChange]:
        self.get_workspace(tenant_id, item_id)
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM coding_changes WHERE tenant_id = ? AND coding_workspace_id = ?",
                (tenant_id, item_id),
            )
            for item in values:
                connection.execute(
                    'INSERT INTO coding_changes (id, tenant_id, coding_workspace_id, path, status, additions, deletions, patch, "binary", previous_path, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (
                        item.id,
                        item.tenant_id,
                        item.coding_workspace_id,
                        item.path,
                        item.status,
                        item.additions,
                        item.deletions,
                        item.patch,
                        item.binary,
                        item.previous_path,
                        self._dt(item.created_at),
                    ),
                )
        return values

    def append_test(self, item: CodingTestResult) -> CodingTestResult:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO coding_test_results (id, tenant_id, coding_workspace_id, command, status, duration_seconds, summary, output_artifact_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    item.id,
                    item.tenant_id,
                    item.coding_workspace_id,
                    item.command,
                    item.status,
                    item.duration_seconds,
                    item.summary,
                    item.output_artifact_id,
                    self._dt(item.created_at),
                ),
            )
        return item

    def append_checkpoint(self, item: CodingCheckpoint) -> CodingCheckpoint:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO coding_checkpoints (id, tenant_id, coding_workspace_id, label, revision, snapshot_id, created_by_user_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    item.id,
                    item.tenant_id,
                    item.coding_workspace_id,
                    item.label,
                    item.revision,
                    item.snapshot_id,
                    item.created_by_user_id,
                    self._dt(item.created_at),
                ),
            )
        return item

    def append_delivery(self, item: CodingDelivery) -> CodingDelivery:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO coding_deliveries (id, tenant_id, coding_workspace_id, commit_sha, commit_message, pull_request_url, pull_request_number, status, created_by_user_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    item.id,
                    item.tenant_id,
                    item.coding_workspace_id,
                    item.commit_sha,
                    item.commit_message,
                    item.pull_request_url,
                    item.pull_request_number,
                    item.status,
                    item.created_by_user_id,
                    self._dt(item.created_at),
                ),
            )
        return item

    def evidence(self, tenant_id: str, item_id: str) -> dict:
        self.get_workspace(tenant_id, item_id)
        with self._connect() as connection:
            changes = connection.execute(
                "SELECT * FROM coding_changes WHERE tenant_id = ? AND coding_workspace_id = ? ORDER BY path, id",
                (tenant_id, item_id),
            ).fetchall()
            tests = connection.execute(
                "SELECT * FROM coding_test_results WHERE tenant_id = ? AND coding_workspace_id = ? ORDER BY created_at, id",
                (tenant_id, item_id),
            ).fetchall()
            checkpoints = connection.execute(
                "SELECT * FROM coding_checkpoints WHERE tenant_id = ? AND coding_workspace_id = ? ORDER BY created_at, id",
                (tenant_id, item_id),
            ).fetchall()
            deliveries = connection.execute(
                "SELECT * FROM coding_deliveries WHERE tenant_id = ? AND coding_workspace_id = ? ORDER BY created_at, id",
                (tenant_id, item_id),
            ).fetchall()
        return {
            "changes": [
                CodingChange(
                    id=row["id"],
                    tenant_id=row["tenant_id"],
                    coding_workspace_id=row["coding_workspace_id"],
                    path=row["path"],
                    status=row["status"],
                    additions=int(row["additions"]),
                    deletions=int(row["deletions"]),
                    patch=row["patch"],
                    binary=bool(row["binary"]),
                    previous_path=row["previous_path"],
                    created_at=self._parse(row["created_at"]),
                )
                for row in changes
            ],
            "tests": [
                CodingTestResult(
                    id=row["id"],
                    tenant_id=row["tenant_id"],
                    coding_workspace_id=row["coding_workspace_id"],
                    command=row["command"],
                    status=row["status"],
                    duration_seconds=float(row["duration_seconds"]),
                    summary=row["summary"],
                    output_artifact_id=row["output_artifact_id"],
                    created_at=self._parse(row["created_at"]),
                )
                for row in tests
            ],
            "checkpoints": [
                CodingCheckpoint(
                    id=row["id"],
                    tenant_id=row["tenant_id"],
                    coding_workspace_id=row["coding_workspace_id"],
                    label=row["label"],
                    revision=row["revision"],
                    snapshot_id=row["snapshot_id"],
                    created_by_user_id=row["created_by_user_id"],
                    created_at=self._parse(row["created_at"]),
                )
                for row in checkpoints
            ],
            "deliveries": [
                CodingDelivery(
                    id=row["id"],
                    tenant_id=row["tenant_id"],
                    coding_workspace_id=row["coding_workspace_id"],
                    commit_sha=row["commit_sha"],
                    commit_message=row["commit_message"],
                    pull_request_url=row["pull_request_url"],
                    pull_request_number=row["pull_request_number"],
                    status=row["status"],
                    created_by_user_id=row["created_by_user_id"],
                    created_at=self._parse(row["created_at"]),
                )
                for row in deliveries
            ],
        }

    def _repository(self, row) -> RepositoryBinding:
        return RepositoryBinding(
            id=row["id"],
            tenant_id=row["tenant_id"],
            workspace_id=row["workspace_id"],
            name=row["name"],
            provider=row["provider"],
            repository_url=row["repository_url"],
            default_branch=row["default_branch"],
            connector_id=row["connector_id"],
            status=row["status"],
            created_by_user_id=row["created_by_user_id"],
            created_at=self._parse(row["created_at"]),
            updated_at=self._parse(row["updated_at"]),
        )

    def _workspace(self, row) -> CodingWorkspace:
        return CodingWorkspace(
            id=row["id"],
            tenant_id=row["tenant_id"],
            workspace_id=row["workspace_id"],
            repository_id=row["repository_id"],
            run_id=row["run_id"],
            engine_session_id=row["engine_session_id"],
            branch=row["branch"],
            worktree_path=row["worktree_path"],
            base_revision=row["base_revision"],
            head_revision=row["head_revision"],
            status=row["status"],
            created_by_user_id=row["created_by_user_id"],
            metadata=self._loads(row["metadata"]),
            created_at=self._parse(row["created_at"]),
            updated_at=self._parse(row["updated_at"]),
        )

    def _connect(self):
        return connect_database(self.config)

    def _json(self, value: Any) -> str:
        return json.dumps(value, separators=(",", ":"))

    def _loads(self, value: Any):
        return value if not isinstance(value, str) else json.loads(value)

    def _dt(self, value: datetime) -> str:
        return value.isoformat()

    def _parse(self, value: Any) -> datetime:
        return datetime.fromisoformat(value) if isinstance(value, str) else value
