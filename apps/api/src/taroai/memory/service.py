import json
import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from taroai.audit import AuditActor, AuditEventCreate
from taroai.domain import utc_now
from taroai.guardrails.models import (
    GuardrailAction,
    GuardrailDecision,
    GuardrailEvaluationRequest,
    GuardrailStage,
)
from taroai.memory.models import (
    MemoryRecord,
    MemoryScopeType,
    MemoryStatus,
    MemoryWriteRequest,
    ShortTermMemoryEntry,
    ShortTermMemoryReview,
    ShortTermMemoryReviewStatus,
    ShortTermMemoryWrite,
)
from taroai.store import NotFoundError, TenantAccessError


class MemoryWriteRejectedError(RuntimeError):
    def __init__(self, message: str, metadata: dict[str, Any] | None = None):
        super().__init__(message)
        self.metadata = metadata or {}


class InMemoryLongTermMemoryService(BaseModel):
    records: list[MemoryRecord] = Field(default_factory=list)

    def write(self, request: MemoryWriteRequest) -> MemoryRecord:
        record = MemoryRecord(**request.model_dump())
        self.records.append(record)
        return record

    def propose_candidate(self, request: MemoryWriteRequest) -> MemoryRecord:
        return self.write(request.model_copy(update={"status": MemoryStatus.CANDIDATE}))

    def get(self, tenant_id: str, memory_id: str) -> MemoryRecord:
        for record in self.records:
            if record.id != memory_id:
                continue
            if record.tenant_id != tenant_id:
                raise TenantAccessError(f"Memory {memory_id} is not in tenant {tenant_id}")
            return record
        raise NotFoundError(f"Memory not found: {memory_id}")

    def approve(
        self,
        tenant_id: str,
        memory_id: str,
        reviewed_by_user_id: str,
    ) -> MemoryRecord:
        return self._update_status(tenant_id, memory_id, MemoryStatus.ACTIVE)

    def reject(
        self,
        tenant_id: str,
        memory_id: str,
        reviewed_by_user_id: str,
    ) -> MemoryRecord:
        return self._update_status(tenant_id, memory_id, MemoryStatus.REJECTED)

    def list_by_scope(
        self,
        tenant_id: str,
        scope_type: MemoryScopeType,
        scope_id: str,
    ) -> list[MemoryRecord]:
        return [
            record
            for record in self.records
            if record.tenant_id == tenant_id
            and record.scope_type == scope_type
            and record.scope_id == scope_id
            and record.status == MemoryStatus.ACTIVE
        ]

    def forget(self, tenant_id: str, memory_id: str) -> MemoryRecord:
        record = self.get(tenant_id, memory_id)
        forgotten = record.model_copy(
            update={
                "content": "",
                "metadata": {},
                "status": MemoryStatus.EXPIRED,
                "expires_at": utc_now(),
            }
        )
        for index, existing in enumerate(self.records):
            if existing.id == memory_id:
                self.records[index] = forgotten
                return forgotten
        raise NotFoundError(f"Memory not found: {memory_id}")

    def delete_for_tenant(self, tenant_id: str) -> list[str]:
        deleted_ids: list[str] = []
        for index, record in enumerate(self.records):
            if record.tenant_id != tenant_id or record.status == MemoryStatus.EXPIRED:
                continue
            deleted_ids.append(record.id)
            self.records[index] = record.model_copy(
                update={
                    "content": "",
                    "metadata": {},
                    "status": MemoryStatus.EXPIRED,
                }
            )
        return deleted_ids

    def _update_status(
        self,
        tenant_id: str,
        memory_id: str,
        status: MemoryStatus,
    ) -> MemoryRecord:
        record = self.get(tenant_id, memory_id)
        updated = record.model_copy(update={"status": status})
        for index, existing in enumerate(self.records):
            if existing.id == memory_id:
                self.records[index] = updated
                return updated
        raise NotFoundError(f"Memory not found: {memory_id}")


