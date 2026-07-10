import hashlib
import json
from typing import Any, Protocol

from pydantic import BaseModel, Field

from taroai.domain import IdempotencyRecord, utc_now


class IdempotencyConflictError(ValueError):
    """Raised when one idempotency key is reused for a different request."""


class IdempotencyRequest(BaseModel):
    tenant_id: str = Field(min_length=1)
    key: str = Field(min_length=1)
    method: str = Field(min_length=1)
    path: str = Field(min_length=1)
    request_hash: str = Field(min_length=1)


class IdempotencyStore(Protocol):
    def get_idempotency_record(
        self,
        tenant_id: str,
        key: str,
        method: str,
        path: str,
    ) -> IdempotencyRecord | None:
        ...

    def save_idempotency_record(self, record: IdempotencyRecord) -> IdempotencyRecord:
        ...


def normalize_idempotency_key(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if normalized == "":
        return None
    return normalized


def canonical_request_hash(payload: BaseModel | dict[str, Any]) -> str:
    if isinstance(payload, BaseModel):
        request_body = payload.model_dump(mode="json")
    else:
        request_body = payload
    encoded = json.dumps(
        request_body,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_idempotency_request(
    tenant_id: str,
    key: str | None,
    method: str,
    path: str,
    payload: BaseModel | dict[str, Any],
) -> IdempotencyRequest | None:
    normalized_key = normalize_idempotency_key(key)
    if normalized_key is None:
        return None
    return IdempotencyRequest(
        tenant_id=tenant_id,
        key=normalized_key,
        method=method,
        path=path,
        request_hash=canonical_request_hash(payload),
    )


def require_matching_idempotency_record(
    record: IdempotencyRecord,
    request: IdempotencyRequest,
) -> IdempotencyRecord:
    if record.request_hash != request.request_hash:
        raise IdempotencyConflictError(
            "Idempotency-Key reused with a different request body"
        )
    return record


def find_idempotent_replay(
    store: IdempotencyStore,
    request: IdempotencyRequest | None,
) -> IdempotencyRecord | None:
    if request is None:
        return None
    record = store.get_idempotency_record(
        tenant_id=request.tenant_id,
        key=request.key,
        method=request.method,
        path=request.path,
    )
    if record is None:
        return None
    return require_matching_idempotency_record(record, request)


def build_idempotency_record(
    request: IdempotencyRequest,
    status_code: int,
    response_body: dict[str, Any],
) -> IdempotencyRecord:
    return IdempotencyRecord(
        tenant_id=request.tenant_id,
        key=request.key,
        method=request.method,
        path=request.path,
        request_hash=request.request_hash,
        status_code=status_code,
        response_body=response_body,
        created_at=utc_now(),
    )


def save_idempotent_response(
    store: IdempotencyStore,
    request: IdempotencyRequest | None,
    status_code: int,
    response_body: dict[str, Any],
) -> None:
    if request is None:
        return
    store.save_idempotency_record(
        build_idempotency_record(
            request=request,
            status_code=status_code,
            response_body=response_body,
        )
    )
