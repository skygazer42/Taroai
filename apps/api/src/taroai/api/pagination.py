import base64
import json
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SortDirection(str, Enum):
    ASC = "asc"
    DESC = "desc"


class PageRequest(BaseModel):
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = None
    sort_direction: SortDirection = SortDirection.DESC


class PageCursor(BaseModel):
    created_at: datetime
    id: str = Field(min_length=1)


class PageResult(BaseModel):
    items: list[dict[str, Any]] = Field(default_factory=list)
    limit: int
    next_cursor: str | None = None
    has_more: bool = False


class InvalidPageCursorError(ValueError):
    """Raised when a pagination cursor cannot be decoded."""


def paginate_created_at_records(records: list[Any], request: PageRequest) -> PageResult:
    cursor = decode_page_cursor(request.cursor) if request.cursor is not None else None
    sorted_records = sorted(
        records,
        key=lambda record: (record.created_at, record_page_id(record)),
        reverse=request.sort_direction == SortDirection.DESC,
    )
    page_candidates = [
        record
        for record in sorted_records
        if cursor is None or record_is_after_cursor(record, cursor, request.sort_direction)
    ]
    visible_records = page_candidates[: request.limit + 1]
    page_records = visible_records[: request.limit]
    has_more = len(visible_records) > request.limit
    next_cursor = encode_page_cursor(page_records[-1]) if has_more and page_records else None
    return PageResult(
        items=[record.model_dump(mode="json") for record in page_records],
        limit=request.limit,
        next_cursor=next_cursor,
        has_more=has_more,
    )


def record_is_after_cursor(record: Any, cursor: PageCursor, direction: SortDirection) -> bool:
    record_key = (record.created_at, record_page_id(record))
    cursor_key = (cursor.created_at, cursor.id)
    if direction == SortDirection.ASC:
        return record_key > cursor_key
    return record_key < cursor_key


def encode_page_cursor(record: Any) -> str:
    payload = PageCursor(
        created_at=record.created_at,
        id=record_page_id(record),
    ).model_dump(mode="json")
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    return encoded.rstrip("=")


def decode_page_cursor(value: str) -> PageCursor:
    try:
        padding = "=" * (-len(value) % 4)
        payload = base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))
        return PageCursor(**json.loads(payload.decode("utf-8")))
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        raise InvalidPageCursorError("Invalid pagination cursor") from error


def record_page_id(record: Any) -> str:
    record_id = getattr(record, "id", None)
    if record_id is not None:
        return str(record_id)
    key = getattr(record, "key", None)
    if key is not None:
        return str(key)
    manifest = getattr(record, "manifest", None)
    manifest_id = getattr(manifest, "id", None)
    if manifest_id is not None:
        return str(manifest_id)
    raise InvalidPageCursorError("Record cannot be paginated without an id or key")
