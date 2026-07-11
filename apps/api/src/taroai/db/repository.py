import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from taroai.db.connection import connect_database
from taroai.db.migrations import MigrationRunner
from taroai.db.models import DatabaseConfig
from taroai.domain import (
    ApprovalRequest,
    ApprovalStatus,
    Artifact,
    AuditEvent,
    BillingMeterEvent,
    ChatMessage,
    ChatMessageCreate,
    ChatMessageDispatchStatus,
    ChatThread,
    ChatThreadCreate,
    IdempotencyRecord,
    Run,
    RunCreate,
    RunEvent,
    RunStatus,
    new_id,
    utc_now,
)
from taroai.errors import AgentActionLeaseConflictError
from taroai.licensing.models import LicenseValidationResult
from taroai.store import (
    NotFoundError,
    RETRYABLE_RUN_STATUSES,
    RunStateSnapshot,
    RunTransitionError,
    TERMINAL_RUN_STATUSES,
    TenantAccessError,
)

if TYPE_CHECKING:
    from taroai.agent.models import (
        AgentAction,
        AgentCheckpoint,
        AgentCycle,
        AgentObservation,
        AgentVerificationResult,
    )


class SqlControlPlaneRepository(BaseModel):
    config: DatabaseConfig

    def initialize_schema(self, migrations_path: Path) -> None:
        MigrationRunner(config=self.config, migrations_path=migrations_path).apply()

    def create_run(self, tenant_id: str, user_id: str, payload: RunCreate) -> Run:
        with self._connect() as connection:
            return self._create_run_with_connection(
                connection,
                tenant_id,
                user_id,
                payload,
            )

    def create_queued_thread_run_if_absent(
        self,
        tenant_id: str,
        user_id: str,
        payload: RunCreate,
    ) -> tuple[Run, bool]:
        if payload.thread_id is None:
            raise ValueError("Thread run creation requires thread_id")
        terminal_statuses = sorted(status.value for status in TERMINAL_RUN_STATUSES)
        placeholders = ", ".join("?" for _ in terminal_statuses)
        with self._connect() as connection:
            if self.config.dialect == "sqlite":
                connection.execute("BEGIN IMMEDIATE")
                lock_suffix = ""
            else:
                lock_suffix = " FOR UPDATE"
            self._ensure_context(
                connection,
                tenant_id,
                payload.workspace_id,
                user_id,
            )
            thread_row = connection.execute(
                """
                SELECT id FROM chat_threads
                WHERE tenant_id = ? AND id = ? AND workspace_id = ?
                """
                + lock_suffix,
                (tenant_id, payload.thread_id, payload.workspace_id),
            ).fetchone()
            if thread_row is None:
                raise ValueError(
                    f"Run thread {payload.thread_id} does not match tenant/workspace"
                )
            active_row = connection.execute(
                f"""
                SELECT * FROM runs
                WHERE tenant_id = ? AND thread_id = ?
                  AND status NOT IN ({placeholders})
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (tenant_id, payload.thread_id, *terminal_statuses),
            ).fetchone()
            if active_row is not None:
                return self._run_from_row(active_row), False
            run = self._create_run_with_connection(
                connection,
                tenant_id,
                user_id,
                payload,
            )
            queued = run.model_copy(
                update={"status": RunStatus.QUEUED, "updated_at": utc_now()}
            )
            connection.execute(
                """
                UPDATE runs SET status = ?, updated_at = ?
                WHERE tenant_id = ? AND id = ?
                """,
                (
                    queued.status.value,
                    self._dt(queued.updated_at),
                    tenant_id,
                    queued.id,
                ),
            )
            self._append_run_event(
                connection,
                queued,
                "run.status_changed",
                {"status": RunStatus.QUEUED.value},
            )
            return queued, True

    def _create_run_with_connection(
        self,
        connection,
        tenant_id: str,
        user_id: str,
        payload: RunCreate,
    ) -> Run:
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
            thread_id=payload.thread_id,
            trigger_message_id=payload.trigger_message_id,
            provider_id=payload.provider_id,
            model_id=payload.model_id,
            reasoning_effort=payload.reasoning_effort,
            resource_refs=payload.resource_refs,
        )
        self._ensure_context(connection, tenant_id, payload.workspace_id, user_id)
        if payload.thread_id is not None:
            thread_row = connection.execute(
                """
                SELECT id FROM chat_threads
                WHERE tenant_id = ? AND id = ? AND workspace_id = ?
                """,
                (tenant_id, payload.thread_id, payload.workspace_id),
            ).fetchone()
            if thread_row is None:
                raise ValueError(
                    f"Run thread {payload.thread_id} does not match tenant/workspace"
                )
        if payload.trigger_message_id is not None:
            if payload.thread_id is None:
                raise ValueError("Run trigger_message_id requires thread_id")
            message_row = connection.execute(
                """
                SELECT id FROM chat_messages
                WHERE tenant_id = ? AND id = ? AND workspace_id = ?
                  AND thread_id = ?
                """,
                (
                    tenant_id,
                    payload.trigger_message_id,
                    payload.workspace_id,
                    payload.thread_id,
                ),
            ).fetchone()
            if message_row is None:
                raise ValueError(
                    "Run trigger message does not match its thread/tenant/workspace"
                )
        connection.execute(
            """
            INSERT INTO runs (
                id, tenant_id, workspace_id, user_id, agent_id, message,
                attachments, mode, status, created_at, updated_at,
                thread_id, trigger_message_id, provider_id, model_id,
                reasoning_effort, resource_refs
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                run.thread_id,
                run.trigger_message_id,
                run.provider_id,
                run.model_id,
                run.reasoning_effort,
                self._json(
                    [
                        reference.model_dump(mode="json")
                        for reference in run.resource_refs
                    ]
                ),
            ),
        )
        self._append_run_event(
            connection,
            run,
            "run.created",
            {
                "status": run.status.value,
                "mode": run.mode.value,
                "agent_id": run.agent_id,
            },
        )
        self._record_run_meter(connection, run)
        self._record_audit_event(
            connection,
            run,
            "run.created",
            {
                "mode": run.mode.value,
                "agent_id": run.agent_id,
            },
        )
        return run

    def create_chat_thread(
        self,
        tenant_id: str,
        user_id: str,
        payload: ChatThreadCreate,
    ) -> ChatThread:
        now = utc_now()
        thread = ChatThread(
            id=new_id("thread"),
            tenant_id=tenant_id,
            workspace_id=payload.workspace_id,
            created_by_user_id=user_id,
            title=payload.title,
            status="active",
            pinned=False,
            provider_id=payload.provider_id,
            model_id=payload.model_id,
            reasoning_effort=payload.reasoning_effort,
            sandbox_session_id=payload.sandbox_session_id,
            created_at=now,
            updated_at=now,
        )
        with self._connect() as connection:
            self._ensure_context(connection, tenant_id, payload.workspace_id, user_id)
            connection.execute(
                """
                INSERT INTO chat_threads (
                    id, tenant_id, workspace_id, created_by_user_id, title,
                    status, pinned, provider_id, model_id, reasoning_effort,
                    sandbox_session_id, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    thread.id,
                    thread.tenant_id,
                    thread.workspace_id,
                    thread.created_by_user_id,
                    thread.title,
                    thread.status.value,
                    thread.pinned,
                    thread.provider_id,
                    thread.model_id,
                    thread.reasoning_effort,
                    thread.sandbox_session_id,
                    self._dt(thread.created_at),
                    self._dt(thread.updated_at),
                ),
            )
        return thread

    def get_chat_thread(self, tenant_id: str, thread_id: str) -> ChatThread:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM chat_threads WHERE tenant_id = ? AND id = ?",
                (tenant_id, thread_id),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Chat thread not found: {thread_id}")
        return self._chat_thread_from_row(row)

    def list_chat_threads(
        self,
        tenant_id: str,
        workspace_id: str | None = None,
    ) -> list[ChatThread]:
        if workspace_id is None:
            sql = """
                SELECT * FROM chat_threads
                WHERE tenant_id = ?
                ORDER BY updated_at DESC, id DESC
            """
            params: tuple[Any, ...] = (tenant_id,)
        else:
            sql = """
                SELECT * FROM chat_threads
                WHERE tenant_id = ? AND workspace_id = ?
                ORDER BY updated_at DESC, id DESC
            """
            params = (tenant_id, workspace_id)
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self._chat_thread_from_row(row) for row in rows]

    def update_chat_thread(
        self,
        tenant_id: str,
        thread_id: str,
        **changes: Any,
    ) -> ChatThread:
        field_columns = {
            "title": "title",
            "status": "status",
            "pinned": "pinned",
            "provider_id": "provider_id",
            "model_id": "model_id",
            "reasoning_effort": "reasoning_effort",
            "sandbox_session_id": "sandbox_session_id",
        }
        unknown_fields = set(changes) - set(field_columns)
        if unknown_fields:
            raise ValueError(f"Unsupported chat thread fields: {sorted(unknown_fields)}")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM chat_threads WHERE tenant_id = ? AND id = ?",
                (tenant_id, thread_id),
            ).fetchone()
            if row is None:
                raise NotFoundError(f"Chat thread not found: {thread_id}")
            thread = self._chat_thread_from_row(row)
            update_payload = {
                **thread.model_dump(),
                **changes,
                "updated_at": utc_now(),
            }
            updated = ChatThread.model_validate(update_payload)
            assignments = [f"{field_columns[field]} = ?" for field in changes]
            values = [
                (
                    getattr(updated, field).value
                    if hasattr(getattr(updated, field), "value")
                    else getattr(updated, field)
                )
                for field in changes
            ]
            assignments.append("updated_at = ?")
            values.append(self._dt(updated.updated_at))
            connection.execute(
                f"""
                UPDATE chat_threads SET {', '.join(assignments)}
                WHERE tenant_id = ? AND id = ?
                """,
                (*values, tenant_id, thread_id),
            )
        return updated

    def append_chat_message(
        self,
        tenant_id: str,
        thread_id: str,
        user_id: str | None,
        payload: ChatMessageCreate,
    ) -> ChatMessage:
        with self._connect() as connection:
            if self.config.dialect == "sqlite":
                connection.execute("BEGIN IMMEDIATE")
                lock_suffix = ""
            else:
                lock_suffix = " FOR UPDATE"
            thread_row = connection.execute(
                "SELECT * FROM chat_threads WHERE tenant_id = ? AND id = ?"
                + lock_suffix,
                (tenant_id, thread_id),
            ).fetchone()
            if thread_row is None:
                raise NotFoundError(f"Chat thread not found: {thread_id}")
            thread = self._chat_thread_from_row(thread_row)
            if user_id is not None:
                self._ensure_context(
                    connection,
                    tenant_id,
                    thread.workspace_id,
                    user_id,
                )
            sequence_row = connection.execute(
                """
                SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence
                FROM chat_messages
                WHERE tenant_id = ? AND thread_id = ?
                """,
                (tenant_id, thread_id),
            ).fetchone()
            sequence = int(sequence_row["next_sequence"])
            now = utc_now()
            message = ChatMessage(
                id=new_id("message"),
                tenant_id=tenant_id,
                workspace_id=thread.workspace_id,
                thread_id=thread.id,
                sequence=sequence,
                created_by_user_id=user_id,
                role=payload.role,
                content=payload.content,
                kind=payload.kind,
                dispatch_status=payload.dispatch_status,
                delivery_status=payload.delivery_status,
                attachments=payload.attachments,
                resource_refs=payload.resource_refs,
                created_at=now,
                updated_at=now,
            )
            connection.execute(
                """
                INSERT INTO chat_messages (
                    id, tenant_id, workspace_id, thread_id, sequence,
                    created_by_user_id, role, kind, content, dispatch_status,
                    delivery_status, attachments, resource_refs, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.id,
                    message.tenant_id,
                    message.workspace_id,
                    message.thread_id,
                    message.sequence,
                    message.created_by_user_id,
                    message.role.value,
                    message.kind,
                    message.content,
                    message.dispatch_status.value,
                    message.delivery_status.value,
                    self._json(message.attachments),
                    self._json(
                        [
                            reference.model_dump(mode="json")
                            for reference in message.resource_refs
                        ]
                    ),
                    self._dt(message.created_at),
                    self._dt(message.updated_at),
                ),
            )
            connection.execute(
                """
                UPDATE chat_threads SET updated_at = ?
                WHERE tenant_id = ? AND id = ?
                """,
                (self._dt(now), tenant_id, thread_id),
            )
        return message

    def get_chat_message(self, tenant_id: str, message_id: str) -> ChatMessage:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM chat_messages WHERE tenant_id = ? AND id = ?",
                (tenant_id, message_id),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Chat message not found: {message_id}")
        return self._chat_message_from_row(row)

    def list_chat_messages(self, tenant_id: str, thread_id: str) -> list[ChatMessage]:
        with self._connect() as connection:
            thread_row = connection.execute(
                "SELECT id FROM chat_threads WHERE tenant_id = ? AND id = ?",
                (tenant_id, thread_id),
            ).fetchone()
            if thread_row is None:
                raise NotFoundError(f"Chat thread not found: {thread_id}")
            rows = connection.execute(
                """
                SELECT * FROM chat_messages
                WHERE tenant_id = ? AND thread_id = ?
                ORDER BY sequence, id
                """,
                (tenant_id, thread_id),
            ).fetchall()
        return [self._chat_message_from_row(row) for row in rows]

    def update_chat_message(
        self,
        tenant_id: str,
        message_id: str,
        **changes: Any,
    ) -> ChatMessage:
        field_columns = {
            "content": "content",
            "kind": "kind",
            "dispatch_status": "dispatch_status",
            "delivery_status": "delivery_status",
            "attachments": "attachments",
            "resource_refs": "resource_refs",
        }
        unknown_fields = set(changes) - set(field_columns)
        if unknown_fields:
            raise ValueError(f"Unsupported chat message fields: {sorted(unknown_fields)}")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM chat_messages WHERE tenant_id = ? AND id = ?",
                (tenant_id, message_id),
            ).fetchone()
            if row is None:
                raise NotFoundError(f"Chat message not found: {message_id}")
            message = self._chat_message_from_row(row)
            updated = ChatMessage.model_validate(
                {
                    **message.model_dump(),
                    **changes,
                    "updated_at": utc_now(),
                }
            )
            assignments = [f"{field_columns[field]} = ?" for field in changes]
            values = []
            for field in changes:
                value = getattr(updated, field)
                if hasattr(value, "value"):
                    value = value.value
                elif field in {"attachments", "resource_refs"}:
                    value = self._json(
                        [
                            item.model_dump(mode="json")
                            if hasattr(item, "model_dump")
                            else item
                            for item in value
                        ]
                    )
                values.append(value)
            assignments.append("updated_at = ?")
            values.append(self._dt(updated.updated_at))
            connection.execute(
                f"""
                UPDATE chat_messages SET {', '.join(assignments)}
                WHERE tenant_id = ? AND id = ?
                """,
                (*values, tenant_id, message_id),
            )
        return updated

    def delete_chat_message(self, tenant_id: str, message_id: str) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM chat_messages WHERE tenant_id = ? AND id = ?",
                (tenant_id, message_id),
            )
            if cursor.rowcount != 1:
                raise NotFoundError(f"Chat message not found: {message_id}")

    def claim_next_queued_message(
        self,
        tenant_id: str,
        thread_id: str,
    ) -> ChatMessage | None:
        with self._connect() as connection:
            if self.config.dialect == "sqlite":
                connection.execute("BEGIN IMMEDIATE")
                candidate_suffix = "LIMIT 1"
            else:
                candidate_suffix = "FOR UPDATE SKIP LOCKED LIMIT 1"
            thread_row = connection.execute(
                "SELECT id FROM chat_threads WHERE tenant_id = ? AND id = ?",
                (tenant_id, thread_id),
            ).fetchone()
            if thread_row is None:
                raise NotFoundError(f"Chat thread not found: {thread_id}")
            updated_at = utc_now()
            row = connection.execute(
                f"""
                UPDATE chat_messages
                SET dispatch_status = ?, updated_at = ?
                WHERE tenant_id = ?
                  AND id = (
                      SELECT id FROM chat_messages
                      WHERE tenant_id = ? AND thread_id = ?
                        AND (
                          dispatch_status = ?
                          OR (dispatch_status = ? AND kind <> 'manual_queue')
                        )
                      ORDER BY sequence, id
                      {candidate_suffix}
                  )
                  AND (
                    dispatch_status = ?
                    OR (dispatch_status = ? AND kind <> 'manual_queue')
                  )
                RETURNING *
                """,
                (
                    ChatMessageDispatchStatus.INFLIGHT.value,
                    self._dt(updated_at),
                    tenant_id,
                    tenant_id,
                    thread_id,
                    ChatMessageDispatchStatus.QUEUED.value,
                    ChatMessageDispatchStatus.READY.value,
                    ChatMessageDispatchStatus.QUEUED.value,
                    ChatMessageDispatchStatus.READY.value,
                ),
            ).fetchone()
            if row is None:
                return None
            return self._chat_message_from_row(row)

    def list_pending_steering_messages(
        self,
        tenant_id: str,
        thread_id: str,
    ) -> list[ChatMessage]:
        with self._connect() as connection:
            thread_row = connection.execute(
                "SELECT id FROM chat_threads WHERE tenant_id = ? AND id = ?",
                (tenant_id, thread_id),
            ).fetchone()
            if thread_row is None:
                raise NotFoundError(f"Chat thread not found: {thread_id}")
            rows = connection.execute(
                """
                SELECT * FROM chat_messages
                WHERE tenant_id = ? AND thread_id = ? AND dispatch_status = ?
                ORDER BY sequence, id
                """,
                (
                    tenant_id,
                    thread_id,
                    ChatMessageDispatchStatus.STEERING.value,
                ),
            ).fetchall()
        return [self._chat_message_from_row(row) for row in rows]

    def mark_steering_applied(self, tenant_id: str, message_id: str) -> ChatMessage:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM chat_messages WHERE tenant_id = ? AND id = ?",
                (tenant_id, message_id),
            ).fetchone()
            if row is None:
                raise NotFoundError(f"Chat message not found: {message_id}")
            message = self._chat_message_from_row(row)
            if message.dispatch_status != ChatMessageDispatchStatus.STEERING:
                raise ValueError(f"Chat message {message_id} is not pending steering")
            updated_at = utc_now()
            result = connection.execute(
                """
                UPDATE chat_messages
                SET dispatch_status = ?, updated_at = ?
                WHERE tenant_id = ? AND id = ? AND dispatch_status = ?
                """,
                (
                    ChatMessageDispatchStatus.COMPLETED.value,
                    self._dt(updated_at),
                    tenant_id,
                    message_id,
                    ChatMessageDispatchStatus.STEERING.value,
                ),
            )
            if result.rowcount != 1:
                raise ValueError(f"Chat message {message_id} steering was already applied")
        return message.model_copy(
            update={
                "dispatch_status": ChatMessageDispatchStatus.COMPLETED,
                "updated_at": updated_at,
            }
        )

    def create_agent_cycle(self, cycle: "AgentCycle") -> "AgentCycle":
        with self._connect() as connection:
            if self.config.dialect == "sqlite":
                connection.execute("BEGIN IMMEDIATE")
            self._lock_run_for_sequence(connection, cycle.tenant_id, cycle.run_id)
            run_row = connection.execute(
                """
                SELECT id, thread_id FROM runs
                WHERE tenant_id = ? AND id = ? AND workspace_id = ?
                """,
                (cycle.tenant_id, cycle.run_id, cycle.workspace_id),
            ).fetchone()
            if run_row is None:
                raise NotFoundError(f"Run not found: {cycle.run_id}")
            if cycle.thread_id != run_row["thread_id"]:
                raise ValueError(
                    f"Agent cycle {cycle.id} thread does not match run {cycle.run_id}"
                )
            duplicate = connection.execute(
                """
                SELECT id FROM agent_cycles
                WHERE tenant_id = ? AND run_id = ? AND iteration = ?
                """,
                (cycle.tenant_id, cycle.run_id, cycle.iteration),
            ).fetchone()
            if duplicate is not None:
                raise ValueError(
                    f"Agent cycle iteration already exists: {cycle.run_id}:{cycle.iteration}"
                )
            connection.execute(
                """
                INSERT INTO agent_cycles (
                    id, tenant_id, workspace_id, thread_id, run_id, iteration,
                    plan_revision, decision, verifier_result, budget_snapshot,
                    status, started_at, completed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cycle.id,
                    cycle.tenant_id,
                    cycle.workspace_id,
                    cycle.thread_id,
                    cycle.run_id,
                    cycle.iteration,
                    cycle.plan_revision,
                    self._json(
                        cycle.decision.model_dump(mode="json")
                        if cycle.decision is not None
                        else None
                    ),
                    self._json(
                        cycle.verifier_result.model_dump(mode="json")
                        if cycle.verifier_result is not None
                        else None
                    ),
                    self._json(cycle.budget_snapshot),
                    cycle.status,
                    self._dt(cycle.started_at),
                    self._dt(cycle.completed_at) if cycle.completed_at is not None else None,
                ),
            )
        return cycle.model_copy(deep=True)

    def complete_agent_cycle(
        self,
        tenant_id: str,
        cycle_id: str,
        *,
        status: str,
        verifier_result: "AgentVerificationResult | None" = None,
    ) -> "AgentCycle":
        if status not in {"completed", "failed", "waiting"}:
            raise ValueError(f"Unsupported completed agent cycle status: {status}")
        with self._connect() as connection:
            if self.config.dialect == "sqlite":
                connection.execute("BEGIN IMMEDIATE")
                lock_suffix = ""
            else:
                lock_suffix = " FOR UPDATE"
            row = connection.execute(
                "SELECT * FROM agent_cycles WHERE tenant_id = ? AND id = ?"
                + lock_suffix,
                (tenant_id, cycle_id),
            ).fetchone()
            if row is None:
                raise NotFoundError(f"Agent cycle not found: {cycle_id}")
            cycle = self._agent_cycle_from_row(row)
            completed_at = utc_now() if status != "waiting" else None
            connection.execute(
                """
                UPDATE agent_cycles
                SET status = ?, verifier_result = ?, completed_at = ?
                WHERE tenant_id = ? AND id = ?
                """,
                (
                    status,
                    self._json(
                        verifier_result.model_dump(mode="json")
                        if verifier_result is not None
                        else None
                    ),
                    self._dt(completed_at) if completed_at is not None else None,
                    tenant_id,
                    cycle_id,
                ),
            )
        return cycle.model_copy(
            update={
                "status": status,
                "verifier_result": verifier_result,
                "completed_at": completed_at,
            },
            deep=True,
        )

    def create_agent_action(self, action: "AgentAction") -> "AgentAction":
        if action.status != "pending":
            raise ValueError(
                f"Agent action {action.id} must be pending until it is claimed"
            )
        try:
            with self._connect() as connection:
                if self.config.dialect == "sqlite":
                    connection.execute("BEGIN IMMEDIATE")
                    lock_suffix = ""
                else:
                    lock_suffix = " FOR UPDATE"
                cycle_row = connection.execute(
                    """
                    SELECT * FROM agent_cycles
                    WHERE tenant_id = ? AND id = ? AND run_id = ? AND workspace_id = ?
                    """
                    + lock_suffix,
                    (
                        action.tenant_id,
                        action.cycle_id,
                        action.run_id,
                        action.workspace_id,
                    ),
                ).fetchone()
                if cycle_row is None:
                    raise NotFoundError(f"Agent cycle not found: {action.cycle_id}")
                cycle = self._agent_cycle_from_row(cycle_row)
                if action.thread_id != cycle.thread_id:
                    raise ValueError(
                        f"Agent action {action.id} thread does not match cycle {cycle.id}"
                    )
                duplicate = connection.execute(
                    """
                    SELECT id FROM agent_actions
                    WHERE tenant_id = ? AND run_id = ? AND action_key = ?
                    """,
                    (action.tenant_id, action.run_id, action.action_key),
                ).fetchone()
                if duplicate is not None:
                    raise ValueError(
                        f"Duplicate action_key for run {action.run_id}: {action.action_key}"
                    )
                connection.execute(
                    """
                    INSERT INTO agent_actions (
                        id, tenant_id, workspace_id, thread_id, run_id, cycle_id,
                        action_key, decision, status, observation, failure_class,
                        lease_owner_id, lease_expires_at, lease_generation,
                        usage, started_at, completed_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        action.id,
                        action.tenant_id,
                        action.workspace_id,
                        action.thread_id,
                        action.run_id,
                        action.cycle_id,
                        action.action_key,
                        self._json(action.decision.model_dump(mode="json")),
                        action.status,
                        self._json(
                            action.observation.model_dump(mode="json")
                            if action.observation is not None
                            else None
                        ),
                        action.failure_class,
                        action.lease_owner_id,
                        (
                            self._dt(action.lease_expires_at)
                            if action.lease_expires_at is not None
                            else None
                        ),
                        action.lease_generation,
                        self._json(action.usage),
                        self._dt(action.started_at) if action.started_at is not None else None,
                        self._dt(action.completed_at) if action.completed_at is not None else None,
                    ),
                )
        except ValueError:
            raise
        except Exception as error:
            if self._is_unique_constraint_error(error):
                raise ValueError(
                    f"Duplicate action_key for run {action.run_id}: {action.action_key}"
                ) from error
            raise
        return action.model_copy(deep=True)

    def get_agent_action(self, tenant_id: str, action_id: str) -> "AgentAction":
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_actions WHERE tenant_id = ? AND id = ?",
                (tenant_id, action_id),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Agent action not found: {action_id}")
        return self._agent_action_from_row(row)

    def list_agent_actions(
        self,
        tenant_id: str,
        run_id: str,
    ) -> "list[AgentAction]":
        self.get_run(tenant_id, run_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM agent_actions
                WHERE tenant_id = ? AND run_id = ?
                ORDER BY COALESCE(started_at, completed_at), id
                """,
                (tenant_id, run_id),
            ).fetchall()
        return [self._agent_action_from_row(row) for row in rows]

    def resolve_uncertain_agent_action(
        self,
        tenant_id: str,
        action_id: str,
        *,
        resolution: str,
        resolved_by_user_id: str,
        note: str = "",
    ) -> "AgentAction":
        from taroai.agent.models import AgentObservation

        if resolution not in {"succeeded", "failed", "retry"}:
            raise ValueError(f"Unsupported uncertain action resolution: {resolution}")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_actions WHERE tenant_id = ? AND id = ?",
                (tenant_id, action_id),
            ).fetchone()
            if row is None:
                raise NotFoundError(f"Agent action not found: {action_id}")
            action = self._agent_action_from_row(row)
            if action.status != "uncertain":
                raise ValueError(f"Agent action {action_id} is not uncertain")
            if resolution == "retry":
                updated_row = connection.execute(
                    """
                    UPDATE agent_actions
                    SET status = 'pending', observation = NULL, failure_class = NULL,
                        lease_owner_id = NULL, lease_expires_at = NULL,
                        started_at = NULL, completed_at = NULL
                    WHERE tenant_id = ? AND id = ? AND status = 'uncertain'
                    RETURNING *
                    """,
                    (tenant_id, action_id),
                ).fetchone()
            else:
                observation = AgentObservation(
                    action_id=action.id,
                    success=resolution == "succeeded",
                    output={
                        "human_resolution": resolution,
                        "resolved_by_user_id": resolved_by_user_id,
                    },
                    error=(note or None) if resolution == "failed" else None,
                    safe_error=(note or None) if resolution == "failed" else None,
                    failure_class=(
                        "human_confirmed_failed" if resolution == "failed" else None
                    ),
                )
                updated_row = connection.execute(
                    """
                    UPDATE agent_actions
                    SET status = ?, observation = ?, failure_class = ?,
                        lease_owner_id = NULL, lease_expires_at = NULL, completed_at = ?
                    WHERE tenant_id = ? AND id = ? AND status = 'uncertain'
                    RETURNING *
                    """,
                    (
                        resolution,
                        self._json(observation.model_dump(mode="json")),
                        observation.failure_class,
                        self._dt(utc_now()),
                        tenant_id,
                        action_id,
                    ),
                ).fetchone()
            if updated_row is None:
                raise ValueError(f"Agent action {action_id} resolution raced")
            updated = self._agent_action_from_row(updated_row)
            run_row = connection.execute(
                "SELECT * FROM runs WHERE tenant_id = ? AND id = ?",
                (tenant_id, action.run_id),
            ).fetchone()
            if run_row is not None:
                self._append_run_event(
                    connection,
                    self._run_from_row(run_row),
                    "agent.action.resolved",
                    {
                        "action_id": action.id,
                        "resolution": resolution,
                        "resolved_by_user_id": resolved_by_user_id,
                        "note": note,
                    },
                )
        return updated

    def pause_connector_action_for_reconnect(
        self,
        tenant_id: str,
        action_id: str,
        *,
        connector_id: str,
    ) -> "AgentAction":
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_actions WHERE tenant_id = ? AND id = ?",
                (tenant_id, action_id),
            ).fetchone()
            if row is None:
                raise NotFoundError(f"Agent action not found: {action_id}")
            action = self._agent_action_from_row(row)
            retry_count = int(action.usage.get("connector_reconnect_retry_count", 0))
            if (
                action.status != "failed"
                or action.failure_class != "connector_reconnect_required"
                or retry_count > 0
            ):
                raise ValueError("connector action is not eligible for reconnect")
            updated_row = connection.execute(
                """
                UPDATE agent_actions SET status = 'uncertain'
                WHERE tenant_id = ? AND id = ? AND status = 'failed'
                  AND failure_class = 'connector_reconnect_required'
                RETURNING *
                """,
                (tenant_id, action_id),
            ).fetchone()
            if updated_row is None:
                raise ValueError("connector action reconnect pause raced")
            run_row = connection.execute(
                "SELECT * FROM runs WHERE tenant_id = ? AND id = ?",
                (tenant_id, action.run_id),
            ).fetchone()
            if run_row is not None:
                self._append_run_event(
                    connection,
                    self._run_from_row(run_row),
                    "connector.reconnect_required",
                    {
                        "connector_id": connector_id,
                        "action_id": action.id,
                        "run_id": action.run_id,
                        "thread_id": action.thread_id,
                    },
                )
            return self._agent_action_from_row(updated_row)

    def retry_connector_action_after_reconnect(
        self,
        tenant_id: str,
        action_id: str,
        *,
        connector_id: str,
        resolved_by_user_id: str,
    ) -> "AgentAction":
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_actions WHERE tenant_id = ? AND id = ?",
                (tenant_id, action_id),
            ).fetchone()
            if row is None:
                raise NotFoundError(f"Agent action not found: {action_id}")
            action = self._agent_action_from_row(row)
            observed_connector = (
                action.observation.output.get("connector_id")
                if action.observation is not None
                else None
            )
            retry_count = int(action.usage.get("connector_reconnect_retry_count", 0))
            if (
                action.status != "uncertain"
                or action.failure_class != "connector_reconnect_required"
                or observed_connector != connector_id
                or retry_count != 0
            ):
                raise ValueError("connector action reconnect retry is no longer available")
            usage = dict(action.usage)
            usage["connector_reconnect_retry_count"] = 1
            updated_row = connection.execute(
                """
                UPDATE agent_actions
                SET status = 'pending', observation = NULL, failure_class = NULL,
                    usage = ?, lease_owner_id = NULL, lease_expires_at = NULL,
                    started_at = NULL, completed_at = NULL
                WHERE tenant_id = ? AND id = ? AND status = 'uncertain'
                  AND failure_class = 'connector_reconnect_required'
                RETURNING *
                """,
                (self._json(usage), tenant_id, action_id),
            ).fetchone()
            if updated_row is None:
                raise ValueError("connector action reconnect retry raced")
            run_row = connection.execute(
                "SELECT * FROM runs WHERE tenant_id = ? AND id = ?",
                (tenant_id, action.run_id),
            ).fetchone()
            if run_row is not None:
                self._append_run_event(
                    connection,
                    self._run_from_row(run_row),
                    "connector.reconnect_resumed",
                    {
                        "connector_id": connector_id,
                        "action_id": action.id,
                        "resolved_by_user_id": resolved_by_user_id,
                    },
                )
            return self._agent_action_from_row(updated_row)

    def claim_agent_action(
        self,
        tenant_id: str,
        action_id: str,
        *,
        lease_owner_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> "AgentAction | None":
        claimed_at = self._lease_time(now or utc_now())
        lease_expires_at = self._lease_expiration(claimed_at, lease_seconds)
        if not lease_owner_id:
            raise ValueError("lease_owner_id must not be empty")
        with self._connect() as connection:
            row = connection.execute(
                """
                UPDATE agent_actions
                SET status = ?, lease_owner_id = ?, lease_expires_at = ?,
                    lease_generation = lease_generation + 1, started_at = ?
                WHERE tenant_id = ? AND id = ? AND status = ?
                RETURNING *
                """,
                (
                    "running",
                    lease_owner_id,
                    self._dt(lease_expires_at),
                    self._dt(claimed_at),
                    tenant_id,
                    action_id,
                    "pending",
                ),
            ).fetchone()
        if row is None:
            return None
        return self._agent_action_from_row(row)

    def renew_agent_action_lease(
        self,
        tenant_id: str,
        action_id: str,
        *,
        lease_owner_id: str,
        lease_generation: int,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> "AgentAction | None":
        renewed_at = self._lease_time(now or utc_now())
        lease_expires_at = self._lease_expiration(renewed_at, lease_seconds)
        with self._connect() as connection:
            row = connection.execute(
                """
                UPDATE agent_actions
                SET lease_expires_at = ?
                WHERE tenant_id = ? AND id = ? AND status = ?
                  AND lease_owner_id = ? AND lease_generation = ?
                  AND lease_expires_at IS NOT NULL AND lease_expires_at > ?
                RETURNING *
                """,
                (
                    self._dt(lease_expires_at),
                    tenant_id,
                    action_id,
                    "running",
                    lease_owner_id,
                    lease_generation,
                    self._dt(renewed_at),
                ),
            ).fetchone()
        if row is None:
            return None
        return self._agent_action_from_row(row)

    def recover_expired_agent_actions(
        self,
        tenant_id: str,
        *,
        now: datetime | None = None,
        limit: int = 100,
    ) -> "list[AgentAction]":
        if limit < 1:
            raise ValueError("limit must be at least 1")
        recovered_at = self._lease_time(now or utc_now())
        with self._connect() as connection:
            if self.config.dialect == "sqlite":
                connection.execute("BEGIN IMMEDIATE")
                lock_clause = ""
            else:
                lock_clause = "FOR UPDATE SKIP LOCKED"
            rows = connection.execute(
                f"""
                SELECT id FROM agent_actions
                WHERE tenant_id = ? AND status = ?
                  AND (lease_expires_at IS NULL OR lease_expires_at <= ?)
                ORDER BY id
                {lock_clause}
                LIMIT ?
                """,
                (tenant_id, "running", self._dt(recovered_at), limit),
            ).fetchall()
            recovered: list[AgentAction] = []
            for candidate in rows:
                row = connection.execute(
                    """
                    UPDATE agent_actions
                    SET status = ?, lease_owner_id = NULL, lease_expires_at = NULL
                    WHERE tenant_id = ? AND id = ? AND status = ?
                      AND (lease_expires_at IS NULL OR lease_expires_at <= ?)
                    RETURNING *
                    """,
                    (
                        "uncertain",
                        tenant_id,
                        candidate["id"],
                        "running",
                        self._dt(recovered_at),
                    ),
                ).fetchone()
                if row is not None:
                    recovered.append(self._agent_action_from_row(row))
        return recovered

    def commit_agent_action_observation(
        self,
        tenant_id: str,
        action_id: str,
        observation: "AgentObservation",
        *,
        lease_owner_id: str,
        lease_generation: int,
        now: datetime | None = None,
        usage: dict[str, Any],
        state_payload: dict[str, Any],
        checksum: str,
        sandbox_checkpoint_ref: str | None = None,
    ) -> "tuple[AgentAction, AgentCheckpoint]":
        from taroai.agent.models import AgentCheckpoint

        json.dumps(
            {
                "observation": observation.model_dump(mode="json"),
                "usage": usage,
                "state_payload": state_payload,
            }
        )
        with self._connect() as connection:
            if self.config.dialect == "sqlite":
                connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM agent_actions WHERE tenant_id = ? AND id = ?",
                (tenant_id, action_id),
            ).fetchone()
            if row is None:
                raise NotFoundError(f"Agent action not found: {action_id}")
            action = self._agent_action_from_row(row)
            if observation.action_id != action.id:
                raise ValueError("Observation action_id does not match the committed action")
            completed_at = self._lease_time(now or utc_now())
            completed_status = "succeeded" if observation.success else "failed"
            committed_row = connection.execute(
                """
                UPDATE agent_actions
                SET status = ?, observation = ?, failure_class = ?, usage = ?,
                    lease_owner_id = NULL, lease_expires_at = NULL, completed_at = ?
                WHERE tenant_id = ? AND id = ? AND status = ?
                  AND lease_owner_id = ? AND lease_generation = ?
                  AND lease_expires_at IS NOT NULL AND lease_expires_at > ?
                RETURNING *
                """,
                (
                    completed_status,
                    self._json(observation.model_dump(mode="json")),
                    observation.failure_class,
                    self._json(usage),
                    self._dt(completed_at),
                    tenant_id,
                    action_id,
                    "running",
                    lease_owner_id,
                    lease_generation,
                    self._dt(completed_at),
                ),
            ).fetchone()
            if committed_row is None:
                raise AgentActionLeaseConflictError(
                    f"Agent action {action_id} lease fence is no longer valid"
                )
            updated_action = self._agent_action_from_row(committed_row)
            self._lock_run_for_sequence(connection, tenant_id, action.run_id)
            sequence_row = connection.execute(
                """
                SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence
                FROM agent_checkpoints
                WHERE tenant_id = ? AND run_id = ?
                """,
                (tenant_id, action.run_id),
            ).fetchone()
            checkpoint = AgentCheckpoint(
                id=new_id("checkpoint"),
                tenant_id=action.tenant_id,
                workspace_id=action.workspace_id,
                thread_id=action.thread_id,
                run_id=action.run_id,
                cycle_id=action.cycle_id,
                sequence=int(sequence_row["next_sequence"]),
                last_committed_action_id=action.id,
                state_payload=state_payload,
                sandbox_checkpoint_ref=sandbox_checkpoint_ref,
                checksum=checksum,
                created_at=completed_at,
            )
            self._insert_agent_checkpoint(connection, checkpoint)
        return updated_action, checkpoint

    def _lease_expiration(self, now: datetime, lease_seconds: int) -> datetime:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be at least 1")
        return now + timedelta(seconds=lease_seconds)

    def _lease_time(self, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Agent action lease times must be timezone-aware")
        return value.astimezone(timezone.utc)

    def create_agent_checkpoint(
        self, checkpoint: "AgentCheckpoint"
    ) -> "AgentCheckpoint":
        json.dumps(checkpoint.model_dump(mode="json"))
        with self._connect() as connection:
            if self.config.dialect == "sqlite":
                connection.execute("BEGIN IMMEDIATE")
            self._lock_run_for_sequence(
                connection,
                checkpoint.tenant_id,
                checkpoint.run_id,
            )
            run_row = connection.execute(
                """
                SELECT id, thread_id FROM runs
                WHERE tenant_id = ? AND id = ? AND workspace_id = ?
                """,
                (checkpoint.tenant_id, checkpoint.run_id, checkpoint.workspace_id),
            ).fetchone()
            if run_row is None:
                raise NotFoundError(f"Run not found: {checkpoint.run_id}")
            if checkpoint.thread_id != run_row["thread_id"]:
                raise ValueError(
                    f"Agent checkpoint {checkpoint.id} thread does not match "
                    f"run {checkpoint.run_id}"
                )
            if checkpoint.cycle_id is not None:
                cycle_row = connection.execute(
                    """
                    SELECT id FROM agent_cycles
                    WHERE tenant_id = ? AND id = ? AND run_id = ?
                      AND workspace_id = ?
                    """,
                    (
                        checkpoint.tenant_id,
                        checkpoint.cycle_id,
                        checkpoint.run_id,
                        checkpoint.workspace_id,
                    ),
                ).fetchone()
                if cycle_row is None:
                    raise ValueError(
                        f"Agent checkpoint {checkpoint.id} cycle does not match its run"
                    )
            if checkpoint.last_committed_action_id is not None:
                action_row = connection.execute(
                    """
                    SELECT id FROM agent_actions
                    WHERE tenant_id = ? AND id = ? AND run_id = ?
                      AND workspace_id = ?
                    """,
                    (
                        checkpoint.tenant_id,
                        checkpoint.last_committed_action_id,
                        checkpoint.run_id,
                        checkpoint.workspace_id,
                    ),
                ).fetchone()
                if action_row is None:
                    raise ValueError(
                        f"Agent checkpoint {checkpoint.id} action does not match its run"
                    )
            sequence_row = connection.execute(
                """
                SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence
                FROM agent_checkpoints
                WHERE tenant_id = ? AND run_id = ?
                """,
                (checkpoint.tenant_id, checkpoint.run_id),
            ).fetchone()
            expected_sequence = int(sequence_row["next_sequence"])
            if checkpoint.sequence != expected_sequence:
                raise ValueError(
                    "Agent checkpoint sequence must be the next checkpoint sequence "
                    f"({expected_sequence}), got {checkpoint.sequence}"
                )
            self._insert_agent_checkpoint(connection, checkpoint)
        return checkpoint.model_copy(deep=True)

    def get_latest_agent_checkpoint(
        self,
        tenant_id: str,
        run_id: str,
    ) -> "AgentCheckpoint | None":
        with self._connect() as connection:
            run_row = connection.execute(
                "SELECT id FROM runs WHERE tenant_id = ? AND id = ?",
                (tenant_id, run_id),
            ).fetchone()
            if run_row is None:
                raise NotFoundError(f"Run not found: {run_id}")
            row = connection.execute(
                """
                SELECT * FROM agent_checkpoints
                WHERE tenant_id = ? AND run_id = ?
                ORDER BY sequence DESC, id DESC
                LIMIT 1
                """,
                (tenant_id, run_id),
            ).fetchone()
        if row is None:
            return None
        return self._agent_checkpoint_from_row(row)

    def get_run(self, tenant_id: str, run_id: str) -> Run:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE tenant_id = ? AND id = ?",
                (tenant_id, run_id),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Run not found: {run_id}")
        if row["tenant_id"] != tenant_id:
            raise TenantAccessError(f"Run {run_id} is not in tenant {tenant_id}")
        return self._run_from_row(row)

    def get_idempotency_record(
        self,
        tenant_id: str,
        key: str,
        method: str,
        path: str,
    ) -> IdempotencyRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM idempotency_records
                WHERE tenant_id = ? AND key = ? AND method = ? AND path = ?
                """,
                (tenant_id, key, method, path),
            ).fetchone()
        if row is None:
            return None
        return self._idempotency_record_from_row(row)

    def reserve_idempotency_record(self, record: IdempotencyRecord) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO idempotency_records (
                    tenant_id, key, method, path, request_hash, status_code,
                    response_body, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.tenant_id,
                    record.key,
                    record.method,
                    record.path,
                    record.request_hash,
                    record.status_code,
                    self._json(record.response_body),
                    self._dt(record.created_at),
                ),
            )
            return cursor.rowcount == 1

    def reclaim_stale_idempotency_record(
        self,
        record: IdempotencyRecord,
        stale_before: datetime,
    ) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE idempotency_records
                SET status_code = ?, response_body = ?, created_at = ?
                WHERE tenant_id = ? AND key = ? AND method = ? AND path = ?
                  AND request_hash = ? AND status_code = ? AND created_at <= ?
                """,
                (
                    record.status_code,
                    self._json(record.response_body),
                    self._dt(record.created_at),
                    record.tenant_id,
                    record.key,
                    record.method,
                    record.path,
                    record.request_hash,
                    102,
                    self._dt(stale_before),
                ),
            )
            return cursor.rowcount == 1

    def delete_idempotency_record(
        self,
        tenant_id: str,
        key: str,
        method: str,
        path: str,
        request_hash: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                DELETE FROM idempotency_records
                WHERE tenant_id = ? AND key = ? AND method = ? AND path = ?
                  AND request_hash = ?
                """,
                (tenant_id, key, method, path, request_hash),
            )

    def save_idempotency_record(self, record: IdempotencyRecord) -> IdempotencyRecord:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO idempotency_records (
                    tenant_id, key, method, path, request_hash, status_code,
                    response_body, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.tenant_id,
                    record.key,
                    record.method,
                    record.path,
                    record.request_hash,
                    record.status_code,
                    self._json(record.response_body),
                    self._dt(record.created_at),
                ),
            )
        return record

    def save_license_validation(
        self,
        validation: LicenseValidationResult,
    ) -> LicenseValidationResult:
        tenant_id = validation.license.tenant_id
        with self._connect() as connection:
            self._ensure_tenant(connection, tenant_id)
            connection.execute(
                """
                INSERT INTO license_validations (
                    tenant_id, license_id, status, validation, updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id) DO UPDATE SET
                    license_id = excluded.license_id,
                    status = excluded.status,
                    validation = excluded.validation,
                    updated_at = excluded.updated_at
                """,
                (
                    tenant_id,
                    validation.license.id,
                    validation.status.value,
                    self._json(validation.model_dump(mode="json")),
                    self._dt(utc_now()),
                ),
            )
        return validation

    def get_active_license_validation(
        self,
        tenant_id: str,
    ) -> LicenseValidationResult | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT validation
                FROM license_validations
                WHERE tenant_id = ? AND status = ?
                """,
                (tenant_id, "active"),
            ).fetchone()
        if row is None:
            return None
        return LicenseValidationResult.model_validate(self._loads(row["validation"]))

    def list_runs(
        self,
        tenant_id: str,
        workspace_id: str | None = None,
        status: RunStatus | None = None,
    ) -> list[Run]:
        clauses = ["tenant_id = ?"]
        params: list[Any] = [tenant_id]
        if workspace_id is not None:
            clauses.append("workspace_id = ?")
            params.append(workspace_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM runs
                WHERE {' AND '.join(clauses)}
                ORDER BY created_at, id
                """,
                params,
            ).fetchall()
        return [self._run_from_row(row) for row in rows]

    def get_active_thread_run(
        self,
        tenant_id: str,
        thread_id: str,
    ) -> Run | None:
        with self._connect() as connection:
            thread_row = connection.execute(
                "SELECT id FROM chat_threads WHERE tenant_id = ? AND id = ?",
                (tenant_id, thread_id),
            ).fetchone()
            if thread_row is None:
                raise NotFoundError(f"Chat thread not found: {thread_id}")
            placeholders = ", ".join("?" for _ in TERMINAL_RUN_STATUSES)
            rows = connection.execute(
                f"""
                SELECT * FROM runs
                WHERE tenant_id = ? AND thread_id = ?
                  AND status NOT IN ({placeholders})
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (
                    tenant_id,
                    thread_id,
                    *(status.value for status in TERMINAL_RUN_STATUSES),
                ),
            ).fetchall()
        if not rows:
            return None
        return self._run_from_row(rows[0])

    def list_run_events(
        self,
        tenant_id: str,
        run_id: str,
        after_sequence: int | None = None,
    ) -> list[RunEvent]:
        self.get_run(tenant_id, run_id)
        with self._connect() as connection:
            if after_sequence is None:
                rows = connection.execute(
                    """
                    SELECT * FROM run_events
                    WHERE tenant_id = ? AND run_id = ?
                    ORDER BY sequence, created_at, id
                    """,
                    (tenant_id, run_id),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM run_events
                    WHERE tenant_id = ? AND run_id = ? AND sequence > ?
                    ORDER BY sequence, created_at, id
                    """,
                    (tenant_id, run_id, after_sequence),
                ).fetchall()
        return [
            RunEvent(
                id=row["id"],
                sequence=int(row["sequence"]),
                tenant_id=row["tenant_id"],
                workspace_id=row["workspace_id"],
                run_id=row["run_id"],
                type=row["type"],
                payload=self._loads(row["payload"]),
                created_at=self._parse_dt(row["created_at"]),
                thread_id=self._row_value(row, "thread_id"),
                thread_sequence=(
                    int(self._row_value(row, "thread_sequence"))
                    if self._row_value(row, "thread_sequence") is not None
                    else None
                ),
            )
            for row in rows
        ]

    def list_thread_events(
        self,
        tenant_id: str,
        thread_id: str,
        after_sequence: int | None = None,
    ) -> list[RunEvent]:
        self.get_chat_thread(tenant_id, thread_id)
        query = """
            SELECT * FROM run_events
            WHERE tenant_id = ? AND thread_id = ? AND thread_sequence IS NOT NULL
        """
        parameters: list[Any] = [tenant_id, thread_id]
        if after_sequence is not None:
            query += " AND thread_sequence > ?"
            parameters.append(after_sequence)
        query += " ORDER BY thread_sequence, id"
        with self._connect() as connection:
            rows = connection.execute(query, tuple(parameters)).fetchall()
        return [
            RunEvent(
                id=row["id"],
                sequence=int(row["sequence"]),
                tenant_id=row["tenant_id"],
                workspace_id=row["workspace_id"],
                run_id=row["run_id"],
                type=row["type"],
                payload=self._loads(row["payload"]),
                created_at=self._parse_dt(row["created_at"]),
                thread_id=self._row_value(row, "thread_id"),
                thread_sequence=int(self._row_value(row, "thread_sequence")),
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
                "UPDATE runs SET status = ?, updated_at = ? WHERE tenant_id = ? AND id = ?",
                (
                    updated_run.status.value,
                    self._dt(updated_run.updated_at),
                    tenant_id,
                    run_id,
                ),
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
                "UPDATE runs SET status = ?, updated_at = ? WHERE tenant_id = ? AND id = ?",
                (
                    cancelled_run.status.value,
                    self._dt(cancelled_run.updated_at),
                    tenant_id,
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

    def request_run_retry(
        self,
        tenant_id: str,
        run_id: str,
        requested_by_user_id: str,
        reason_code: str,
    ) -> Run:
        run = self.get_run(tenant_id, run_id)
        if run.status not in RETRYABLE_RUN_STATUSES:
            raise RunTransitionError(f"Run {run_id} cannot be retried from {run.status.value}")
        retrying_run = run.model_copy(
            update={"status": RunStatus.RETRYING, "updated_at": utc_now()}
        )
        metadata = {
            "requested_by_user_id": requested_by_user_id,
            "reason_code": reason_code,
            "previous_status": run.status.value,
            "status": RunStatus.RETRYING.value,
        }
        with self._connect() as connection:
            connection.execute(
                "UPDATE runs SET status = ?, updated_at = ? WHERE tenant_id = ? AND id = ?",
                (
                    retrying_run.status.value,
                    self._dt(retrying_run.updated_at),
                    tenant_id,
                    run_id,
                ),
            )
            self._insert_audit_event(
                connection,
                retrying_run,
                AuditEvent(
                    id=new_id("audit"),
                    tenant_id=tenant_id,
                    workspace_id=run.workspace_id,
                    user_id=requested_by_user_id,
                    run_id=run_id,
                    event_type="run.retry_requested",
                    metadata=metadata,
                    created_at=utc_now(),
                ),
            )
            self._append_run_event(connection, retrying_run, "run.retry_requested", metadata)
        return retrying_run

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
        **rich_fields: Any,
    ) -> Artifact:
        run = self.get_run(tenant_id, run_id)
        rich_fields.setdefault("workspace_id", run.workspace_id)
        rich_fields.setdefault("thread_id", run.thread_id)
        artifact = Artifact(
            id=new_id("artifact"),
            tenant_id=tenant_id,
            run_id=run_id,
            name=name,
            artifact_type=artifact_type,
            uri=uri,
            created_at=utc_now(),
            **rich_fields,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO artifacts (
                    id, tenant_id, run_id, name, artifact_type, uri, created_at,
                    workspace_id, thread_id, message_id, storage_object_id,
                    content_type, size_bytes, preview_payload, dashboard_payload,
                    render_policy, metadata
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact.id,
                    artifact.tenant_id,
                    artifact.run_id,
                    artifact.name,
                    artifact.artifact_type,
                    artifact.uri,
                    self._dt(artifact.created_at),
                    artifact.workspace_id,
                    artifact.thread_id,
                    artifact.message_id,
                    artifact.storage_object_id,
                    artifact.content_type,
                    artifact.size_bytes,
                    self._json(artifact.preview_payload),
                    self._json(artifact.dashboard_payload) if artifact.dashboard_payload is not None else None,
                    self._json(artifact.render_policy),
                    self._json(artifact.metadata),
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

    def get_artifact(self, tenant_id: str, artifact_id: str) -> Artifact:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM artifacts WHERE tenant_id = ? AND id = ?",
                (tenant_id, artifact_id),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Artifact not found: {artifact_id}")
        return self._artifact_from_row(row)

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
                    WHERE tenant_id = ? AND id = ?
                    """,
                    (
                        ApprovalStatus.CANCELLED.value,
                        cancelled_by_user_id,
                        self._dt(resolved_at),
                        tenant_id,
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
                "SELECT * FROM approval_requests WHERE tenant_id = ? AND id = ? AND run_id = ?",
                (tenant_id, approval_id, run_id),
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
                WHERE tenant_id = ? AND id = ?
                """,
                (
                    status.value,
                    resolved_by_user_id,
                    self._dt(resolved_at),
                    tenant_id,
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
                    sandbox_session_id, browser_session_id, approval_id,
                    failure_reason, state_payload, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    sandbox_session_id = excluded.sandbox_session_id,
                    browser_session_id = excluded.browser_session_id,
                    approval_id = excluded.approval_id,
                    failure_reason = excluded.failure_reason,
                    state_payload = excluded.state_payload,
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
                    snapshot.sandbox_session_id,
                    snapshot.browser_session_id,
                    snapshot.approval_id,
                    snapshot.failure_reason,
                    self._json(snapshot.state_payload),
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
            sandbox_session_id=row["sandbox_session_id"],
            browser_session_id=row["browser_session_id"],
            approval_id=row["approval_id"],
            failure_reason=row["failure_reason"],
            state_payload=self._loads(row["state_payload"]),
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
                ORDER BY created_at, event_type, id
                """,
                (tenant_id,),
            ).fetchall()
        return [self._audit_event_from_row(row) for row in rows]

    def record_billing_meter(
        self,
        tenant_id: str,
        run_id: str | None,
        meter_type: str,
        quantity: float,
        unit: str,
        metadata: dict[str, Any] | None = None,
        skill_id: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        cost_estimate: float | None = None,
        workspace_id: str | None = None,
        user_id: str | None = None,
        agent_id: str | None = None,
    ) -> BillingMeterEvent:
        run = self.get_run(tenant_id, run_id) if run_id is not None else None
        resolved_workspace_id = run.workspace_id if run is not None else workspace_id
        resolved_user_id = run.user_id if run is not None else user_id
        resolved_agent_id = run.agent_id if run is not None else agent_id
        if resolved_workspace_id is None or resolved_user_id is None:
            raise ValueError("workspace_id and user_id are required when run_id is not provided")
        meter = BillingMeterEvent(
            id=new_id("meter"),
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            user_id=resolved_user_id,
            run_id=run.id if run is not None else None,
            agent_id=resolved_agent_id,
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
        return connect_database(self.config)

    def _ensure_context(self, connection, tenant_id: str, workspace_id: str, user_id: str) -> None:
        self._ensure_tenant(connection, tenant_id)
        now = self._dt(utc_now())
        self._insert_context_record_or_verify_owner(
            connection,
            insert_sql="""
                INSERT INTO workspaces (id, tenant_id, name, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT DO NOTHING
                RETURNING id
            """,
            insert_params=(workspace_id, tenant_id, workspace_id, now),
            lookup_sql="""
                SELECT id FROM workspaces
                WHERE tenant_id = ? AND id = ?
            """,
            lookup_params=(tenant_id, workspace_id),
            resource_name=f"Workspace {workspace_id}",
        )
        self._insert_context_record_or_verify_owner(
            connection,
            insert_sql="""
                INSERT INTO users (
                    id, tenant_id, email, display_name, password_hash, status, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO NOTHING
                RETURNING id
            """,
            insert_params=(
                user_id,
                tenant_id,
                f"{user_id}@local",
                user_id,
                "not_used_for_dev_context",
                "active",
                now,
            ),
            lookup_sql="""
                SELECT id FROM users
                WHERE tenant_id = ? AND id = ?
            """,
            lookup_params=(tenant_id, user_id),
            resource_name=f"User {user_id}",
        )

    def _insert_context_record_or_verify_owner(
        self,
        connection,
        *,
        insert_sql: str,
        insert_params: tuple[Any, ...],
        lookup_sql: str,
        lookup_params: tuple[Any, ...],
        resource_name: str,
    ) -> None:
        inserted = connection.execute(insert_sql, insert_params).fetchone()
        if inserted is not None:
            return
        owned = connection.execute(lookup_sql, lookup_params).fetchone()
        if owned is None:
            raise TenantAccessError(f"{resource_name} is not in the active tenant")

    def _ensure_tenant(self, connection, tenant_id: str) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO tenants (id, name, created_at) VALUES (?, ?, ?)",
            (tenant_id, tenant_id, self._dt(utc_now())),
        )

    def _lock_run_for_sequence(self, connection, tenant_id: str, run_id: str) -> None:
        if self.config.dialect == "postgresql":
            row = connection.execute(
                """
                SELECT id FROM runs
                WHERE tenant_id = ? AND id = ?
                FOR UPDATE
                """,
                (tenant_id, run_id),
            ).fetchone()
            found = row is not None
        else:
            result = connection.execute(
                """
                UPDATE runs SET updated_at = updated_at
                WHERE tenant_id = ? AND id = ?
                """,
                (tenant_id, run_id),
            )
            found = result.rowcount == 1
        if not found:
            raise NotFoundError(f"Run not found: {run_id}")

    def _lock_chat_thread_for_sequence(
        self,
        connection,
        tenant_id: str,
        thread_id: str,
    ) -> None:
        if self.config.dialect == "postgresql":
            row = connection.execute(
                """
                SELECT id FROM chat_threads
                WHERE tenant_id = ? AND id = ?
                FOR UPDATE
                """,
                (tenant_id, thread_id),
            ).fetchone()
            found = row is not None
        else:
            result = connection.execute(
                """
                UPDATE chat_threads SET updated_at = updated_at
                WHERE tenant_id = ? AND id = ?
                """,
                (tenant_id, thread_id),
            )
            found = result.rowcount == 1
        if not found:
            raise NotFoundError(f"Chat thread not found: {thread_id}")

    def _append_run_event(self, connection, run: Run, event_type: str, payload: dict[str, Any]) -> RunEvent:
        if run.thread_id is not None:
            self._lock_chat_thread_for_sequence(
                connection,
                run.tenant_id,
                run.thread_id,
            )
        sequence_row = connection.execute(
            """
            SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence
            FROM run_events
            WHERE tenant_id = ? AND run_id = ?
            """,
            (run.tenant_id, run.id),
        ).fetchone()
        sequence = int(sequence_row["next_sequence"]) if sequence_row is not None else 1
        thread_sequence: int | None = None
        if run.thread_id is not None:
            thread_sequence_row = connection.execute(
                """
                SELECT COALESCE(MAX(thread_sequence), 0) + 1 AS next_sequence
                FROM run_events
                WHERE tenant_id = ? AND thread_id = ?
                """,
                (run.tenant_id, run.thread_id),
            ).fetchone()
            thread_sequence = (
                int(thread_sequence_row["next_sequence"])
                if thread_sequence_row is not None
                else 1
            )
        event = RunEvent(
            id=new_id("event"),
            sequence=sequence,
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            run_id=run.id,
            type=event_type,
            payload=payload,
            created_at=utc_now(),
            thread_id=run.thread_id,
            thread_sequence=thread_sequence,
        )
        connection.execute(
            """
            INSERT INTO run_events (
                id, sequence, tenant_id, workspace_id, run_id, type, payload,
                created_at, thread_id, thread_sequence
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.id,
                event.sequence,
                event.tenant_id,
                event.workspace_id,
                event.run_id,
                event.type,
                self._json(event.payload),
                self._dt(event.created_at),
                event.thread_id,
                event.thread_sequence,
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
        run: Run | None,
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
        if run is not None:
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

    def _insert_agent_checkpoint(
        self,
        connection,
        checkpoint: "AgentCheckpoint",
    ) -> None:
        connection.execute(
            """
            INSERT INTO agent_checkpoints (
                id, tenant_id, workspace_id, thread_id, run_id, cycle_id,
                sequence, last_committed_action_id, state_payload,
                sandbox_checkpoint_ref, checksum, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                checkpoint.id,
                checkpoint.tenant_id,
                checkpoint.workspace_id,
                checkpoint.thread_id,
                checkpoint.run_id,
                checkpoint.cycle_id,
                checkpoint.sequence,
                checkpoint.last_committed_action_id,
                self._json(checkpoint.state_payload),
                checkpoint.sandbox_checkpoint_ref,
                checkpoint.checksum,
                self._dt(checkpoint.created_at),
            ),
        )

    def _chat_thread_from_row(self, row) -> ChatThread:
        return ChatThread(
            id=row["id"],
            tenant_id=row["tenant_id"],
            workspace_id=row["workspace_id"],
            created_by_user_id=row["created_by_user_id"],
            title=row["title"],
            status=row["status"],
            pinned=bool(row["pinned"]),
            provider_id=row["provider_id"],
            model_id=row["model_id"],
            reasoning_effort=row["reasoning_effort"],
            sandbox_session_id=row["sandbox_session_id"],
            created_at=self._parse_dt(row["created_at"]),
            updated_at=self._parse_dt(row["updated_at"]),
        )

    def _chat_message_from_row(self, row) -> ChatMessage:
        return ChatMessage(
            id=row["id"],
            tenant_id=row["tenant_id"],
            workspace_id=row["workspace_id"],
            thread_id=row["thread_id"],
            sequence=int(row["sequence"]),
            created_by_user_id=row["created_by_user_id"],
            role=row["role"],
            content=row["content"],
            kind=row["kind"],
            dispatch_status=row["dispatch_status"],
            delivery_status=row["delivery_status"],
            attachments=self._loads(row["attachments"]),
            resource_refs=self._loads(row["resource_refs"]),
            created_at=self._parse_dt(row["created_at"]),
            updated_at=self._parse_dt(row["updated_at"]),
        )

    def _agent_cycle_from_row(self, row) -> "AgentCycle":
        from taroai.agent.models import AgentCycle

        return AgentCycle(
            id=row["id"],
            tenant_id=row["tenant_id"],
            workspace_id=row["workspace_id"],
            thread_id=row["thread_id"],
            run_id=row["run_id"],
            iteration=int(row["iteration"]),
            plan_revision=int(row["plan_revision"]),
            decision=self._loads(row["decision"]),
            verifier_result=self._loads(row["verifier_result"]),
            budget_snapshot=self._loads(row["budget_snapshot"]),
            status=row["status"],
            started_at=self._parse_dt(row["started_at"]),
            completed_at=self._parse_optional_dt(row["completed_at"]),
        )

    def _agent_action_from_row(self, row) -> "AgentAction":
        from taroai.agent.models import AgentAction

        return AgentAction(
            id=row["id"],
            tenant_id=row["tenant_id"],
            workspace_id=row["workspace_id"],
            thread_id=row["thread_id"],
            run_id=row["run_id"],
            cycle_id=row["cycle_id"],
            action_key=row["action_key"],
            decision=self._loads(row["decision"]),
            status=row["status"],
            observation=self._loads(row["observation"]),
            failure_class=self._row_value(row, "failure_class"),
            lease_owner_id=self._row_value(row, "lease_owner_id"),
            lease_expires_at=self._parse_optional_dt(
                self._row_value(row, "lease_expires_at")
            ),
            lease_generation=int(self._row_value(row, "lease_generation", 0)),
            usage=self._loads(row["usage"]),
            started_at=self._parse_optional_dt(row["started_at"]),
            completed_at=self._parse_optional_dt(row["completed_at"]),
        )

    def _agent_checkpoint_from_row(self, row) -> "AgentCheckpoint":
        from taroai.agent.models import AgentCheckpoint

        return AgentCheckpoint(
            id=row["id"],
            tenant_id=row["tenant_id"],
            workspace_id=row["workspace_id"],
            thread_id=row["thread_id"],
            run_id=row["run_id"],
            cycle_id=row["cycle_id"],
            sequence=int(row["sequence"]),
            last_committed_action_id=row["last_committed_action_id"],
            state_payload=self._loads(row["state_payload"]),
            sandbox_checkpoint_ref=row["sandbox_checkpoint_ref"],
            checksum=row["checksum"],
            created_at=self._parse_dt(row["created_at"]),
        )

    def _is_unique_constraint_error(self, error: Exception) -> bool:
        error_text = str(error).lower()
        error_sqlstate = getattr(error, "sqlstate", "")
        return (
            "unique constraint" in error_text
            or "unique violation" in error_text
            or error_sqlstate == "23505"
        )

    def _artifact_from_row(self, row) -> Artifact:
        return Artifact(
            id=row["id"],
            tenant_id=row["tenant_id"],
            run_id=row["run_id"],
            name=row["name"],
            artifact_type=row["artifact_type"],
            uri=row["uri"],
            created_at=self._parse_dt(row["created_at"]),
            workspace_id=self._row_value(row, "workspace_id"),
            thread_id=self._row_value(row, "thread_id"),
            message_id=self._row_value(row, "message_id"),
            storage_object_id=self._row_value(row, "storage_object_id"),
            content_type=self._row_value(row, "content_type"),
            size_bytes=int(self._row_value(row, "size_bytes") or 0),
            preview_payload=self._loads(self._row_value(row, "preview_payload") or "{}"),
            dashboard_payload=(
                self._loads(self._row_value(row, "dashboard_payload"))
                if self._row_value(row, "dashboard_payload") is not None
                else None
            ),
            render_policy=self._loads(self._row_value(row, "render_policy") or "{}"),
            metadata=self._loads(self._row_value(row, "metadata") or "{}"),
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
            thread_id=self._row_value(row, "thread_id"),
            trigger_message_id=self._row_value(row, "trigger_message_id"),
            provider_id=self._row_value(row, "provider_id"),
            model_id=self._row_value(row, "model_id"),
            reasoning_effort=self._row_value(row, "reasoning_effort"),
            resource_refs=self._loads(self._row_value(row, "resource_refs", "[]")),
        )

    def _idempotency_record_from_row(self, row) -> IdempotencyRecord:
        return IdempotencyRecord(
            tenant_id=row["tenant_id"],
            key=row["key"],
            method=row["method"],
            path=row["path"],
            request_hash=row["request_hash"],
            status_code=row["status_code"],
            response_body=self._loads(row["response_body"]),
            created_at=self._parse_dt(row["created_at"]),
        )

    def _json(self, value: Any) -> str:
        return json.dumps(value, separators=(",", ":"))

    def _loads(self, value: str | None) -> Any:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        return json.loads(value)

    def _row_value(self, row, key: str, default: Any = None) -> Any:
        try:
            return row[key]
        except (KeyError, IndexError):
            return default

    def _dt(self, value: datetime) -> str:
        return value.isoformat()

    def _parse_dt(self, value: datetime | str) -> datetime:
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(value)

    def _parse_optional_dt(self, value: datetime | str | None) -> datetime | None:
        if value is None:
            return None
        return self._parse_dt(value)
