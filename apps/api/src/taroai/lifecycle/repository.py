import json
import sqlite3
from datetime import datetime

from pydantic import BaseModel

from taroai.db import DatabaseConfig
from taroai.domain import utc_now
from taroai.lifecycle.models import (
    DataCategory,
    DeletionBehavior,
    LegalHold,
    LegalHoldCreate,
    LegalHoldScopeType,
    LifecyclePolicy,
    LifecyclePolicyCreate,
)
from taroai.lifecycle.offboarding import (
    TenantOffboardingApprovalStatus,
    TenantOffboardingPlan,
    TenantOffboardingState,
)
from taroai.store import NotFoundError


class SqlLifecyclePolicyStore(BaseModel):
    config: DatabaseConfig

    def upsert_policy(self, request: LifecyclePolicyCreate) -> LifecyclePolicy:
        existing = self._get_policy_optional(
            request.tenant_id,
            request.category,
            request.workspace_id,
        )
        now = utc_now()
        if existing is None:
            policy = LifecyclePolicy(**request.model_dump(), created_at=now, updated_at=now)
        else:
            policy = existing.model_copy(
                update={
                    **request.model_dump(),
                    "updated_at": now,
                }
            )
        with self._connect() as connection:
            self._ensure_tenant(connection, policy.tenant_id)
            connection.execute(
                """
                INSERT INTO lifecycle_policies (
                    id, tenant_id, workspace_id, category, retention_days, deletion_behavior,
                    exportable, residency_region, backup_class, legal_hold_supported,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, workspace_id, category) DO UPDATE SET
                    retention_days = excluded.retention_days,
                    deletion_behavior = excluded.deletion_behavior,
                    exportable = excluded.exportable,
                    residency_region = excluded.residency_region,
                    backup_class = excluded.backup_class,
                    legal_hold_supported = excluded.legal_hold_supported,
                    updated_at = excluded.updated_at
                """,
                (
                    policy.id,
                    policy.tenant_id,
                    self._db_workspace_id(policy.workspace_id),
                    policy.category.value,
                    policy.retention_days,
                    policy.deletion_behavior.value,
                    self._bool(policy.exportable),
                    policy.residency_region,
                    policy.backup_class,
                    self._bool(policy.legal_hold_supported),
                    self._dt(policy.created_at),
                    self._dt(policy.updated_at),
                ),
            )
        return policy

    def get_policy(
        self,
        tenant_id: str,
        category: DataCategory,
        workspace_id: str | None = None,
    ) -> LifecyclePolicy:
        policy = self._get_policy_optional(tenant_id, category, workspace_id)
        if policy is None:
            raise NotFoundError(f"Lifecycle policy not found: {tenant_id}/{category.value}")
        return policy

    def resolve_policy(
        self,
        tenant_id: str,
        category: DataCategory,
        workspace_id: str | None = None,
    ) -> LifecyclePolicy:
        if workspace_id is not None:
            workspace_policy = self._get_policy_optional(tenant_id, category, workspace_id)
            if workspace_policy is not None:
                return workspace_policy
        return self.get_policy(tenant_id, category)

    def create_legal_hold(self, request: LegalHoldCreate) -> LegalHold:
        hold = LegalHold(**request.model_dump())
        with self._connect() as connection:
            self._ensure_tenant(connection, hold.tenant_id)
            connection.execute(
                """
                INSERT INTO legal_holds (
                    id, tenant_id, category, scope_type, scope_id, reason,
                    created_by_user_id, expires_at, released_at, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    hold.id,
                    hold.tenant_id,
                    hold.category.value,
                    hold.scope_type.value,
                    hold.scope_id,
                    hold.reason,
                    hold.created_by_user_id,
                    self._dt_or_none(hold.expires_at),
                    self._dt_or_none(hold.released_at),
                    self._dt(hold.created_at),
                ),
            )
        return hold

    def release_legal_hold(
        self,
        tenant_id: str,
        legal_hold_id: str,
        released_at: datetime,
    ) -> LegalHold:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM legal_holds
                WHERE tenant_id = ? AND id = ?
                """,
                (tenant_id, legal_hold_id),
            ).fetchone()
            if row is None:
                raise NotFoundError(f"Legal hold not found: {legal_hold_id}")
            connection.execute(
                """
                UPDATE legal_holds
                SET released_at = ?
                WHERE tenant_id = ? AND id = ?
                """,
                (self._dt(released_at), tenant_id, legal_hold_id),
            )
        return self._hold_from_row(row).model_copy(update={"released_at": released_at})

    def list_active_legal_holds(
        self,
        tenant_id: str,
        category: DataCategory,
        scope_type: LegalHoldScopeType,
        scope_id: str,
        now: datetime,
    ) -> list[LegalHold]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM legal_holds
                WHERE tenant_id = ?
                  AND category = ?
                  AND scope_type = ?
                  AND scope_id = ?
                  AND released_at IS NULL
                  AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY created_at, id
                """,
                (
                    tenant_id,
                    category.value,
                    scope_type.value,
                    scope_id,
                    self._dt(now),
                ),
            ).fetchall()
        return [self._hold_from_row(row) for row in rows]

    def is_under_legal_hold(
        self,
        tenant_id: str,
        category: DataCategory,
        scope_type: LegalHoldScopeType,
        scope_id: str,
        now: datetime,
    ) -> bool:
        return bool(
            self.list_active_legal_holds(
                tenant_id=tenant_id,
                category=category,
                scope_type=scope_type,
                scope_id=scope_id,
                now=now,
            )
        )

    def _get_policy_optional(
        self,
        tenant_id: str,
        category: DataCategory,
        workspace_id: str | None = None,
    ) -> LifecyclePolicy | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM lifecycle_policies
                WHERE tenant_id = ? AND workspace_id = ? AND category = ?
                """,
                (tenant_id, self._db_workspace_id(workspace_id), category.value),
            ).fetchone()
        if row is None:
            return None
        return self._policy_from_row(row)

    def _connect(self):
        path = self.config.sqlite_path
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_tenant(self, connection, tenant_id: str) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO tenants (id, name, created_at) VALUES (?, ?, ?)",
            (tenant_id, tenant_id, self._dt(utc_now())),
        )

    def _policy_from_row(self, row) -> LifecyclePolicy:
        return LifecyclePolicy(
            id=row["id"],
            tenant_id=row["tenant_id"],
            workspace_id=self._model_workspace_id(row["workspace_id"]),
            category=DataCategory(row["category"]),
            retention_days=row["retention_days"],
            deletion_behavior=DeletionBehavior(row["deletion_behavior"]),
            exportable=self._parse_bool(row["exportable"]),
            residency_region=row["residency_region"],
            backup_class=row["backup_class"],
            legal_hold_supported=self._parse_bool(row["legal_hold_supported"]),
            created_at=self._parse_dt(row["created_at"]),
            updated_at=self._parse_dt(row["updated_at"]),
        )

    def _hold_from_row(self, row) -> LegalHold:
        return LegalHold(
            id=row["id"],
            tenant_id=row["tenant_id"],
            category=DataCategory(row["category"]),
            scope_type=LegalHoldScopeType(row["scope_type"]),
            scope_id=row["scope_id"],
            reason=row["reason"],
            created_by_user_id=row["created_by_user_id"],
            expires_at=self._parse_dt_or_none(row["expires_at"]),
            released_at=self._parse_dt_or_none(row["released_at"]),
            created_at=self._parse_dt(row["created_at"]),
        )

    def _bool(self, value: bool) -> int:
        return 1 if value else 0

    def _parse_bool(self, value) -> bool:
        return bool(value)

    def _db_workspace_id(self, workspace_id: str | None) -> str:
        return workspace_id or ""

    def _model_workspace_id(self, workspace_id: str) -> str | None:
        if workspace_id == "":
            return None
        return workspace_id

    def _dt(self, value: datetime) -> str:
        return value.isoformat()

    def _dt_or_none(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        return self._dt(value)

    def _parse_dt(self, value: str) -> datetime:
        return datetime.fromisoformat(value)

    def _parse_dt_or_none(self, value: str | None) -> datetime | None:
        if value is None:
            return None
        return self._parse_dt(value)


class SqlTenantOffboardingStore(BaseModel):
    config: DatabaseConfig

    def save_plan(self, plan: TenantOffboardingPlan) -> TenantOffboardingPlan:
        with self._connect() as connection:
            self._ensure_tenant(connection, plan.tenant_id)
            self._upsert_plan(connection, plan)
        return plan.model_copy(deep=True)

    def get_plan(self, tenant_id: str, plan_id: str) -> TenantOffboardingPlan:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM tenant_offboarding_plans
                WHERE tenant_id = ? AND id = ?
                """,
                (tenant_id, plan_id),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Tenant offboarding plan not found: {plan_id}")
        return self._plan_from_row(row)

    def update_plan(self, plan: TenantOffboardingPlan) -> TenantOffboardingPlan:
        self.get_plan(plan.tenant_id, plan.id)
        with self._connect() as connection:
            self._upsert_plan(connection, plan)
        return plan.model_copy(deep=True)

    def _upsert_plan(self, connection, plan: TenantOffboardingPlan) -> None:
        connection.execute(
            """
            INSERT INTO tenant_offboarding_plans (
                id, tenant_id, requested_by_user_id, state, approval_required,
                approval_status, next_state_after_approval, export_before_delete,
                categories, reason_length, blocked_reason, blocking_legal_hold_ids,
                deletion_scope, approved_by_user_id, approved_at, export_bundle_id,
                export_storage_object_id, export_completed_by_user_id,
                export_completed_at, deleted_by_user_id, deleted_at, created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                state = excluded.state,
                approval_required = excluded.approval_required,
                approval_status = excluded.approval_status,
                next_state_after_approval = excluded.next_state_after_approval,
                export_before_delete = excluded.export_before_delete,
                categories = excluded.categories,
                reason_length = excluded.reason_length,
                blocked_reason = excluded.blocked_reason,
                blocking_legal_hold_ids = excluded.blocking_legal_hold_ids,
                deletion_scope = excluded.deletion_scope,
                approved_by_user_id = excluded.approved_by_user_id,
                approved_at = excluded.approved_at,
                export_bundle_id = excluded.export_bundle_id,
                export_storage_object_id = excluded.export_storage_object_id,
                export_completed_by_user_id = excluded.export_completed_by_user_id,
                export_completed_at = excluded.export_completed_at,
                deleted_by_user_id = excluded.deleted_by_user_id,
                deleted_at = excluded.deleted_at,
                updated_at = excluded.updated_at
            """,
            (
                plan.id,
                plan.tenant_id,
                plan.requested_by_user_id,
                plan.state.value,
                self._bool(plan.approval_required),
                plan.approval_status.value,
                plan.next_state_after_approval.value
                if plan.next_state_after_approval is not None
                else None,
                self._bool(plan.export_before_delete),
                json.dumps([category.value for category in plan.categories]),
                plan.reason_length,
                plan.blocked_reason,
                json.dumps(plan.blocking_legal_hold_ids),
                json.dumps(plan.deletion_scope),
                plan.approved_by_user_id,
                self._dt_or_none(plan.approved_at),
                plan.export_bundle_id,
                plan.export_storage_object_id,
                plan.export_completed_by_user_id,
                self._dt_or_none(plan.export_completed_at),
                plan.deleted_by_user_id,
                self._dt_or_none(plan.deleted_at),
                self._dt(plan.created_at),
                self._dt(plan.updated_at),
            ),
        )

    def _plan_from_row(self, row) -> TenantOffboardingPlan:
        next_state = row["next_state_after_approval"]
        return TenantOffboardingPlan(
            id=row["id"],
            tenant_id=row["tenant_id"],
            requested_by_user_id=row["requested_by_user_id"],
            state=TenantOffboardingState(row["state"]),
            approval_required=self._parse_bool(row["approval_required"]),
            approval_status=TenantOffboardingApprovalStatus(row["approval_status"]),
            next_state_after_approval=(
                TenantOffboardingState(next_state) if next_state is not None else None
            ),
            export_before_delete=self._parse_bool(row["export_before_delete"]),
            categories=[
                DataCategory(category)
                for category in json.loads(row["categories"])
            ],
            reason_length=row["reason_length"],
            blocked_reason=row["blocked_reason"],
            blocking_legal_hold_ids=json.loads(row["blocking_legal_hold_ids"]),
            deletion_scope=json.loads(row["deletion_scope"]),
            approved_by_user_id=row["approved_by_user_id"],
            approved_at=self._parse_dt_or_none(row["approved_at"]),
            export_bundle_id=row["export_bundle_id"],
            export_storage_object_id=row["export_storage_object_id"],
            export_completed_by_user_id=row["export_completed_by_user_id"],
            export_completed_at=self._parse_dt_or_none(row["export_completed_at"]),
            deleted_by_user_id=row["deleted_by_user_id"],
            deleted_at=self._parse_dt_or_none(row["deleted_at"]),
            created_at=self._parse_dt(row["created_at"]),
            updated_at=self._parse_dt(row["updated_at"]),
        )

    def _connect(self):
        path = self.config.sqlite_path
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_tenant(self, connection, tenant_id: str) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO tenants (id, name, created_at) VALUES (?, ?, ?)",
            (tenant_id, tenant_id, self._dt(utc_now())),
        )

    def _bool(self, value: bool) -> int:
        return 1 if value else 0

    def _parse_bool(self, value) -> bool:
        return bool(value)

    def _dt(self, value: datetime) -> str:
        return value.isoformat()

    def _dt_or_none(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        return self._dt(value)

    def _parse_dt(self, value: str) -> datetime:
        return datetime.fromisoformat(value)

    def _parse_dt_or_none(self, value: str | None) -> datetime | None:
        if value is None:
            return None
        return self._parse_dt(value)