class InMemoryShortTermMemoryService(BaseModel):
    entries: dict[str, ShortTermMemoryEntry] = Field(default_factory=dict)

    def put(
        self,
        request: ShortTermMemoryWrite,
        now: datetime | None = None,
    ) -> ShortTermMemoryEntry:
        resolved_now = now or utc_now()
        entry = ShortTermMemoryEntry.from_write(request, resolved_now)
        self.entries[self._key(request.tenant_id, request.run_id, request.key)] = entry
        return entry

    def get(
        self,
        tenant_id: str,
        run_id: str,
        key: str,
        now: datetime | None = None,
    ) -> ShortTermMemoryEntry | None:
        resolved_now = now or utc_now()
        entry = self.entries.get(self._key(tenant_id, run_id, key))
        if entry is None:
            return None
        if entry.is_expired(resolved_now):
            del self.entries[self._key(tenant_id, run_id, key)]
            return None
        return entry

    def list_for_run(
        self,
        tenant_id: str,
        run_id: str,
        now: datetime | None = None,
    ) -> list[ShortTermMemoryEntry]:
        resolved_now = now or utc_now()
        entries: list[ShortTermMemoryEntry] = []
        expired_keys: list[str] = []
        prefix = self._prefix(tenant_id, run_id)
        for stored_key, entry in self.entries.items():
            if not stored_key.startswith(prefix):
                continue
            if entry.is_expired(resolved_now):
                expired_keys.append(stored_key)
                continue
            entries.append(entry)
        for stored_key in expired_keys:
            del self.entries[stored_key]
        return sorted(entries, key=lambda entry: entry.key)

    def delete(self, tenant_id: str, run_id: str, key: str) -> bool:
        stored_key = self._key(tenant_id, run_id, key)
        if stored_key not in self.entries:
            return False
        del self.entries[stored_key]
        return True

    def delete_for_tenant(self, tenant_id: str) -> int:
        prefix = f"{tenant_id}:"
        keys = [
            stored_key
            for stored_key in self.entries
            if stored_key.startswith(prefix)
        ]
        for stored_key in keys:
            del self.entries[stored_key]
        return len(keys)

    def _prefix(self, tenant_id: str, run_id: str) -> str:
        return f"{tenant_id}:{run_id}:"

    def _key(self, tenant_id: str, run_id: str, key: str) -> str:
        return f"{self._prefix(tenant_id, run_id)}{key}"


class RedisMemoryConfigurationError(RuntimeError):
    pass


class RedisShortTermMemoryService(BaseModel):
    url: str = Field(min_length=1)
    key_prefix: str = Field(default="taroai:short_term_memory", min_length=1)
    client: Any | None = None

    def put(
        self,
        request: ShortTermMemoryWrite,
        now: datetime | None = None,
    ) -> ShortTermMemoryEntry:
        resolved_now = now or utc_now()
        entry = ShortTermMemoryEntry.from_write(request, resolved_now)
        self._client().set(
            self._key(request.tenant_id, request.run_id, request.key),
            entry.model_dump_json(),
            ex=request.ttl_seconds,
        )
        return entry

    def get(
        self,
        tenant_id: str,
        run_id: str,
        key: str,
        now: datetime | None = None,
    ) -> ShortTermMemoryEntry | None:
        resolved_now = now or utc_now()
        redis_key = self._key(tenant_id, run_id, key)
        client = self._client()
        raw = client.get(redis_key)
        if raw is None:
            return None
        entry = ShortTermMemoryEntry.model_validate_json(self._decode(raw))
        if entry.is_expired(resolved_now):
            client.delete(redis_key)
            return None
        return entry

    def list_for_run(
        self,
        tenant_id: str,
        run_id: str,
        now: datetime | None = None,
    ) -> list[ShortTermMemoryEntry]:
        resolved_now = now or utc_now()
        client = self._client()
        entries: list[ShortTermMemoryEntry] = []
        for redis_key in client.scan_iter(match=f"{self._prefix(tenant_id, run_id)}*"):
            raw = client.get(self._decode(redis_key))
            if raw is None:
                continue
            entry = ShortTermMemoryEntry.model_validate_json(self._decode(raw))
            if entry.is_expired(resolved_now):
                client.delete(self._decode(redis_key))
                continue
            entries.append(entry)
        return sorted(entries, key=lambda entry: entry.key)

    def delete(self, tenant_id: str, run_id: str, key: str) -> bool:
        redis_key = self._key(tenant_id, run_id, key)
        client = self._client()
        if client.get(redis_key) is None:
            return False
        client.delete(redis_key)
        return True

    def delete_for_tenant(self, tenant_id: str) -> int:
        client = self._client()
        redis_keys = [
            self._decode(redis_key)
            for redis_key in client.scan_iter(match=f"{self._tenant_prefix(tenant_id)}*")
        ]
        for redis_key in redis_keys:
            client.delete(redis_key)
        return len(redis_keys)

    def _tenant_prefix(self, tenant_id: str) -> str:
        return f"{self.key_prefix}:tenant:{tenant_id}:"

    def _prefix(self, tenant_id: str, run_id: str) -> str:
        return f"{self.key_prefix}:tenant:{tenant_id}:run:{run_id}:key:"

    def _client(self):
        if self.client is not None:
            return self.client
        try:
            import redis
        except ImportError as error:
            raise RedisMemoryConfigurationError(
                "redis package is required for RedisShortTermMemoryService"
            ) from error
        client = redis.Redis.from_url(self.url, decode_responses=True)
        object.__setattr__(self, "client", client)
        return client

    def _key(self, tenant_id: str, run_id: str, key: str) -> str:
        return f"{self._prefix(tenant_id, run_id)}{key}"

    def _decode(self, value: str | bytes) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return value


