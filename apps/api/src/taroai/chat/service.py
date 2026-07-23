import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from threading import Lock, RLock
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

from taroai.api.idempotency import (
    IdempotencyConflictError,
    IdempotencyRequest,
    build_idempotency_record,
    find_idempotent_replay,
    save_idempotent_response,
)
from taroai.domain import (
    ChatMessage,
    ChatMessageCreate,
    ChatMessageDispatchStatus,
    ChatThread,
    ChatThreadCreate,
    ChatThreadStatus,
    ResourceReference,
    Run,
    RunCreate,
    RunMode,
    IdempotencyRecord,
    utc_now,
)
from taroai.model_gateway import (
    ModelCatalogEntry,
    ModelGatewayRequest,
    ModelMessage,
    ModelPolicy,
    ModelPolicyDeniedError,
    ModelProviderRegistry,
    ReasoningEffort,
)
from taroai.store import NotFoundError, TenantAccessError


_MESSAGE_KIND_BY_RUN_MODE = {
    RunMode.CHAT: "text",
    RunMode.AUTONOMOUS: "agent",
    RunMode.WORKFLOW: "workflow",
}
_RUN_MODE_BY_MESSAGE_KIND = {
    kind: mode for mode, kind in _MESSAGE_KIND_BY_RUN_MODE.items()
}


class ChatThreadApiCreate(BaseModel):
    workspace_id: str = Field(min_length=1)
    title: str = ""
    provider_id: str | None = None
    model_id: str | None = None
    reasoning_effort: ReasoningEffort | None = None

    model_config = ConfigDict(extra="forbid")


class ChatThreadPatch(BaseModel):
    title: str | None = None
    pinned: bool | None = None
    status: Literal["active", "archived"] | None = None
    provider_id: str | None = None
    model_id: str | None = None
    reasoning_effort: ReasoningEffort | None = None

    model_config = ConfigDict(extra="forbid")


class ChatMessageSubmit(BaseModel):
    content: str = Field(min_length=1)
    display_content: str | None = Field(default=None, min_length=1)
    timezone: str | None = Field(default=None, min_length=1, max_length=128)
    skill_ids: list[str] = Field(default_factory=list)
    delivery_mode: Literal["auto", "queue", "manual", "steer"] = "auto"
    mode: RunMode = RunMode.CHAT
    attachments: list[str] = Field(default_factory=list)
    resource_refs: list[ResourceReference] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as error:
            raise ValueError(f"unknown timezone: {value}") from error
        return value


class ChatMessageEdit(BaseModel):
    content: str | None = Field(default=None, min_length=1)
    attachments: list[str] | None = None
    resource_refs: list[ResourceReference] | None = None

    model_config = ConfigDict(extra="forbid")


class ChatSteerSubmit(BaseModel):
    content: str = Field(min_length=1)
    attachments: list[str] = Field(default_factory=list)
    resource_refs: list[ResourceReference] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class MessageDispatch(BaseModel):
    message_id: str
    run_id: str
    dispatch_status: ChatMessageDispatchStatus
    events_url: str
    run_started: bool = Field(default=False, exclude=True)


class ModelSelection(BaseModel):
    provider_id: str
    model_id: str
    reasoning_effort: ReasoningEffort


@dataclass
class _NamedLockEntry:
    lock: RLock = field(default_factory=RLock)
    users: int = 0


