from typing import Any

from taroai.coding_workspaces.models import (
    CodingChange,
    CodingChangesSubmit,
    CodingCheckpoint,
    CodingCheckpointCreate,
    CodingDelivery,
    CodingDeliveryCreate,
    CodingTestResult,
    CodingTestResultCreate,
    CodingWorkspace,
    CodingWorkspaceCreate,
    RepositoryBinding,
    RepositoryBindingCreate,
    RepositoryBindingPatch,
)
from taroai.coding_workspaces.repository import CodingWorkspaceRegistry
from taroai.domain import utc_now


class CodingWorkspaceService:
    def __init__(self, registry: CodingWorkspaceRegistry, store: Any):
        self.registry = registry
        self.store = store

    def create_repository(
        self, tenant_id: str, user_id: str, payload: RepositoryBindingCreate
    ) -> RepositoryBinding:
        return self.registry.save_repository(
            RepositoryBinding(
                tenant_id=tenant_id, created_by_user_id=user_id, **payload.model_dump()
            )
        )

    def update_repository(
        self, tenant_id: str, item_id: str, payload: RepositoryBindingPatch
    ) -> RepositoryBinding:
        item = self.registry.get_repository(tenant_id, item_id)
        return self.registry.save_repository(
            item.model_copy(
                update={
                    **payload.model_dump(exclude_none=True),
                    "updated_at": utc_now(),
                }
            )
        )

    def create_workspace(
        self, tenant_id: str, user_id: str, payload: CodingWorkspaceCreate
    ) -> CodingWorkspace:
        repository = self.registry.get_repository(tenant_id, payload.repository_id)
        run = self.store.get_run(tenant_id, payload.run_id)
        if (
            repository.workspace_id != payload.workspace_id
            or run.workspace_id != payload.workspace_id
            or repository.status != "active"
        ):
            raise ValueError(
                "Repository, Run, and Coding Workspace must share an active workspace"
            )
        branch = payload.branch or f"taroai/{run.id}"
        safe_id = "".join(
            character
            for character in payload.repository_id
            if character.isalnum() or character in "_-"
        )
        return self.registry.save_workspace(
            CodingWorkspace(
                tenant_id=tenant_id,
                workspace_id=payload.workspace_id,
                repository_id=repository.id,
                run_id=run.id,
                engine_session_id=payload.engine_session_id,
                branch=branch,
                worktree_path=f"/workspace/repos/{safe_id}/{run.id}",
                base_revision=payload.base_revision,
                created_by_user_id=user_id,
            )
        )

    def submit_changes(
        self, tenant_id: str, item_id: str, payload: CodingChangesSubmit
    ) -> CodingWorkspace:
        workspace = self.registry.get_workspace(tenant_id, item_id)
        values = []
        for source in payload.changes:
            path = source.path.replace("\\", "/").lstrip("/")
            if not path or ".." in path.split("/"):
                raise ValueError("Code change path is unsafe")
            values.append(
                CodingChange(
                    tenant_id=tenant_id,
                    coding_workspace_id=item_id,
                    path=path,
                    **source.model_dump(exclude={"path"}),
                )
            )
        self.registry.replace_changes(tenant_id, item_id, values)
        updated = workspace.model_copy(
            update={
                "head_revision": payload.head_revision,
                "status": "dirty",
                "updated_at": utc_now(),
            }
        )
        return self.registry.save_workspace(updated)

    def add_test(
        self, tenant_id: str, item_id: str, payload: CodingTestResultCreate
    ) -> CodingTestResult:
        workspace = self.registry.get_workspace(tenant_id, item_id)
        result = self.registry.append_test(
            CodingTestResult(
                tenant_id=tenant_id, coding_workspace_id=item_id, **payload.model_dump()
            )
        )
        if payload.status == "passed":
            self.registry.save_workspace(
                workspace.model_copy(
                    update={"status": "tested", "updated_at": utc_now()}
                )
            )
        return result

    def add_checkpoint(
        self,
        tenant_id: str,
        user_id: str,
        item_id: str,
        payload: CodingCheckpointCreate,
    ) -> CodingCheckpoint:
        self.registry.get_workspace(tenant_id, item_id)
        return self.registry.append_checkpoint(
            CodingCheckpoint(
                tenant_id=tenant_id,
                coding_workspace_id=item_id,
                created_by_user_id=user_id,
                **payload.model_dump(),
            )
        )

    def add_delivery(
        self, tenant_id: str, user_id: str, item_id: str, payload: CodingDeliveryCreate
    ) -> CodingDelivery:
        workspace = self.registry.get_workspace(tenant_id, item_id)
        delivery = self.registry.append_delivery(
            CodingDelivery(
                tenant_id=tenant_id,
                coding_workspace_id=item_id,
                created_by_user_id=user_id,
                **payload.model_dump(),
            )
        )
        if payload.status in {"committed", "pull_request_open", "merged"}:
            self.registry.save_workspace(
                workspace.model_copy(
                    update={
                        "status": "delivered",
                        "head_revision": payload.commit_sha or workspace.head_revision,
                        "updated_at": utc_now(),
                    }
                )
            )
        return delivery

    def detail(self, tenant_id: str, item_id: str) -> dict:
        workspace = self.registry.get_workspace(tenant_id, item_id)
        repository = self.registry.get_repository(tenant_id, workspace.repository_id)
        evidence = self.registry.evidence(tenant_id, item_id)
        return {
            "coding_workspace": workspace.model_dump(mode="json"),
            "repository": repository.model_dump(mode="json"),
            **{
                key: [item.model_dump(mode="json") for item in values]
                for key, values in evidence.items()
            },
        }