InMemoryMemoryService = InMemoryLongTermMemoryService


class InMemoryShortTermMemoryReviewStore(BaseModel):
    reviews: dict[str, ShortTermMemoryReview] = Field(default_factory=dict)

    def save_review(self, review: ShortTermMemoryReview) -> ShortTermMemoryReview:
        self.reviews[review.id] = review
        return review

    def get_review(self, tenant_id: str, review_id: str) -> ShortTermMemoryReview:
        review = self.reviews.get(review_id)
        if review is None:
            raise NotFoundError(f"Short-term memory review not found: {review_id}")
        if review.tenant_id != tenant_id:
            raise TenantAccessError(
                f"Short-term memory review {review_id} is not in tenant {tenant_id}"
            )
        return review

    def list_reviews(
        self,
        tenant_id: str | None = None,
        run_id: str | None = None,
        status: ShortTermMemoryReviewStatus | None = None,
    ) -> list[ShortTermMemoryReview]:
        reviews = [
            review
            for review in self.reviews.values()
            if (tenant_id is None or review.tenant_id == tenant_id)
            and (run_id is None or review.run_id == run_id)
            and (status is None or review.status == status)
        ]
        return sorted(reviews, key=lambda review: (review.created_at, review.id))

    def delete_for_tenant(self, tenant_id: str) -> int:
        review_ids = [
            review_id
            for review_id, review in self.reviews.items()
            if review.tenant_id == tenant_id
        ]
        for review_id in review_ids:
            del self.reviews[review_id]
        return len(review_ids)