class ChatService:
    _PENDING_MARKER = "_taroai_idempotency_pending"
    _IDEMPOTENCY_RESERVATION_TTL_SECONDS = 30

    def __init__(
        self,
        *,
        store: Any,
        model_policy_resolver: Callable[[], ModelPolicy],
        provider_registry_resolver: Callable[[], ModelProviderRegistry],
        steering_available_resolver: Callable[[], bool] | None = None,
    ) -> None:
        self.store = store
        self._model_policy_resolver = model_policy_resolver
        self._provider_registry_resolver = provider_registry_resolver
        self._steering_available_resolver = (
            steering_available_resolver or (lambda: True)
        )
        self._lock_guard = Lock()
        self._locks: dict[str, _NamedLockEntry] = {}

    def create_thread(
        self,
        tenant_id: str,
        user_id: str,
        payload: ChatThreadApiCreate,
    ) -> ChatThread:
        selection = self.resolve_selection(
            tenant_id=tenant_id,
            workspace_id=payload.workspace_id,
            user_id=user_id,
            provider_id=payload.provider_id,
            model_id=payload.model_id,
            reasoning_effort=payload.reasoning_effort,
        )
        return self.store.create_chat_thread(
            tenant_id,
            user_id,
            ChatThreadCreate(
                workspace_id=payload.workspace_id,
                title=payload.title,
                provider_id=selection.provider_id,
                model_id=selection.model_id,
                reasoning_effort=selection.reasoning_effort,
            ),
        )

    def get_thread(self, tenant_id: str, thread_id: str) -> ChatThread:
        try:
            thread = self.store.get_chat_thread(tenant_id, thread_id)
        except TenantAccessError as error:
            raise NotFoundError(f"Chat thread not found: {thread_id}") from error
        if thread.status == ChatThreadStatus.DELETED:
            raise NotFoundError(f"Chat thread not found: {thread_id}")
        return thread

    def list_threads(
        self,
        tenant_id: str,
        workspace_id: str | None = None,
        *,
        include_archived: bool = False,
    ) -> list[ChatThread]:
        return [
            thread
            for thread in self.store.list_chat_threads(tenant_id, workspace_id)
            if thread.status != ChatThreadStatus.DELETED
            and (include_archived or thread.status != ChatThreadStatus.ARCHIVED)
        ]

    def update_thread(
        self,
        tenant_id: str,
        user_id: str,
        thread_id: str,
        payload: ChatThreadPatch,
    ) -> ChatThread:
        thread = self.get_thread(tenant_id, thread_id)
        changes = payload.model_dump(exclude_unset=True)
        selection = self.resolve_selection(
            tenant_id=tenant_id,
            workspace_id=thread.workspace_id,
            user_id=user_id,
            provider_id=changes.get("provider_id", thread.provider_id),
            model_id=changes.get("model_id", thread.model_id),
            reasoning_effort=changes.get(
                "reasoning_effort", thread.reasoning_effort
            ),
        )
        changes.update(
            provider_id=selection.provider_id,
            model_id=selection.model_id,
            reasoning_effort=selection.reasoning_effort,
        )
        return self.store.update_chat_thread(tenant_id, thread_id, **changes)

    def delete_thread(self, tenant_id: str, thread_id: str) -> ChatThread:
        self.get_thread(tenant_id, thread_id)
        return self.store.update_chat_thread(
            tenant_id,
            thread_id,
            status=ChatThreadStatus.DELETED,
        )

    def list_messages(self, tenant_id: str, thread_id: str) -> list[ChatMessage]:
        self.get_thread(tenant_id, thread_id)
        return self.store.list_chat_messages(tenant_id, thread_id)

    def edit_message(
        self,
        tenant_id: str,
        user_id: str,
        thread_id: str,
        message_id: str,
        payload: ChatMessageEdit,
    ) -> ChatMessage:
        self.get_thread(tenant_id, thread_id)
        message = self.store.get_chat_message(tenant_id, message_id)
        if message.thread_id != thread_id:
            raise NotFoundError(f"Chat message not found: {message_id}")
        if message.created_by_user_id != user_id:
            raise TenantAccessError("Only the author can edit a chat message")
        if message.dispatch_status not in {
            ChatMessageDispatchStatus.READY,
            ChatMessageDispatchStatus.QUEUED,
            ChatMessageDispatchStatus.STEERING,
        }:
            raise ValueError("Only queued or pending steering messages can be edited")
        changes = payload.model_dump(exclude_unset=True)
        if "content" in changes:
            changes["execution_content"] = changes["content"]
        return self.store.update_chat_message(tenant_id, message_id, **changes)

    def delete_message(
        self,
        tenant_id: str,
        user_id: str,
        thread_id: str,
        message_id: str,
    ) -> None:
        self.get_thread(tenant_id, thread_id)
        message = self.store.get_chat_message(tenant_id, message_id)
        if message.thread_id != thread_id:
            raise NotFoundError(f"Chat message not found: {message_id}")
        if message.created_by_user_id != user_id:
            raise TenantAccessError("Only the author can delete a chat message")
        if message.dispatch_status not in {
            ChatMessageDispatchStatus.READY,
            ChatMessageDispatchStatus.QUEUED,
            ChatMessageDispatchStatus.STEERING,
            ChatMessageDispatchStatus.CANCELLED,
        }:
            raise ValueError("An inflight or completed message cannot be deleted")
        self.store.delete_chat_message(tenant_id, message_id)

    def promote_manual_message(
        self,
        tenant_id: str,
        user_id: str,
        thread_id: str,
        message_id: str,
    ) -> ChatMessage:
        self.get_thread(tenant_id, thread_id)
        message = self.store.get_chat_message(tenant_id, message_id)
        if message.thread_id != thread_id or message.kind != "manual_queue":
            raise NotFoundError(f"Manual queued message not found: {message_id}")
        if message.created_by_user_id != user_id:
            raise TenantAccessError("Only the author can promote a queued message")
        if message.dispatch_status != ChatMessageDispatchStatus.READY:
            raise ValueError("Only a pending manual message can be promoted")
        self.store.delete_chat_message(tenant_id, message_id)
        return message

    def steer(
        self,
        tenant_id: str,
        user_id: str,
        thread_id: str,
        payload: ChatSteerSubmit,
    ) -> MessageDispatch:
        return self.post_message(
            tenant_id,
            user_id,
            thread_id,
            ChatMessageSubmit(
                content=payload.content,
                delivery_mode="steer",
                attachments=payload.attachments,
                resource_refs=payload.resource_refs,
            ),
        )

    def continue_thread(
        self,
        tenant_id: str,
        thread_id: str,
    ) -> MessageDispatch | None:
        with self._named_lock(f"thread:{tenant_id}:{thread_id}"):
            thread = self.get_thread(tenant_id, thread_id)
            if thread.status != ChatThreadStatus.ACTIVE:
                return None
            if self.store.get_active_thread_run(tenant_id, thread_id) is not None:
                return None
            for steering in self.store.list_pending_steering_messages(
                tenant_id, thread_id
            ):
                self.store.update_chat_message(
                    tenant_id,
                    steering.id,
                    dispatch_status=ChatMessageDispatchStatus.QUEUED,
                )
            message = self.store.claim_next_queued_message(tenant_id, thread_id)
            if message is None:
                return None
            try:
                selection = self.resolve_selection(
                    tenant_id=tenant_id,
                    workspace_id=thread.workspace_id,
                    user_id=message.created_by_user_id or thread.created_by_user_id,
                    provider_id=thread.provider_id,
                    model_id=thread.model_id,
                    reasoning_effort=thread.reasoning_effort,
                )
                run, started = self.store.create_queued_thread_run_if_absent(
                    tenant_id,
                    message.created_by_user_id or thread.created_by_user_id,
                    RunCreate(
                        workspace_id=thread.workspace_id,
                        message=message.execution_content or message.content,
                        attachments=message.attachments,
                        mode=_RUN_MODE_BY_MESSAGE_KIND.get(message.kind, RunMode.CHAT),
                        thread_id=thread.id,
                        trigger_message_id=message.id,
                        provider_id=selection.provider_id,
                        model_id=selection.model_id,
                        reasoning_effort=selection.reasoning_effort,
                        resource_refs=message.resource_refs,
                    ),
                )
            except Exception:
                self.store.update_chat_message(
                    tenant_id,
                    message.id,
                    dispatch_status=ChatMessageDispatchStatus.QUEUED,
                )
                raise
            if not started:
                self.store.update_chat_message(
                    tenant_id,
                    message.id,
                    dispatch_status=ChatMessageDispatchStatus.QUEUED,
                )
                return None
            return self._dispatch_response(message, run, run_started=True)

    def post_message(
        self,
        tenant_id: str,
        user_id: str,
        thread_id: str,
        payload: ChatMessageSubmit,
    ) -> MessageDispatch:
        with self._named_lock(f"thread:{tenant_id}:{thread_id}"):
            thread = self.get_thread(tenant_id, thread_id)
            if thread.status != ChatThreadStatus.ACTIVE:
                raise ValueError("messages cannot be posted to an archived thread")
            if payload.timezone is not None:
                local_datetime = utc_now().astimezone(
                    ZoneInfo(payload.timezone)
                ).isoformat()
                payload = payload.model_copy(
                    update={
                        "display_content": payload.display_content or payload.content,
                        "content": (
                            "[Platform context: "
                            f"user_timezone={payload.timezone}; "
                            f"current_local_datetime={local_datetime}]\n\n"
                            f"{payload.content}"
                        ),
                    }
                )
            selection = self.resolve_selection(
                tenant_id=tenant_id,
                workspace_id=thread.workspace_id,
                user_id=user_id,
                provider_id=thread.provider_id,
                model_id=thread.model_id,
                reasoning_effort=thread.reasoning_effort,
            )
            active_run = self.store.get_active_thread_run(tenant_id, thread_id)
            if active_run is not None:
                return self._append_to_active_run(
                    tenant_id,
                    user_id,
                    thread,
                    active_run,
                    payload,
                )
            return self._start_run(
                tenant_id,
                user_id,
                thread,
                selection,
                payload,
            )

    def execute_idempotently(
        self,
        request: IdempotencyRequest | None,
        operation: Callable[[], MessageDispatch],
    ) -> tuple[int, dict[str, Any]]:
        if request is None:
            result = operation()
            return 202, result.model_dump(mode="json")
        lock_name = (
            f"idempotency:{request.tenant_id}:{request.method}:"
            f"{request.path}:{request.key}"
        )
        with self._named_lock(lock_name):
            replay = find_idempotent_replay(self.store, request)
            if replay is not None and not self._is_pending(replay.response_body):
                return replay.status_code, replay.response_body
            if replay is None:
                reservation = build_idempotency_record(
                    request,
                    102,
                    {self._PENDING_MARKER: True},
                )
                if not self.store.reserve_idempotency_record(reservation):
                    replay = self._wait_for_idempotent_completion(request)
                    return replay.status_code, replay.response_body
            elif self._is_pending(replay.response_body):
                replacement = build_idempotency_record(
                    request,
                    102,
                    {self._PENDING_MARKER: True},
                )
                stale_before = utc_now() - timedelta(
                    seconds=self._IDEMPOTENCY_RESERVATION_TTL_SECONDS
                )
                reclaimed = self.store.reclaim_stale_idempotency_record(
                    replacement,
                    stale_before,
                )
                if not reclaimed:
                    replay = self._wait_for_idempotent_completion(request)
                    return replay.status_code, replay.response_body
            try:
                result = operation()
            except Exception:
                self.store.delete_idempotency_record(
                    request.tenant_id,
                    request.key,
                    request.method,
                    request.path,
                    request.request_hash,
                )
                raise
            response_body = result.model_dump(mode="json")
            save_idempotent_response(self.store, request, 202, response_body)
            return 202, response_body

    def model_catalog(
        self,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
    ) -> list[ModelCatalogEntry]:
        entries: dict[tuple[str, str], ModelCatalogEntry] = {}
        registry = self._provider_registry_resolver()
        policy = self._model_policy_resolver()
        default_request = self._selection_request(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            provider_id=None,
            model_id=None,
            reasoning_effort=None,
        )
        default_model = policy.resolve_model(default_request)
        provider_order = {
            provider.id: index
            for index, provider in enumerate(registry.candidates(default_request))
        }
        for provider in registry.providers:
            for model_id in provider.catalog_model_ids():
                request = self._selection_request(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    provider_id=provider.id,
                    model_id=model_id,
                    reasoning_effort=None,
                )
                if not provider.matches(request):
                    continue
                try:
                    policy.assert_request_allowed(request)
                except ModelPolicyDeniedError:
                    continue
                entries[(provider.id, model_id)] = ModelCatalogEntry(
                    provider_id=provider.id,
                    model_id=model_id,
                    display_name=f"{provider.display_name or provider.id} / {model_id}",
                    reasoning_efforts=provider.reasoning_efforts,
                    default_reasoning_effort=provider.default_reasoning_effort,
                    configured=bool(
                        provider.api_key or provider.api_key_secret_ref_id
                    ),
                )
        return sorted(
            entries.values(),
            key=lambda entry: (
                entry.model_id != default_model,
                provider_order.get(entry.provider_id, len(provider_order)),
                entry.provider_id,
                entry.model_id,
            ),
        )

    def resolve_selection(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        provider_id: str | None,
        model_id: str | None,
        reasoning_effort: str | None,
    ) -> ModelSelection:
        request = self._selection_request(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            provider_id=provider_id,
            model_id=model_id,
            reasoning_effort=reasoning_effort,
        )
        policy = self._model_policy_resolver()
        resolved_model = policy.assert_request_allowed(request)
        request = request.model_copy(update={"model": resolved_model})
        registry = self._provider_registry_resolver()
        candidates = registry.candidates(request)
        if not candidates and resolved_model is None:
            candidates = registry.candidates(request.model_copy(update={"model": None}))
        if not candidates:
            raise ModelPolicyDeniedError(
                "selected model provider is unavailable for this workspace",
                metadata={
                    "provider_id": provider_id,
                    "model_id": resolved_model,
                    "reasoning_effort": reasoning_effort,
                },
            )
        provider = candidates[0]
        resolved_model = resolved_model or provider.default_model
        if resolved_model is None:
            raise ModelPolicyDeniedError("selected provider has no model")
        policy.assert_request_allowed(request.model_copy(update={"model": resolved_model}))
        resolved_effort: str = (
            reasoning_effort
            or provider.default_reasoning_effort
            or (provider.reasoning_efforts[0] if provider.reasoning_efforts else "none")
        )
        final_request = request.model_copy(
            update={
                "provider_id": provider.id,
                "model": resolved_model,
                "reasoning_effort": resolved_effort,
            }
        )
        if not provider.matches(final_request):
            raise ModelPolicyDeniedError(
                "selected reasoning effort is not supported by the model provider",
                metadata={
                    "provider_id": provider.id,
                    "model_id": resolved_model,
                    "reasoning_effort": resolved_effort,
                },
            )
        return ModelSelection(
            provider_id=provider.id,
            model_id=resolved_model,
            reasoning_effort=resolved_effort,
        )

    def _append_to_active_run(
        self,
        tenant_id: str,
        user_id: str,
        thread: ChatThread,
        run: Run,
        payload: ChatMessageSubmit,
    ) -> MessageDispatch:
        dispatch_status = (
            ChatMessageDispatchStatus.READY
            if payload.delivery_mode == "manual"
            else
            ChatMessageDispatchStatus.STEERING
            if self._steering_available_resolver()
            and (
                payload.delivery_mode == "steer"
                or (
                    payload.delivery_mode == "auto"
                    and run.status.value == "waiting_for_user"
                )
            )
            else ChatMessageDispatchStatus.QUEUED
        )
        message = self.store.append_chat_message(
            tenant_id,
            thread.id,
            user_id,
            ChatMessageCreate(
                content=payload.display_content or payload.content,
                execution_content=payload.content,
                kind=(
                    "manual_queue"
                    if payload.delivery_mode == "manual"
                    else _MESSAGE_KIND_BY_RUN_MODE[payload.mode]
                ),
                dispatch_status=dispatch_status,
                attachments=payload.attachments,
                resource_refs=payload.resource_refs,
            ),
        )
        return self._dispatch_response(message, run, run_started=False)

    def _start_run(
        self,
        tenant_id: str,
        user_id: str,
        thread: ChatThread,
        selection: ModelSelection,
        payload: ChatMessageSubmit,
    ) -> MessageDispatch:
        message = self.store.append_chat_message(
            tenant_id,
            thread.id,
            user_id,
            ChatMessageCreate(
                content=payload.display_content or payload.content,
                execution_content=payload.content,
                kind=_MESSAGE_KIND_BY_RUN_MODE[payload.mode],
                dispatch_status=ChatMessageDispatchStatus.INFLIGHT,
                attachments=payload.attachments,
                resource_refs=payload.resource_refs,
            ),
        )
        try:
            run, run_started = self.store.create_queued_thread_run_if_absent(
                tenant_id,
                user_id,
                RunCreate(
                    workspace_id=thread.workspace_id,
                    message=payload.content,
                    attachments=message.attachments,
                    mode=payload.mode,
                    thread_id=thread.id,
                    trigger_message_id=message.id,
                    provider_id=selection.provider_id,
                    model_id=selection.model_id,
                    reasoning_effort=selection.reasoning_effort,
                    resource_refs=message.resource_refs,
                ),
            )
        except Exception:
            self.store.delete_chat_message(tenant_id, message.id)
            raise
        if not run_started:
            fallback_status = (
                ChatMessageDispatchStatus.READY
                if payload.delivery_mode == "manual"
                else ChatMessageDispatchStatus.STEERING
                if payload.delivery_mode == "steer"
                else ChatMessageDispatchStatus.QUEUED
            )
            message = self.store.update_chat_message(
                tenant_id,
                message.id,
                dispatch_status=fallback_status,
                kind=(
                    "manual_queue"
                    if payload.delivery_mode == "manual"
                    else _MESSAGE_KIND_BY_RUN_MODE[payload.mode]
                ),
            )
        return self._dispatch_response(message, run, run_started=run_started)

    def _selection_request(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        provider_id: str | None,
        model_id: str | None,
        reasoning_effort: str | None,
    ) -> ModelGatewayRequest:
        return ModelGatewayRequest(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            run_id="chat_model_selection",
            provider_id=provider_id,
            model=model_id,
            reasoning_effort=reasoning_effort,
            messages=[ModelMessage(role="user", content="Validate model selection")],
        )

    def _dispatch_response(
        self,
        message: ChatMessage,
        run: Run,
        *,
        run_started: bool,
    ) -> MessageDispatch:
        return MessageDispatch(
            message_id=message.id,
            run_id=run.id,
            dispatch_status=message.dispatch_status,
            events_url=f"/api/runs/{run.id}/events",
            run_started=run_started,
        )

    def _wait_for_idempotent_completion(
        self,
        request: IdempotencyRequest,
    ) -> IdempotencyRecord:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            replay = find_idempotent_replay(self.store, request)
            if replay is not None and not self._is_pending(replay.response_body):
                return replay
            time.sleep(0.01)
        raise IdempotencyConflictError("Idempotent request is still in progress")

    def _is_pending(self, response_body: dict[str, Any]) -> bool:
        return response_body.get(self._PENDING_MARKER) is True

    @contextmanager
    def _named_lock(self, name: str) -> Iterator[None]:
        with self._lock_guard:
            entry = self._locks.setdefault(name, _NamedLockEntry())
            entry.users += 1
        try:
            with entry.lock:
                yield
        finally:
            with self._lock_guard:
                entry.users -= 1
                if entry.users == 0 and self._locks.get(name) is entry:
                    del self._locks[name]
