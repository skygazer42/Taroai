from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from taroai.solution_packs.models import (
    SolutionPackInstallAction,
    SolutionPackInstallIssue,
    SolutionPackInstallPreview,
    SolutionPackInstallation,
    SolutionPackInstallationStatus,
    SolutionPackRollbackRecord,
    SolutionPackStatus,
)
from taroai.store import NotFoundError


class SolutionPackService(BaseModel):
    pack_registry: Any
    skill_registry: Any
    audit_store: Any | None = Field(default=None, exclude=True, repr=False)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def preview_install(
        self,
        tenant_id: str,
        pack_id: str,
        workspace_ids: list[str],
        installed_by_user_id: str,
        selected_resource_ids: list[str] | None = None,
    ) -> SolutionPackInstallPreview:
        entry = self.pack_registry.get_for_tenant(tenant_id, pack_id)
        if entry.status != SolutionPackStatus.PUBLISHED:
            raise ValueError(f"Solution pack is not published: {pack_id}")

        selected_ids = set(selected_resource_ids or [])
        actions: list[SolutionPackInstallAction] = []
        conflicts: list[SolutionPackInstallIssue] = []
        required_approvals: list[SolutionPackInstallIssue] = []
        skipped_resources: list[SolutionPackInstallAction] = []
        for skill in entry.manifest.skills:
            if selected_ids and skill.id not in selected_ids:
                skipped_resources.append(
                    SolutionPackInstallAction(
                        resource_type="skill",
                        resource_id=skill.id,
                        action="skip",
                        risk_level=skill.risk_level,
                    )
                )
                continue
            versions = self.skill_registry.list_versions(tenant_id, skill.id)
            if not any(version.manifest.version == skill.version for version in versions):
                actions.append(
                    SolutionPackInstallAction(
                        resource_type="skill",
                        resource_id=skill.id,
                        action="register",
                        risk_level=skill.risk_level,
                    )
                )
            actions.append(
                SolutionPackInstallAction(
                    resource_type="skill",
                    resource_id=skill.id,
                    action="publish",
                    risk_level=skill.risk_level,
                )
            )
            if self._is_high_risk_skill(skill):
                required_approvals.append(
                    SolutionPackInstallIssue(
                        kind="high_risk_skill_requires_approval",
                        resource_type="skill",
                        resource_id=skill.id,
                        message="High-risk skills are installed disabled until approval.",
                    )
                )
            for workspace_id in workspace_ids:
                if self._workspace_skill_installed(tenant_id, workspace_id, skill.id):
                    conflicts.append(
                        SolutionPackInstallIssue(
                            kind="workspace_skill_already_installed",
                            resource_type="skill",
                            resource_id=skill.id,
                            workspace_id=workspace_id,
                            message="Workspace already has this skill installed.",
                        )
                    )
                    continue
                actions.append(
                    SolutionPackInstallAction(
                        resource_type="skill",
                        resource_id=skill.id,
                        action="install",
                        workspace_id=workspace_id,
                        risk_level=skill.risk_level,
                        requires_approval=self._is_high_risk_skill(skill),
                    )
                )
        return SolutionPackInstallPreview(
            tenant_id=tenant_id,
            pack_id=entry.manifest.id,
            version=entry.manifest.version,
            workspace_ids=workspace_ids,
            can_install=not conflicts,
            actions=actions,
            conflicts=conflicts,
            required_approvals=required_approvals,
            skipped_resources=skipped_resources,
        )

    def install_for_tenant(
        self,
        tenant_id: str,
        pack_id: str,
        workspace_ids: list[str],
        installed_by_user_id: str,
        selected_resource_ids: list[str] | None = None,
    ) -> SolutionPackInstallation:
        preview = self.preview_install(
            tenant_id=tenant_id,
            pack_id=pack_id,
            workspace_ids=workspace_ids,
            installed_by_user_id=installed_by_user_id,
            selected_resource_ids=selected_resource_ids,
        )
        if not preview.can_install:
            self._record_audit_event(
                tenant_id=tenant_id,
                user_id=installed_by_user_id,
                event_type="solution_pack.install_conflict",
                metadata={
                    "pack_id": preview.pack_id,
                    "version": preview.version,
                    "workspace_count": len(preview.workspace_ids),
                    "conflict_count": len(preview.conflicts),
                },
            )
            raise ValueError(f"Solution pack install has conflicts: {pack_id}")

        entry = self.pack_registry.get_for_tenant(tenant_id, pack_id)
        selected_ids = set(selected_resource_ids or [])
        installed_skill_ids: list[str] = []
        for skill in entry.manifest.skills:
            if selected_ids and skill.id not in selected_ids:
                self._record_audit_event(
                    tenant_id=tenant_id,
                    user_id=installed_by_user_id,
                    event_type="solution_pack.resource_skipped",
                    metadata={
                        "pack_id": entry.manifest.id,
                        "version": entry.manifest.version,
                        "resource_type": "skill",
                        "resource_id": skill.id,
                        "reason_code": "not_selected",
                    },
                )
                continue
            self._ensure_skill_registered(
                tenant_id=tenant_id,
                created_by_user_id=installed_by_user_id,
                skill=skill,
            )
            self.skill_registry.publish(tenant_id, skill.id)
            for workspace_id in workspace_ids:
                self.skill_registry.install_for_workspace(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    skill_id=skill.id,
                    installed_by_user_id=installed_by_user_id,
                )
                if self._is_high_risk_skill(skill):
                    self.skill_registry.disable_for_workspace(
                        tenant_id=tenant_id,
                        workspace_id=workspace_id,
                        skill_id=skill.id,
                    )
            installed_skill_ids.append(skill.id)

        return self.pack_registry.record_installation(
            tenant_id=tenant_id,
            pack_id=entry.manifest.id,
            version=entry.manifest.version,
            workspace_ids=workspace_ids,
            installed_skill_ids=installed_skill_ids,
            installed_by_user_id=installed_by_user_id,
        )

    def rollback_installation(
        self,
        tenant_id: str,
        pack_id: str,
        rolled_back_by_user_id: str,
        reason_code: str,
    ) -> SolutionPackRollbackRecord:
        installation = self.pack_registry.get_installation(tenant_id, pack_id)
        disabled_skill_ids: list[str] = []
        for skill_id in installation.installed_skill_ids:
            disabled = False
            for workspace_id in installation.workspace_ids:
                try:
                    self.skill_registry.disable_for_workspace(
                        tenant_id=tenant_id,
                        workspace_id=workspace_id,
                        skill_id=skill_id,
                    )
                    disabled = True
                except NotFoundError:
                    continue
            if disabled:
                disabled_skill_ids.append(skill_id)
        self._set_installation_status(
            tenant_id,
            pack_id,
            SolutionPackInstallationStatus.ROLLED_BACK,
        )
        rollback = SolutionPackRollbackRecord(
            tenant_id=tenant_id,
            pack_id=installation.pack_id,
            version=installation.version,
            workspace_ids=installation.workspace_ids,
            disabled_skill_ids=disabled_skill_ids,
            rolled_back_by_user_id=rolled_back_by_user_id,
            reason_code=reason_code,
        )
        self._record_audit_event(
            tenant_id=tenant_id,
            user_id=rolled_back_by_user_id,
            event_type="solution_pack.rollback",
            metadata={
                "pack_id": rollback.pack_id,
                "version": rollback.version,
                "workspace_count": len(rollback.workspace_ids),
                "disabled_skill_count": len(rollback.disabled_skill_ids),
                "rolled_back_by_user_id": rolled_back_by_user_id,
                "reason_code": reason_code,
            },
        )
        return rollback

    def _ensure_skill_registered(
        self,
        tenant_id: str,
        created_by_user_id: str,
        skill,
    ) -> None:
        versions = self.skill_registry.list_versions(tenant_id, skill.id)
        if any(entry.manifest.version == skill.version for entry in versions):
            return
        self.skill_registry.register_for_tenant(
            tenant_id=tenant_id,
            created_by_user_id=created_by_user_id,
            manifest=skill,
        )

    def _workspace_skill_installed(
        self,
        tenant_id: str,
        workspace_id: str,
        skill_id: str,
    ) -> bool:
        return any(
            installation.skill_id == skill_id
            for installation in self.skill_registry.list_for_workspace(
                tenant_id,
                workspace_id,
            )
        )

    def _is_high_risk_skill(self, skill) -> bool:
        return str(skill.risk_level).lower() in {"high", "critical"} or bool(
            skill.approval_required
        )

    def _set_installation_status(
        self,
        tenant_id: str,
        pack_id: str,
        status: SolutionPackInstallationStatus,
    ) -> None:
        if hasattr(self.pack_registry, "update_installation_status"):
            self.pack_registry.update_installation_status(tenant_id, pack_id, status)

    def _record_audit_event(
        self,
        tenant_id: str,
        user_id: str,
        event_type: str,
        metadata: dict[str, Any],
    ) -> None:
        if self.audit_store is None:
            return
        self.audit_store.record_audit_event(
            tenant_id=tenant_id,
            workspace_id=None,
            user_id=user_id,
            run_id=None,
            event_type=event_type,
            metadata=metadata,
        )