class GuardedLongTermMemoryService(BaseModel):
    service: Any
    guardrail_service: Any | None = None
    audit_service: Any | None = None

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def write(self, request: MemoryWriteRequest) -> MemoryRecord:
        return self.service.write(self._apply_memory_write_guardrails(request))

    def propose_candidate(self, request: MemoryWriteRequest) -> MemoryRecord:
        candidate_request = request.model_copy(update={"status": MemoryStatus.CANDIDATE})
        return self.service.propose_candidate(
            self._apply_memory_write_guardrails(
                candidate_request,
                hold_approval_as_candidate=True,
            )
        )

    def get(self, tenant_id: str, memory_id: str) -> MemoryRecord:
        return self.service.get(tenant_id, memory_id)

    def approve(
        self,
        tenant_id: str,
        memory_id: str,
        reviewed_by_user_id: str,
    ) -> MemoryRecord:
        return self.service.approve(tenant_id, memory_id, reviewed_by_user_id)

    def reject(
        self,
        tenant_id: str,
        memory_id: str,
        reviewed_by_user_id: str,
    ) -> MemoryRecord:
        return self.service.reject(tenant_id, memory_id, reviewed_by_user_id)

    def list_by_scope(
        self,
        tenant_id: str,
        scope_type: MemoryScopeType,
        scope_id: str,
    ) -> list[MemoryRecord]:
        return self.service.list_by_scope(tenant_id, scope_type, scope_id)

    def forget(self, tenant_id: str, memory_id: str) -> MemoryRecord:
        return self.service.forget(tenant_id, memory_id)

    def delete_for_tenant(self, tenant_id: str) -> list[str]:
        return self.service.delete_for_tenant(tenant_id)

    def _apply_memory_write_guardrails(
        self,
        request: MemoryWriteRequest,
        hold_approval_as_candidate: bool = False,
    ) -> MemoryWriteRequest:
        if self.guardrail_service is None:
            return request
        decision = self.guardrail_service.evaluate(
            GuardrailEvaluationRequest(
                tenant_id=request.tenant_id,
                workspace_id=request.workspace_id,
                stage=GuardrailStage.MEMORY_WRITE,
                content=request.content,
                attributes={
                    "memory_kind": "long_term",
                    "scope_type": request.scope_type.value,
                    "scope_id": request.scope_id,
                    "source_run_id": request.source_run_id,
                    "status": request.status.value,
                    "sensitivity_level": request.sensitivity_level,
                    "content_length": len(request.content),
                    "metadata_keys": sorted(request.metadata.keys()),
                },
            )
        )
        if decision.blocked:
            metadata = self._record_guardrail_audit(
                request,
                decision,
                "guardrail.memory_write_blocked",
                "long_term",
            )
            raise MemoryWriteRejectedError("memory write rejected by guardrail", metadata)
        if decision.approval_required:
            metadata = self._record_guardrail_audit(
                request,
                decision,
                "guardrail.memory_write_approval_required",
                "long_term",
            )
            if hold_approval_as_candidate:
                return request.model_copy(
                    update={
                        "status": MemoryStatus.CANDIDATE,
                        "metadata": {
                            **request.metadata,
                            "guardrail_approval_required": True,
                            "guardrail_action": decision.action.value,
                            "guardrail_rule_ids": decision.matched_rule_ids,
                            "guardrail_detector_finding_ids": decision.detector_finding_ids,
                            "guardrail_severity": (
                                decision.severity.value
                                if decision.severity is not None
                                else None
                            ),
                            "guardrail_review_event_type": "guardrail.memory_write_approval_required",
                        },
                    }
                )
            raise MemoryWriteRejectedError("memory write requires guardrail approval", metadata)
        if decision.action == GuardrailAction.REDACT and decision.redactions:
            self._record_guardrail_audit(
                request,
                decision,
                "guardrail.memory_write_redacted",
                "long_term",
            )
            return request.model_copy(
                update={"content": self._apply_guardrail_redactions(request.content, decision)}
            )
        if decision.audit_required and decision.warnings:
            self._record_guardrail_audit(
                request,
                decision,
                "guardrail.memory_write_warned",
                "long_term",
            )
        return request

    def _record_guardrail_audit(
        self,
        request: MemoryWriteRequest,
        decision: GuardrailDecision,
        event_type: str,
        memory_kind: str,
    ) -> dict[str, Any]:
        metadata = {
            "guardrail_action": decision.action.value,
            "guardrail_rule_ids": decision.matched_rule_ids,
            "guardrail_detector_finding_ids": decision.detector_finding_ids,
            "severity": decision.severity.value if decision.severity is not None else None,
            "message": decision.message,
            "memory_kind": memory_kind,
            "scope_type": request.scope_type.value,
            "scope_id": request.scope_id,
            "source_run_id": request.source_run_id,
            "status": request.status.value,
            "sensitivity_level": request.sensitivity_level,
            "content_length": len(request.content),
            "metadata_keys": sorted(request.metadata.keys()),
        }
        if self.audit_service is not None:
            self.audit_service.record(
                AuditEventCreate(
                    tenant_id=request.tenant_id,
                    workspace_id=request.workspace_id,
                    user_id=request.created_by,
                    run_id=None,
                    event_type=event_type,
                    metadata=metadata,
                    actor=AuditActor(
                        tenant_id=request.tenant_id,
                        user_id=request.created_by,
                    ),
                )
            )
        return metadata

    def _apply_guardrail_redactions(self, value: str, decision: GuardrailDecision) -> str:
        redacted = value
        for redaction in decision.redactions:
            flags = 0 if redaction.case_sensitive else re.IGNORECASE
            redacted = re.sub(
                re.escape(redaction.text),
                redaction.replacement,
                redacted,
                flags=flags,
            )
        return redacted


