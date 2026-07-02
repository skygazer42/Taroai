import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from taroai.db.migrations import MigrationRunner
from taroai.db.models import DatabaseConfig
from taroai.domain import (
    ApprovalRequest,
    ApprovalStatus,
    Artifact,
    AuditEvent,
    BillingMeterEvent,
    Run,
    RunCreate,
    RunEvent,
    RunStatus,
    new_id,
    utc_now,
)
from taroai.store import (
    NotFoundError,
    RunStateSnapshot,
    RunTransitionError,
    TERMINAL_RUN_STATUSES,
    TenantAccessError,
)


class SqlControlPlaneRepository(BaseModel):
    config: DatabaseConfig

    def initialize_schema(self, migrations_path: Path) -> None:
        MigrationRunner(config=self.config, migrations_path=migrations_path).apply()

    def create_run(self, tenant_id: str, user_id: str, payload: RunCreate) -> Run:
        now = utc_now()
        run = Run(
            id=new_id("run"),
            tenant_id=tenant_id,
            workspace_id=payload.workspace_id,
            user_id=user_id,
            agent_id=payload.agent_id,
            message=payload.message,
            attachments=payload.attachments,
            mode=payload.mode,
            status=RunStatus.CREATED,
            created_at=now,
            updated_at=now,
        )
        with self._connect() as connection:
            self._ensure_context(connection, tenant_id, payload.workspace_id, user_id)
            connection.execute(
                """
                INSERT INTO runs (
                    id, tenant_id, workspace_id, user_id, agent_id, message,
                    attachments, mode, status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.id,
                    run.tenant_id,
                    run.workspace_id,
                    run.user_id,
                    run.agent_id,
                    run.message,
                    self._json(run.attachments),
                    run.mode.value,
                    run.status.value,
                    self._dt(run.created_at),
                    self._dt(run.updated_at),
                ),
            )
            self._append_run_event(connection, run, "run.created", {
                "status": run.status.value,
                "mode": run.mode.value,
                "agent_id": run.agent_id,
            })
            self._record_run_meter(connection, run)
            self._record_audit_event(connection, run, "run.created", {
                "mode": run.mode.value,
                "agent_id": run.agent_id,
            })
        return run

    def get_run(self, tenant_id: str, run_id: str) -> Run:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"Run not found: {run_id}")
        if row["tenant_id"] != tenant_id:
            raise TenantAccessError(f"Run {run_id} is not in tenant {tenant_id}")
        return self._run_from_row(row)

    def list_run_events(self, tenant_id: str, run_id: str) -> list[RunEvent]:
        self.get_run(tenant_id, run_id)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM run_events WHERE tenant_id = ? AND run_id = ? ORDER BY created_at, id",
                (tenant_id, run_id),
            ).fetchall()
        return [
            RunEvent(
                id=row["id"],
                tenant_id=row["tenant_id"],
                workspace_id=row["workspace_id"],
                run_id=row["run_id"],
                type=row["type"],
                payload=self._loads(row["payload"]),
                created_at=self._parse_dt(row["created_at"]),
            )
            for row in rows
        ]

    def update_run_status(
        self,
        tenant_id: str,
        run_id: str,
        status: RunStatus,
        emit_status_event: bool = True,
    ) -> Run:
        run = self.get_run(tenant_id, run_id)
        updated_run = run.model_copy(update={"status": status, "updated_at": utc_now()})
        with self._connect() as connection:
            connection.execute(
                "UPDATE runs SET status = ?, updated_at = ? WHERE id = ?",
                (updated_run.status.value, self._dt(updated_run.updated_at), run_id),
            )
            if emit_status_event:
                self._append_run_event(
                    connection,
                    updated_run,
                    "run.status_changed",
                    {"status": updated_run.status.value},
                )
        return updated_run

    def cancel_run(
        self,
        tenant_id: str,
        run_id: str,
        cancelled_by_user_id: str,
        reason_code: str,
    ) -> Run:
        run = self.get_run(tenant_id, run_id)
        if run.status in TERMINAL_RUN_STATUSES:
            raise RunTransitionError(f"Run {run_id} cannot be cancelled from {run.status.value}")
        cancelled_run = run.model_copy(
            update={"status": RunStatus.CANCELLED, "updated_at": utc_now()}
        )
        metadata = {
            "cancelled_by_user_id": cancelled_by_user_id,
            "reason_code": reason_code,
            "status": RunStatus.CANCELLED.value,
        }
        with self._connect() as connection:
            connection.execute(
                "UPDATE runs SET status = ?, updated_at = ? WHERE id = ?",
                (
                    cancelled_run.status.value,
                    self._dt(cancelled_run.updated_at),
                    run_id,
                ),
            )
            self._insert_audit_event(
                connection,
                cancelled_run,
                AuditEvent(
                    id=new_id("audit"),
                    tenant_id=tenant_id,
                    workspace_id=run.workspace_id,
                    user_id=cancelled_by_user_id,
                    run_id=run_id,
                    event_type="run.cancelled",
                    metadata=metadata,
                    created_at=utc_now(),
                ),
            )
            self._append_run_event(connection, cancelled_run, "run.cancelled", metadata)
        return cancelled_run

    def append_run_event(self, run: Run, event_type: str, payload: dict) -> RunEvent:
        self.get_run(run.tenant_id, run.id)
        with self._connect() as connection:
            return self._append_run_event(connection, run, event_type, payload)

    def create_artifact(
        self,
        tenant_id: str,
        run_id: str,
        name: str,
        artifact_type: str,
        uri: str,
    ) -> Artifact:
        run = self.get_run(tenant_id, run_id)
        artifact = Artifact(
            id=new_id("artifact"),
            tenant_id=tenant_id,
            run_id=run_id,
            name=name,
            artifact_type=artifact_type,
            uri=uri,
            created_at=utc_now(),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO artifacts (
                    id, tenant_id, run_id, name, artifact_type, uri, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact.id,
                    artifact.tenant_id,
                    artifact.run_id,
                    artifact.name,
                    artifact.artifact_type,
                    artifact.uri,
                    self._dt(artifact.created_at),
                ),
            )
            self._append_run_event(
                connection,
                run,
                "artifact.created",
                {
                    "artifact_id": artifact.id,
                    "name": artifact.name,
                    "type": artifact.artifact_type,
                },
            )
        return artifact

    def list_artifacts(self, tenant_id: str, run_id: str) -> list[Artifact]:
        self.get_run(tenant_id, run_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM artifacts
                WHERE tenant_id = ? AND run_id = ?
                ORDER BY created_at, id
                """,
                (tenant_id, run_id),
            ).fetchall()
        return [self._artifact_from_row(row) for row in rows]

    def create_approval_request(
        self,
        tenant_id: str,
        run_id: str,
        step_id: str,
        reason: str,
    ) -> ApprovalRequest:
        run = self.get_run(tenant_id, run_id)
        approval = ApprovalRequest(
            id=new_id("approval"),
            tenant_id=tenant_id,
            workspace_id=run.workspace_id,
            run_id=run_id,
            step_id=step_id,
            reason=reason,
            status=ApprovalStatus.PENDING,
            requested_by_user_id=run.user_id,
            created_at=utc_now(),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO approval_requests (
                    id, tenant_id, workspace_id, run_id, step_id, reason, status,
                    requested_by_user_id, resolved_by_user_id, created_at, resolved_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval.id,
                    approval.tenant_id,
                    approval.workspace_id,
                    approval.run_id,
                    approval.step_id,
                    approval.reason,
                    approval.status.value,
                    approval.requested_by_user_id,
                    approval.resolved_by_user_id,
                    self._dt(approval.created_at),
                    None,
                ),
            )
            self._append_run_event(
                connection,
                run,
                "approval.requested",
                {"approval_id": approval.id, "step_id": step_id, "reason": reason},
            )
        return approval

    def resolve_approval_request(
        self,
        tenant_id: str,
        run_id: str,
        approval_id: str,
        approved_by_user_id: str,
    ) -> ApprovalRequest:
        return self._complete_approval_request(
            tenant_id=tenant_id,
            run_id=run_id,
            approval_id=approval_id,
            status=ApprovalStatus.APPROVED,
            resolved_by_user_id=approved_by_user_id,
            event_type="approval.resolved",
        )

    def reject_approval_request(
        self,
        tenant_id: str,
        run_id: str,
        approval_id: str,
        rejected_by_user_id: str,
    ) -> ApprovalRequest:
        return self._complete_approval_request(
            tenant_id=tenant_id,
            run_id=run_id,
            approval_id=approval_id,
            status=ApprovalStatus.REJECTED,
            resolved_by_user_id=rejected_by_user_id,
            event_type="approval.rejected",
        )

    def cancel_pending_approval_requests(
        self,
        tenant_id: str,
        run_id: str,
        cancelled_by_user_id: str,
    ) -> list[ApprovalRequest]:
        run = self.get_run(tenant_id, run_id)
        cancelled: list[ApprovalRequest] = []
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM approval_requests
                WHERE tenant_id = ? AND run_id = ? AND status = ?
                ORDER BY created_at, id
                """,
                (tenant_id, run_id, ApprovalStatus.PENDING.value),
            ).fetchall()
            for row in rows:
                resolved_at = utc_now()
                connection.execute(
                    """
                    UPDATE approval_requests
                    SET status = ?, resolved_by_user_id = ?, resolved_at = ?
                    WHERE id = ?
                    """,
                    (
                        ApprovalStatus.CANCELLED.value,
                        cancelled_by_user_id,
                        self._dt(resolved_at),
                        row["id"],
                    ),
                )
                metadata = {
                    "approval_id": row["id"],
                    "status": ApprovalStatus.CANCELLED.value,
                    "resolved_by_user_id": cancelled_by_user_id,
                }
                self._insert_audit_event(
                    connection,
                    run,
                    AuditEvent(
                        id=new_id("audit"),
                        tenant_id=tenant_id,
                        workspace_id=run.workspace_id,
                        user_id=cancelled_by_user_id,
                        run_id=run_id,
                        event_type="approval.cancelled",
                        metadata=metadata,
                        created_at=utc_now(),
                    ),
                )
                self._append_run_event(connection, run, "approval.cancelled", metadata)
                cancelled.append(
                    ApprovalRequest(
                        id=row["id"],
                        tenant_id=row["tenant_id"],
                        workspace_id=row["workspace_id"],
                        run_id=row["run_id"],
                        step_id=row["step_id"],
                        reason=row["reason"],
                        status=ApprovalStatus.CANCELLED,
                        requested_by_user_id=row["requested_by_user_id"],
                        resolved_by_user_id=cancelled_by_user_id,
                        created_at=self._parse_dt(row["created_at"]),
                        resolved_at=resolved_at,
                    )
                )
        return cancelled

    def _complete_approval_request(
        self,
        tenant_id: str,
        run_id: str,
        approval_id: str,
        status: ApprovalStatus,
        resolved_by_user_id: str,
        event_type: str,
    ) -> ApprovalRequest:
        run = self.get_run(tenant_id, run_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM approval_requests WHERE id = ? AND run_id = ?",
                (approval_id, run_id),
            ).fetchone()
            if row is None:
                raise NotFoundError(f"Approval request not found: {approval_id}")
            if row["tenant_id"] != tenant_id:
                raise TenantAccessError(
                    f"Approval request {approval_id} is not in tenant {tenant_id}"
                )
            resolved_at = utc_now()
            connection.execute(
                """
                UPDATE approval_requests
                SET status = ?, resolved_by_user_id = ?, resolved_at = ?
                WHERE id = ?
                """,
                (
                    status.value,
                    resolved_by_user_id,
                    self._dt(resolved_at),
                    approval_id,
                ),
            )
            self._append_run_event(
                connection,
                run,
                event_type,
                {
                    "approval_id": approval_id,
                    "status": status.value,
                    "resolved_by_user_id": resolved_by_user_id,
                },
            )
            self._insert_audit_event(
                connection,
                run,
                AuditEvent(
                    id=new_id("audit"),
                    tenant_id=tenant_id,
                    workspace_id=run.workspace_id,
                    user_id=resolved_by_user_id,
                    run_id=run_id,
                    event_type=event_type,
                    metadata={
                        "approval_id": approval_id,
                        "status": status.value,
                        "resolved_by_user_id": resolved_by_user_id,
                    },
                    created_at=utc_now(),
                ),
            )
        return ApprovalRequest(
            id=row["id"],
            tenant_id=row["tenant_id"],
            workspace_id=row["workspace_id"],
            run_id=row["run_id"],
            step_id=row["step_id"],
            reason=row["reason"],
            status=status,
            requested_by_user_id=row["requested_by_user_id"],
            resolved_by_user_id=resolved_by_user_id,
            created_at=self._parse_dt(row["created_at"]),
            resolved_at=resolved_at,
        )

    def list_approval_requests(self, tenant_id: str, run_id: str) -> list[ApprovalRequest]:
        self.get_run(tenant_id, run_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM approval_requests
                WHERE tenant_id = ? AND run_id = ?
                ORDER BY created_at, id
                """,
                (tenant_id, run_id),
            ).fetchall()
        return [self._approval_from_row(row) for row in rows]

    def save_runtime_state(self, state: Any) -> RunStateSnapshot:
        self.get_run(state.tenant_id, state.run_id)
        snapshot = RunStateSnapshot.from_runtime_state(state)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runtime_states (
                    run_id, tenant_id, workspace_id, user_id, goal, status,
                    plan, current_step_id, completed_step_ids, approved_step_ids,
                    approved_guardrail_keys, pending_guardrail_approval_key,
                    pending_guardrail_approval_stage, tool_results, retrieved_context,
                    approval_id, failure_reason, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    tenant_id = excluded.tenant_id,
                    workspace_id = excluded.workspace_id,
                    user_id = excluded.user_id,
                    goal = excluded.goal,
                    status = excluded.status,
                    plan = excluded.plan,
                    current_step_id = excluded.current_step_id,
                    completed_step_ids = excluded.completed_step_ids,
                    approved_step_ids = excluded.approved_step_ids,
                    approved_guardrail_keys = excluded.approved_guardrail_keys,
                    pending_guardrail_approval_key = excluded.pending_guardrail_approval_key,
                    pending_guardrail_approval_stage = excluded.pending_guardrail_approval_stage,
                    tool_results = excluded.tool_results,
                    retrieved_context = excluded.retrieved_context,
                    approval_id = excluded.approval_id,
                    failure_reason = excluded.failure_reason,
                    updated_at = excluded.updated_at
                """,
                (
                    snapshot.run_id,
                    snapshot.tenant_id,
                    snapshot.workspace_id,
                    snapshot.user_id,
                    snapshot.goal,
                    snapshot.status.value,
                    self._json(snapshot.plan),
                    snapshot.current_step_id,
                    self._json(snapshot.completed_step_ids),
                    self._json(snapshot.approved_step_ids),
                    self._json(snapshot.approved_guardrail_keys),
                    snapshot.pending_guardrail_approval_key,
                    snapshot.pending_guardrail_approval_stage,
                    self._json(snapshot.tool_results),
                    self._json(snapshot.retrieved_context),
                    snapshot.approval_id,
                    snapshot.failure_reason,
                    self._dt(snapshot.updated_at),
                ),
            )
        return snapshot

    def get_runtime_state(self, tenant_id: str, run_id: str) -> RunStateSnapshot:
        self.get_run(tenant_id, run_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM runtime_states WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Runtime state not found: {run_id}")
        return RunStateSnapshot(
            tenant_id=row["tenant_id"],
            workspace_id=row["workspace_id"],
            user_id=row["user_id"],
            run_id=row["run_id"],
            goal=row["goal"],
            status=RunStatus(row["status"]),
            plan=self._loads(row["plan"]),
            current_step_id=row["current_step_id"],
            completed_step_ids=self._loads(row["completed_step_ids"]),
            approved_step_ids=self._loads(row["approved_step_ids"]),
            approved_guardrail_keys=self._loads(row["approved_guardrail_keys"]),
            pending_guardrail_approval_key=row["pending_guardrail_approval_key"],
            pending_guardrail_approval_stage=row["pending_guardrail_approval_stage"],
            tool_results=self._loads(row["tool_results"]),
            retrieved_context=self._loads(row["retrieved_context"]),
            approval_id=row["approval_id"],
            failure_reason=row["failure_reason"],
            updated_at=self._parse_dt(row["updated_at"]),
        )

    def list_billing_meters(self, tenant_id: str) -> list[BillingMeterEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM billing_meter_events
                WHERE tenant_id = ?
                ORDER BY created_at, id
                """,
                (tenant_id,),
            ).fetchall()
        return [self._billing_meter_from_row(row) for row in rows]

    def list_audit_events(self, tenant_id: str) -> list[AuditEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM audit_events
                WHERE tenant_id = ?
                ORDER BY created_at, id
                """,
                (tenant_id,),
            ).fetchall()
        return [self._audit_event_from_row(row) for row in rows]

    def record_billing_meter(
        self,
        tenant_id: str,
        run_id: str,
        meter_type: str,
        quantity: float,
        unit: str,
        metadata: dict[str, Any] | None = None,
        skill_id: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        cost_estimate: float | None = None,
    ) -> BillingMeterEvent:
        run = self.get_run(tenant_id, run_id)
        meter = BillingMeterEvent(
            id=new_id("meter"),
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            user_id=run.user_id,
            run_id=run.id,
            agent_id=run.agent_id,
            skill_id=skill_id,
            meter_type=meter_type,
            quantity=quantity,
            unit=unit,
            provider=provider,
            model=model,
            cost_estimate=cost_estimate,
            metadata=metadata or {},
            created_at=utc_now(),
        )
        with self._connect() as connection:
            self._insert_billing_meter(connection, run, meter)
        return meter

    def record_audit_event(
        self,
        tenant_id: str,
        workspace_id: str | None,
        user_id: str | None,
        run_id: str | None,
        event_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> AuditEvent:
        run = self.get_run(tenant_id, run_id) if run_id is not None else None
        audit_event = AuditEvent(
            id=new_id("audit"),
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            run_id=run_id,
            event_type=event_type,
            metadata=metadata or {},
            created_at=utc_now(),
        )
        with self._connect() as connection:
            self._insert_audit_event(connection, run, audit_event)
        return audit_event

    def _connect(self):
        path = self.config.sqlite_path
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_context(self, connection, tenant_id: str, workspace_id: str, user_id: str) -> None:
        now = self._dt(utc_now())
        connection.execute(
            "INSERT OR IGNORE INTO tenants (id, name, created_at) VALUES (?, ?, ?)",
            (tenant_id, tenant_id, now),
        )
        connection.execute(
            "INSERT OR IGNORE INTO workspaces (id, tenant_id, name, created_at) VALUES (?, ?, ?, ?)",
            (workspace_id, tenant_id, workspace_id, now),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO users (
                id, tenant_id, email, display_name, password_hash, status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, tenant_id, f"{user_id}@local", user_id, "not_used_for_dev_context", "active", now),
        )

    def _append_run_event(self, connection, run: Run, event_type: str, payload: dict[str, Any]) -> RunEvent:
        event = RunEvent(
            id=new_id("event"),
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            run_id=run.id,
            type=event_type,
            payload=payload,
            created_at=utc_now(),
        )
        connection.execute(
            """
            INSERT INTO run_events (
                id, tenant_id, workspace_id, run_id, type, payload, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.id,
                event.tenant_id,
                event.workspace_id,
                event.run_id,
                event.type,
                self._json(event.payload),
                self._dt(event.created_at),
            ),
        )
        return event

    def _record_run_meter(self, connection, run: Run) -> BillingMeterEvent:
        meter = BillingMeterEvent(
            id=new_id("meter"),
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            user_id=run.user_id,
            run_id=run.id,
            agent_id=run.agent_id,
            meter_type="run_count",
            quantity=1,
            unit="run",
            metadata={"mode": run.mode.value},
            created_at=utc_now(),
        )
        self._insert_billing_meter(connection, run, meter)
        return meter

    def _insert_billing_meter(
        self,
        connection,
        run: Run,
        meter: BillingMeterEvent,
    ) -> None:
        connection.execute(
            """
            INSERT INTO billing_meter_events (
                id, tenant_id, workspace_id, user_id, run_id, agent_id, skill_id,
                meter_type, quantity, unit, provider, model, cost_estimate,
                metadata, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                meter.id,
                meter.tenant_id,
                meter.workspace_id,
                meter.user_id,
                meter.run_id,
                meter.agent_id,
                meter.skill_id,
                meter.meter_type,
                meter.quantity,
                meter.unit,
                meter.provider,
                meter.model,
                meter.cost_estimate,
                self._json(meter.metadata),
                self._dt(meter.created_at),
            ),
        )
        self._append_run_event(run=run, connection=connection, event_type="billing.metered", payload={
            "meter_id": meter.id,
            "type": meter.meter_type,
        })
        self._insert_audit_event(
            connection,
            run,
            AuditEvent(
                id=new_id("audit"),
                tenant_id=meter.tenant_id,
                workspace_id=meter.workspace_id,
                user_id=meter.user_id,
                run_id=meter.run_id,
                event_type="billing.metered",
                metadata=self._billing_audit_metadata(meter),
                created_at=utc_now(),
            ),
        )

    def _billing_audit_metadata(self, meter: BillingMeterEvent) -> dict[str, Any]:
        return {
            "meter_id": meter.id,
            "meter_type": meter.meter_type,
            "quantity": meter.quantity,
            "unit": meter.unit,
            "skill_id": meter.skill_id,
            "provider": meter.provider,
            "model": meter.model,
            "cost_estimate": meter.cost_estimate,
        }

    def _record_audit_event(self, connection, run: Run, event_type: str, metadata: dict[str, Any]) -> AuditEvent:
        audit_event = AuditEvent(
            id=new_id("audit"),
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            user_id=run.user_id,
            run_id=run.id,
            event_type=event_type,
            metadata=metadata,
            created_at=utc_now(),
        )
        self._insert_audit_event(connection, run, audit_event)
        return audit_event

    def _insert_audit_event(
        self,
        connection,
        run: Run | None,
        audit_event: AuditEvent,
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_events (
                id, tenant_id, workspace_id, user_id, run_id, event_type, metadata, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_event.id,
                audit_event.tenant_id,
                audit_event.workspace_id,
                audit_event.user_id,
                audit_event.run_id,
                audit_event.event_type,
                self._json(audit_event.metadata),
                self._dt(audit_event.created_at),
            ),
        )
        if run is not None:
            self._append_run_event(run=run, connection=connection, event_type="audit.recorded", payload={
                "audit_event_id": audit_event.id,
            })

    def _artifact_from_row(self, row) -> Artifact:
        return Artifact(
            id=row["id"],
            tenant_id=row["tenant_id"],
            run_id=row["run_id"],
            name=row["name"],
            artifact_type=row["artifact_type"],
            uri=row["uri"],
            created_at=self._parse_dt(row["created_at"]),
        )

    def _approval_from_row(self, row) -> ApprovalRequest:
        return ApprovalRequest(
            id=row["id"],
            tenant_id=row["tenant_id"],
            workspace_id=row["workspace_id"],
            run_id=row["run_id"],
            step_id=row["step_id"],
            reason=row["reason"],
            status=ApprovalStatus(row["status"]),
            requested_by_user_id=row["requested_by_user_id"],
            resolved_by_user_id=row["resolved_by_user_id"],
            created_at=self._parse_dt(row["created_at"]),
            resolved_at=self._parse_dt(row["resolved_at"]) if row["resolved_at"] else None,
        )

    def _billing_meter_from_row(self, row) -> BillingMeterEvent:
        return BillingMeterEvent(
            id=row["id"],
            tenant_id=row["tenant_id"],
            workspace_id=row["workspace_id"],
            user_id=row["user_id"],
            run_id=row["run_id"],
            agent_id=row["agent_id"],
            skill_id=row["skill_id"],
            meter_type=row["meter_type"],
            quantity=row["quantity"],
            unit=row["unit"],
            provider=row["provider"],
            model=row["model"],
            cost_estimate=row["cost_estimate"],
            metadata=self._loads(row["metadata"]),
            created_at=self._parse_dt(row["created_at"]),
        )

    def _audit_event_from_row(self, row) -> AuditEvent:
        return AuditEvent(
            id=row["id"],
            tenant_id=row["tenant_id"],
            workspace_id=row["workspace_id"],
            user_id=row["user_id"],
            run_id=row["run_id"],
            event_type=row["event_type"],
            metadata=self._loads(row["metadata"]),
            created_at=self._parse_dt(row["created_at"]),
        )

    def _run_from_row(self, row) -> Run:
        return Run(
            id=row["id"],
            tenant_id=row["tenant_id"],
            workspace_id=row["workspace_id"],
            user_id=row["user_id"],
            agent_id=row["agent_id"],
            message=row["message"],
            attachments=self._loads(row["attachments"]),
            mode=row["mode"],
            status=RunStatus(row["status"]),
            created_at=self._parse_dt(row["created_at"]),
            updated_at=self._parse_dt(row["updated_at"]),
        )

    def _json(self, value: Any) -> str:
        return json.dumps(value, separators=(",", ":"))

    def _loads(self, value: str | None) -> Any:
        if value is None:
            return None
        return json.loads(value)

    def _dt(self, value: datetime) -> str:
        return value.isoformat()

    def _parse_dt(self, value: str) -> datetime:
        return datetime.fromisoformat(value)