class GuardedShortTermMemoryService(BaseModel):
    service: Any
    guardrail_service: Any | None = None
    audit_service: Any | None = None
    review_store: Any = Field(default_factory=InMemoryShortTermMemoryReviewStore)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def put(
        self,
        request: ShortTermMemoryWrite,
        now: datetime | None = None,
    ) -> ShortTermMemoryEntry | ShortTermMemoryReview:
        resolved_now = now or utc_now()
        guarded = self._apply_memory_write_guardrails(request, resolved_now)
        if isinstance(guarded, ShortTermMemoryReview):
            return self.review_store.save_review(guarded)
        return self.service.put(guarded, resolved_now)

    def get(
        self,
        tenant_id: str,
        run_id: str,
        key: str,
        now: datetime | None = None,
    ) -> ShortTermMemoryEntry | None:
        return self.service.get(tenant_id, run_id, key, now)

    def list_for_run(
        self,
        tenant_id: str,
        run_id: str,
        now: datetime | None = None,
    ) -> list[ShortTermMemoryEntry]:
        return self.service.list_for_run(tenant_id, run_id, now)

    def delete(self, tenant_id: str, run_id: str, key: str) -> bool:
        return self.service.delete(tenant_id, run_id, key)

    def delete_for_tenant(self, tenant_id: str) -> int:
        deleted_count = self.service.delete_for_tenant(tenant_id)
        if hasattr(self.review_store, "delete_for_tenant"):
            deleted_count += self.review_store.delete_for_tenant(tenant_id)
        return deleted_count

    def list_reviews(
        self,
        tenant_id: str,
        run_id: str | None = None,
        status: ShortTermMemoryReviewStatus | None = None,
        now: datetime | None = None,
    ) -> list[ShortTermMemoryReview]:
        resolved_now = now or utc_now()
        self._expire_pending_reviews(resolved_now)
        return self.review_store.list_reviews(
            tenant_id,
            run_id=run_id,
            status=status,
        )

    def approve_review(
        self,
        tenant_id: str,
        review_id: str,
        reviewed_by_user_id: str,
        now: datetime | None = None,
    ) -> ShortTermMemoryReview:
        resolved_now = now or utc_now()
        review = self._get_review(tenant_id, review_id, resolved_now)
        self._ensure_pending_review(review)
        entry = self.service.put(
            ShortTermMemoryWrite(
                tenant_id=review.tenant_id,
                workspace_id=review.workspace_id,
                run_id=review.run_id,
                key=review.key,
                value=review.value,
                ttl_seconds=review.ttl_seconds,
                created_by=review.created_by,
            ),
            resolved_now,
        )
        updated = review.model_copy(
            update={
                "status": ShortTermMemoryReviewStatus.APPROVED,
                "approved_by_user_id": reviewed_by_user_id,
                "approved_at": resolved_now,
                "activated_entry_expires_at": entry.expires_at,
            }
        )
        return self.review_store.save_review(updated)

    def reject_review(
        self,
        tenant_id: str,
        review_id: str,
        reviewed_by_user_id: str,
        now: datetime | None = None,
    ) -> ShortTermMemoryReview:
        resolved_now = now or utc_now()
        review = self._get_review(tenant_id, review_id, resolved_now)
        self._ensure_pending_review(review)
        updated = review.model_copy(
            update={
                "status": ShortTermMemoryReviewStatus.REJECTED,
                "rejected_by_user_id": reviewed_by_user_id,
                "rejected_at": resolved_now,
            }
        )
        return self.review_store.save_review(updated)

    def _apply_memory_write_guardrails(
        self,
        request: ShortTermMemoryWrite,
        now: datetime,
    ) -> ShortTermMemoryWrite | ShortTermMemoryReview:
        if self.guardrail_service is None:
            return request
        content = json.dumps({"key": request.key, "value": request.value}, sort_keys=True)
        decision = self.guardrail_service.evaluate(
            GuardrailEvaluationRequest(
                tenant_id=request.tenant_id,
                workspace_id=request.workspace_id,
                stage=GuardrailStage.MEMORY_WRITE,
                content=content,
                attributes={
                    "memory_kind": "short_term",
                    "run_id": request.run_id,
                    "key_length": len(request.key),
                    "ttl_seconds": request.ttl_seconds,
                    "value_keys": sorted(request.value.keys()),
                },
            )
        )
        if decision.blocked:
            metadata = self._record_guardrail_audit(
                request,
                decision,
                "guardrail.memory_write_blocked",
                "short_term",
            )
            raise MemoryWriteRejectedError("memory write rejected by guardrail", metadata)
        if decision.approval_required:
            metadata = self._record_guardrail_audit(
                request,
                decision,
                "guardrail.memory_write_approval_required",
                "short_term",
            )
            return ShortTermMemoryReview.from_write(
                request,
                now,
                guardrail_metadata=metadata,
            )
        if decision.action == GuardrailAction.REDACT and decision.redactions:
            self._record_guardrail_audit(
                request,
                decision,
                "guardrail.memory_write_redacted",
                "short_term",
            )
            return request.model_copy(
                update={
                    "key": self._apply_guardrail_redactions(request.key, decision),
                    "value": self._redact_guarded_value(request.value, decision),
                }
            )
        if decision.audit_required and decision.warnings:
            self._record_guardrail_audit(
                request,
                decision,
                "guardrail.memory_write_warned",
                "short_term",
            )
        return request

    def _record_guardrail_audit(
        self,
        request: ShortTermMemoryWrite,
        decision: GuardrailDecision,
        event_type: str,
        memory_kind: str,
    ) -> dict[str, Any]:
        metadata = {
            "guardrail_action": decision.action.value,
            "guardrail_rule_ids": decision.matched_rule_ids,
            "guardrail_detector_finding_ids": decision.detector_finding_ids,
            "severity": decision.severity.value if decision.severity is not None else None,
            "message": decision.message,
            "memory_kind": memory_kind,
            "run_id": request.run_id,
            "key_length": len(request.key),
            "ttl_seconds": request.ttl_seconds,
            "value_keys": sorted(request.value.keys()),
        }
        if self.audit_service is not None:
            self.audit_service.record(
                AuditEventCreate(
                    tenant_id=request.tenant_id,
                    workspace_id=request.workspace_id,
                    user_id=request.created_by,
                    run_id=None,
                    event_type=event_type,
                    metadata=metadata,
                    actor=AuditActor(
                        tenant_id=request.tenant_id,
                        user_id=request.created_by,
                        actor_type="user" if request.created_by is not None else "system",
                    ),
                )
            )
        return metadata

    def _get_review(
        self,
        tenant_id: str,
        review_id: str,
        now: datetime,
    ) -> ShortTermMemoryReview:
        self._expire_pending_reviews(now)
        return self.review_store.get_review(tenant_id, review_id)

    def _ensure_pending_review(self, review: ShortTermMemoryReview) -> None:
        if review.status != ShortTermMemoryReviewStatus.PENDING:
            raise ValueError("short-term memory review is not pending")

    def _expire_pending_reviews(self, now: datetime) -> None:
        for review in self.review_store.list_reviews(
            tenant_id=None,
            status=ShortTermMemoryReviewStatus.PENDING,
        ):
            if not review.is_expired(now):
                continue
            self.review_store.save_review(
                review.model_copy(update={"status": ShortTermMemoryReviewStatus.EXPIRED})
            )

    def _redact_guarded_value(self, value, decision: GuardrailDecision):
        if isinstance(value, str):
            return self._apply_guardrail_redactions(value, decision)
        if isinstance(value, dict):
            return {
                key: self._redact_guarded_value(item, decision)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._redact_guarded_value(item, decision) for item in value]
        return value

    def _apply_guardrail_redactions(self, value: str, decision: GuardrailDecision) -> str:
        redacted = value
        for redaction in decision.redactions:
            flags = 0 if redaction.case_sensitive else re.IGNORECASE
            redacted = re.sub(
                re.escape(redaction.text),
                redaction.replacement,
                redacted,
                flags=flags,
            )
        return redacted
