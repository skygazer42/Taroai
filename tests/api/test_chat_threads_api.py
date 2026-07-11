from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient

from taroai.app import create_app
from taroai.api.idempotency import build_idempotency_record, build_idempotency_request
from taroai.chat import ChatMessageSubmit
from taroai.config import Settings
from taroai.db import SqlControlPlaneRepository
from taroai.model_gateway import (
    InMemoryModelProviderStore,
    ModelPolicy,
    ModelPolicyScope,
    ModelProviderConfig,
    ModelProviderUpsert,
)
from taroai.domain import RunStatus, utc_now
from taroai.store import InMemoryControlPlaneStore
from taroai.workers import InMemoryJobQueue


class BarrierSqlControlPlaneRepository(SqlControlPlaneRepository):
    active_run_barrier: ClassVar[Barrier | None] = None
    idempotency_barrier: ClassVar[Barrier | None] = None

    def get_active_thread_run(self, tenant_id: str, thread_id: str):
        run = super().get_active_thread_run(tenant_id, thread_id)
        if run is None and self.active_run_barrier is not None:
            self.active_run_barrier.wait(timeout=5)
        return run

    def reserve_idempotency_record(self, record):
        if self.idempotency_barrier is not None:
            self.idempotency_barrier.wait(timeout=5)
        return super().reserve_idempotency_record(record)


class FaultingChatRunStore(InMemoryControlPlaneStore):
    def create_run(self, tenant_id, user_id, payload):
        super().create_run(tenant_id, user_id, payload)
        raise RuntimeError("simulated storage failure")

    def create_queued_thread_run_if_absent(self, tenant_id, user_id, payload):
        raise RuntimeError("simulated storage failure")


TENANT_HEADERS = {
    "X-Tenant-ID": "tenant_acme",
    "X-User-ID": "user_luke",
}
OTHER_TENANT_HEADERS = {
    "X-Tenant-ID": "tenant_other",
    "X-User-ID": "user_other",
}


def chat_settings(**updates) -> Settings:
    values = {
        "_env_file": None,
        "run_execution_dispatch_mode": "queue",
        "agent_runtime_mode": "loop_v2",
        "model_gateway_allowed_models": ["deepseek-chat"],
        "model_gateway_policy_scopes": [
            ModelPolicyScope(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                default_model="deepseek-chat",
                allowed_models=["deepseek-chat"],
            )
        ],
        "model_gateway_providers": [
            ModelProviderConfig(
                id="deepseek",
                display_name="DeepSeek",
                base_url="https://private.deepseek.example/v1",
                api_key_secret_ref_id="secret_deepseek_prod",
                default_model="deepseek-chat",
                model_ids=["deepseek-chat", "deepseek-reasoner"],
                reasoning_efforts=["low", "medium", "high"],
                default_reasoning_effort="medium",
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
            ),
            ModelProviderConfig(
                id="other-workspace",
                display_name="Other Workspace",
                base_url="https://other-workspace.example/v1",
                api_key_secret_ref_id="secret_other_workspace",
                default_model="other-model",
                model_ids=["other-model"],
                tenant_id="tenant_acme",
                workspace_id="workspace_other",
            ),
            ModelProviderConfig(
                id="other-tenant",
                display_name="Other Tenant",
                base_url="https://other-tenant.example/v1",
                api_key_secret_ref_id="secret_other_tenant",
                default_model="other-model",
                model_ids=["other-model"],
                reasoning_efforts=["low"],
                default_reasoning_effort="low",
                tenant_id="tenant_other",
                workspace_id="workspace_other",
            ),
        ],
    }
    values["model_gateway_policy_scopes"].append(
        ModelPolicyScope(
            tenant_id="tenant_other",
            workspace_id="workspace_other",
            default_model="other-model",
            allowed_models=["other-model"],
        )
    )
    values.update(updates)
    return Settings(**values)


def create_chat_client(
    *,
    store=None,
    settings: Settings | None = None,
    model_provider_store=None,
) -> TestClient:
    return TestClient(
        create_app(
            store=store,
            settings=settings or chat_settings(),
            model_provider_store=model_provider_store,
            job_queue=InMemoryJobQueue(),
        )
    )


def create_thread(client: TestClient, **overrides) -> dict:
    payload = {
        "workspace_id": "workspace_sales",
        "title": "Repair the report",
        "provider_id": "deepseek",
        "model_id": "deepseek-chat",
        "reasoning_effort": "medium",
    }
    payload.update(overrides)
    response = client.post("/api/threads", headers=TENANT_HEADERS, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_thread_crud_orders_by_latest_update_and_soft_deletes():
    client = create_chat_client()
    first = create_thread(client, title="First")
    second = create_thread(client, title="Second")

    initial = client.get(
        "/api/threads?workspace_id=workspace_sales",
        headers=TENANT_HEADERS,
    )
    assert initial.status_code == 200
    assert [thread["id"] for thread in initial.json()] == [second["id"], first["id"]]

    patched = client.patch(
        f"/api/threads/{first['id']}",
        headers=TENANT_HEADERS,
        json={"title": "First, updated", "pinned": True},
    )
    assert patched.status_code == 200
    assert patched.json()["title"] == "First, updated"
    assert patched.json()["pinned"] is True
    assert client.get(
        "/api/threads?workspace_id=workspace_sales",
        headers=TENANT_HEADERS,
    ).json()[0]["id"] == first["id"]

    deleted = client.delete(
        f"/api/threads/{first['id']}",
        headers=TENANT_HEADERS,
    )
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "deleted"
    hidden = client.get(
        f"/api/threads/{first['id']}",
        headers=TENANT_HEADERS,
    )
    assert hidden.status_code == 404
    assert [
        thread["id"]
        for thread in client.get(
            "/api/threads?workspace_id=workspace_sales",
            headers=TENANT_HEADERS,
        ).json()
    ] == [second["id"]]


def test_thread_create_resolves_default_provider_model_and_reasoning_snapshot():
    client = create_chat_client()

    created = client.post(
        "/api/threads",
        headers=TENANT_HEADERS,
        json={"workspace_id": "workspace_sales"},
    )

    assert created.status_code == 201
    assert (
        created.json()["provider_id"],
        created.json()["model_id"],
        created.json()["reasoning_effort"],
    ) == ("deepseek", "deepseek-chat", "medium")


def test_new_message_moves_thread_to_front_of_recent_conversations():
    client = create_chat_client()
    first = create_thread(client, title="First")
    second = create_thread(client, title="Second")

    sent = client.post(
        f"/api/threads/{first['id']}/messages",
        headers=TENANT_HEADERS,
        json={"content": "Touch first"},
    )
    ordered = client.get(
        "/api/threads?workspace_id=workspace_sales",
        headers=TENANT_HEADERS,
    ).json()

    assert sent.status_code == 202
    assert [thread["id"] for thread in ordered][:2] == [first["id"], second["id"]]


def test_thread_routes_do_not_reveal_cross_tenant_resources():
    client = create_chat_client()
    thread = create_thread(client)

    for method, path, kwargs in [
        ("get", f"/api/threads/{thread['id']}", {}),
        ("patch", f"/api/threads/{thread['id']}", {"json": {"title": "stolen"}}),
        ("delete", f"/api/threads/{thread['id']}", {}),
        ("get", f"/api/threads/{thread['id']}/messages", {}),
        (
            "post",
            f"/api/threads/{thread['id']}/messages",
            {"json": {"content": "stolen"}},
        ),
    ]:
        response = getattr(client, method)(path, headers=OTHER_TENANT_HEADERS, **kwargs)
        assert response.status_code == 404
        assert response.json()["code"] == "not_found"


def test_post_thread_message_starts_run_with_immutable_model_snapshot():
    client = create_chat_client()
    thread = create_thread(client)

    sent = client.post(
        f"/api/threads/{thread['id']}/messages",
        headers={**TENANT_HEADERS, "Idempotency-Key": "message-1"},
        json={
            "content": "Repair the report",
            "delivery_mode": "queue",
            "attachments": ["storage://report.csv"],
            "resource_refs": [
                {"type": "skill", "id": "spreadsheet-repair", "version": "1.2.0"}
            ],
        },
    )

    assert sent.status_code == 202
    body = sent.json()
    assert set(body) == {"message_id", "run_id", "dispatch_status", "events_url"}
    assert "Repair the report" not in sent.text
    assert "storage://report.csv" not in sent.text
    assert body["dispatch_status"] == "inflight"
    run = client.get(f"/api/runs/{body['run_id']}", headers=TENANT_HEADERS).json()
    assert (run["thread_id"], run["trigger_message_id"]) == (
        thread["id"],
        body["message_id"],
    )
    assert (run["provider_id"], run["model_id"], run["reasoning_effort"]) == (
        "deepseek",
        "deepseek-chat",
        "medium",
    )
    assert run["attachments"] == ["storage://report.csv"]
    assert run["resource_refs"] == [
        {"type": "skill", "id": "spreadsheet-repair", "version": "1.2.0"}
    ]


def test_active_run_queues_or_steers_messages_without_creating_duplicate_run():
    client = create_chat_client()
    thread = create_thread(client)
    first = client.post(
        f"/api/threads/{thread['id']}/messages",
        headers=TENANT_HEADERS,
        json={"content": "Start", "delivery_mode": "auto"},
    )
    assert first.status_code == 202

    queued = client.post(
        f"/api/threads/{thread['id']}/messages",
        headers=TENANT_HEADERS,
        json={"content": "Do this next", "delivery_mode": "queue"},
    )
    steering = client.post(
        f"/api/threads/{thread['id']}/messages",
        headers=TENANT_HEADERS,
        json={"content": "Use the smaller table", "delivery_mode": "steer"},
    )

    assert queued.status_code == steering.status_code == 202
    assert queued.json()["run_id"] == steering.json()["run_id"] == first.json()["run_id"]
    assert queued.json()["dispatch_status"] == "queued"
    assert steering.json()["dispatch_status"] == "steering"
    messages = client.get(
        f"/api/threads/{thread['id']}/messages",
        headers=TENANT_HEADERS,
    ).json()
    assert [message["sequence"] for message in messages] == [1, 2, 3]
    assert [message["dispatch_status"] for message in messages] == [
        "inflight",
        "queued",
        "steering",
    ]
    assert len(client.get("/api/runs", headers=TENANT_HEADERS).json()["items"]) == 1


@pytest.mark.parametrize(
    "active_status",
    [
        RunStatus.CREATED,
        RunStatus.QUEUED,
        RunStatus.CLASSIFYING,
        RunStatus.RETRIEVING_CONTEXT,
        RunStatus.PLANNING,
        RunStatus.AWAITING_POLICY,
        RunStatus.RUNNING,
        RunStatus.AWAITING_APPROVAL,
        RunStatus.RETRYING,
    ],
)
def test_every_non_terminal_run_status_prevents_duplicate_thread_run(active_status):
    client = create_chat_client()
    thread = create_thread(client)
    first = client.post(
        f"/api/threads/{thread['id']}/messages",
        headers=TENANT_HEADERS,
        json={"content": "Start"},
    ).json()
    client.app.state.store.update_run_status(
        "tenant_acme",
        first["run_id"],
        active_status,
    )

    second = client.post(
        f"/api/threads/{thread['id']}/messages",
        headers=TENANT_HEADERS,
        json={"content": "Next", "delivery_mode": "queue"},
    )

    assert second.status_code == 202
    assert second.json()["run_id"] == first["run_id"]
    assert len(client.get("/api/runs", headers=TENANT_HEADERS).json()["items"]) == 1


@pytest.mark.parametrize(
    "terminal_status",
    [
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
        RunStatus.TIMED_OUT,
    ],
)
def test_terminal_thread_run_allows_next_message_to_start_new_run(terminal_status):
    client = create_chat_client()
    thread = create_thread(client)
    first = client.post(
        f"/api/threads/{thread['id']}/messages",
        headers=TENANT_HEADERS,
        json={"content": "Start"},
    ).json()
    client.app.state.store.update_run_status(
        "tenant_acme",
        first["run_id"],
        terminal_status,
    )

    second = client.post(
        f"/api/threads/{thread['id']}/messages",
        headers=TENANT_HEADERS,
        json={"content": "Continue"},
    )

    assert second.status_code == 202
    assert second.json()["run_id"] != first["run_id"]


def test_message_submit_is_idempotent_and_conflicts_on_changed_payload():
    client = create_chat_client()
    thread = create_thread(client)
    path = f"/api/threads/{thread['id']}/messages"
    headers = {**TENANT_HEADERS, "Idempotency-Key": "message-replay-1"}
    payload = {"content": "Only once", "delivery_mode": "queue"}

    first = client.post(path, headers=headers, json=payload)
    replay = client.post(path, headers=headers, json=payload)
    conflict = client.post(
        path,
        headers=headers,
        json={"content": "Changed", "delivery_mode": "queue"},
    )

    assert first.status_code == replay.status_code == 202
    assert replay.json() == first.json()
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "idempotency_key_conflict"
    assert len(client.get(path, headers=TENANT_HEADERS).json()) == 1
    assert len(client.get("/api/runs", headers=TENANT_HEADERS).json()["items"]) == 1
    assert client.app.state.chat_service._locks == {}


def test_stale_idempotency_reservation_is_reclaimed_safely():
    store = InMemoryControlPlaneStore()
    client = create_chat_client(store=store)
    thread = create_thread(client)
    path = f"/api/threads/{thread['id']}/messages"
    payload = ChatMessageSubmit(content="Recover stale reservation")
    request = build_idempotency_request(
        tenant_id="tenant_acme",
        key="stale-message",
        method="POST",
        path=path,
        payload=payload,
    )
    assert request is not None
    stale = build_idempotency_record(
        request,
        102,
        {"_taroai_idempotency_pending": True},
    ).model_copy(update={"created_at": utc_now() - timedelta(minutes=5)})
    store.save_idempotency_record(stale)

    recovered = client.post(
        path,
        headers={**TENANT_HEADERS, "Idempotency-Key": "stale-message"},
        json=payload.model_dump(mode="json"),
    )

    assert recovered.status_code == 202
    assert len(client.get(path, headers=TENANT_HEADERS).json()) == 1


def test_unrelated_run_storage_failure_is_not_masked_and_message_is_cleaned_up():
    store = FaultingChatRunStore()
    client = create_chat_client(store=store)
    thread = create_thread(client)

    with pytest.raises(RuntimeError, match="simulated storage failure"):
        client.post(
            f"/api/threads/{thread['id']}/messages",
            headers=TENANT_HEADERS,
            json={"content": "Must roll back"},
        )
    assert store.list_chat_messages("tenant_acme", thread["id"]) == []


def test_concurrent_idempotent_submit_creates_exactly_one_message_and_run():
    client = create_chat_client()
    thread = create_thread(client)
    path = f"/api/threads/{thread['id']}/messages"
    headers = {**TENANT_HEADERS, "Idempotency-Key": "concurrent-message"}

    def submit_once(_index: int):
        return client.post(
            path,
            headers=headers,
            json={"content": "Exactly once", "delivery_mode": "queue"},
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        responses = list(executor.map(submit_once, range(8)))

    assert {response.status_code for response in responses} == {202}
    assert len({response.json()["message_id"] for response in responses}) == 1
    assert len({response.json()["run_id"] for response in responses}) == 1
    assert len(client.get(path, headers=TENANT_HEADERS).json()) == 1
    assert len(client.get("/api/runs", headers=TENANT_HEADERS).json()["items"]) == 1


def test_concurrent_first_messages_share_one_thread_active_run():
    client = create_chat_client()
    thread = create_thread(client)
    path = f"/api/threads/{thread['id']}/messages"

    def submit(index: int):
        return client.post(
            path,
            headers={**TENANT_HEADERS, "Idempotency-Key": f"first-{index}"},
            json={"content": f"Message {index}", "delivery_mode": "queue"},
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        responses = list(executor.map(submit, range(8)))

    assert {response.status_code for response in responses} == {202}
    assert len({response.json()["run_id"] for response in responses}) == 1
    assert len(client.get(path, headers=TENANT_HEADERS).json()) == 8
    assert len(client.get("/api/runs", headers=TENANT_HEADERS).json()["items"]) == 1


def test_sql_instances_converge_concurrent_first_messages_to_one_active_run(tmp_path):
    settings = chat_settings(database_url=f"sqlite:///{tmp_path / 'race.sqlite3'}")
    first_store = BarrierSqlControlPlaneRepository(config=settings.database_config())
    first_store.initialize_schema(Path("apps/api/migrations"))
    second_store = BarrierSqlControlPlaneRepository(config=settings.database_config())
    first_client = create_chat_client(store=first_store, settings=settings)
    second_client = create_chat_client(store=second_store, settings=settings)
    thread = create_thread(first_client)
    BarrierSqlControlPlaneRepository.active_run_barrier = Barrier(2)

    def submit(client_and_content):
        client, content = client_and_content
        return client.post(
            f"/api/threads/{thread['id']}/messages",
            headers=TENANT_HEADERS,
            json={"content": content, "delivery_mode": "queue"},
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(
                executor.map(
                    submit,
                    [(first_client, "First"), (second_client, "Second")],
                )
            )
    finally:
        BarrierSqlControlPlaneRepository.active_run_barrier = None

    assert {response.status_code for response in responses} == {202}
    assert len({response.json()["run_id"] for response in responses}) == 1
    assert len(first_store.list_runs("tenant_acme")) == 1


def test_sql_instances_reserve_same_idempotency_key_before_side_effects(tmp_path):
    settings = chat_settings(database_url=f"sqlite:///{tmp_path / 'idempotency.sqlite3'}")
    first_store = BarrierSqlControlPlaneRepository(config=settings.database_config())
    first_store.initialize_schema(Path("apps/api/migrations"))
    second_store = BarrierSqlControlPlaneRepository(config=settings.database_config())
    first_client = create_chat_client(store=first_store, settings=settings)
    second_client = create_chat_client(store=second_store, settings=settings)
    thread = create_thread(first_client)
    BarrierSqlControlPlaneRepository.idempotency_barrier = Barrier(2)

    def submit(client):
        return client.post(
            f"/api/threads/{thread['id']}/messages",
            headers={**TENANT_HEADERS, "Idempotency-Key": "sql-shared-key"},
            json={"content": "Exactly once", "delivery_mode": "queue"},
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(submit, [first_client, second_client]))
    finally:
        BarrierSqlControlPlaneRepository.idempotency_barrier = None

    assert {response.status_code for response in responses} == {202}
    assert len({response.json()["message_id"] for response in responses}) == 1
    assert len({response.json()["run_id"] for response in responses}) == 1
    assert len(first_store.list_chat_messages("tenant_acme", thread["id"])) == 1
    assert len(first_store.list_runs("tenant_acme")) == 1


def test_idempotency_keys_are_isolated_by_tenant_and_thread_path():
    client = create_chat_client()
    acme_thread = create_thread(client)
    other_thread = client.post(
        "/api/threads",
        headers=OTHER_TENANT_HEADERS,
        json={"workspace_id": "workspace_other"},
    ).json()
    headers = {"Idempotency-Key": "shared-key"}

    acme = client.post(
        f"/api/threads/{acme_thread['id']}/messages",
        headers={**TENANT_HEADERS, **headers},
        json={"content": "Acme"},
    )
    other = client.post(
        f"/api/threads/{other_thread['id']}/messages",
        headers={**OTHER_TENANT_HEADERS, **headers},
        json={"content": "Other"},
    )

    assert acme.status_code == other.status_code == 202
    assert acme.json()["message_id"] != other.json()["message_id"]


def test_dispatch_policy_rejection_is_atomic_and_leaves_no_message_or_run():
    client = create_chat_client()
    thread = create_thread(client)
    client.app.state.runtime.model_policy = ModelPolicy(
        allowed_models=["another-model"]
    )

    rejected = client.post(
        f"/api/threads/{thread['id']}/messages",
        headers=TENANT_HEADERS,
        json={"content": "Must not persist", "delivery_mode": "queue"},
    )

    assert rejected.status_code == 403
    assert rejected.json()["code"] == "model_policy_denied"
    assert client.get(
        f"/api/threads/{thread['id']}/messages",
        headers=TENANT_HEADERS,
    ).json() == []
    assert client.get("/api/runs", headers=TENANT_HEADERS).json()["items"] == []


def test_archived_thread_rejects_messages_and_deleted_thread_cannot_be_revived():
    client = create_chat_client()
    archived = create_thread(client, title="Archived")
    client.app.state.store.update_chat_thread(
        "tenant_acme", archived["id"], status="archived"
    )

    archived_post = client.post(
        f"/api/threads/{archived['id']}/messages",
        headers=TENANT_HEADERS,
        json={"content": "No"},
    )
    assert archived_post.status_code == 409

    deleted = create_thread(client, title="Deleted")
    client.delete(f"/api/threads/{deleted['id']}", headers=TENANT_HEADERS)
    revive = client.patch(
        f"/api/threads/{deleted['id']}",
        headers=TENANT_HEADERS,
        json={"title": "Revived", "status": "active"},
    )
    assert revive.status_code in {404, 422}


@pytest.mark.parametrize(
    "payload",
    [
        {"workspace_id": "workspace_sales", "sandbox_session_id": "sandbox_admin"},
        {"workspace_id": "workspace_sales", "status": "active"},
    ],
)
def test_thread_create_rejects_server_managed_fields(payload):
    client = create_chat_client()
    response = client.post("/api/threads", headers=TENANT_HEADERS, json=payload)
    assert response.status_code == 422


@pytest.mark.parametrize(
    "field,value",
    [
        ("role", "assistant"),
        ("dispatch_status", "completed"),
        ("delivery_status", "delivered"),
    ],
)
def test_message_submit_rejects_server_managed_fields(field, value):
    client = create_chat_client()
    thread = create_thread(client)
    response = client.post(
        f"/api/threads/{thread['id']}/messages",
        headers=TENANT_HEADERS,
        json={"content": "No forgery", field: value},
    )
    assert response.status_code == 422


def test_thread_patch_does_not_mutate_existing_run_model_snapshot():
    client = create_chat_client()
    thread = create_thread(client)
    first = client.post(
        f"/api/threads/{thread['id']}/messages",
        headers=TENANT_HEADERS,
        json={"content": "Snapshot"},
    ).json()

    patched = client.patch(
        f"/api/threads/{thread['id']}",
        headers=TENANT_HEADERS,
        json={"reasoning_effort": "high"},
    )
    persisted_run = client.get(
        f"/api/runs/{first['run_id']}", headers=TENANT_HEADERS
    ).json()

    assert patched.status_code == 200
    assert patched.json()["reasoning_effort"] == "high"
    assert persisted_run["reasoning_effort"] == "medium"


def test_invalid_or_unsupported_model_selection_does_not_create_thread():
    client = create_chat_client()

    blocked_model = client.post(
        "/api/threads",
        headers=TENANT_HEADERS,
        json={
            "workspace_id": "workspace_sales",
            "provider_id": "deepseek",
            "model_id": "deepseek-reasoner",
        },
    )
    unsupported_reasoning = client.post(
        "/api/threads",
        headers=TENANT_HEADERS,
        json={
            "workspace_id": "workspace_sales",
            "provider_id": "deepseek",
            "model_id": "deepseek-chat",
            "reasoning_effort": "minimal",
        },
    )

    assert blocked_model.status_code == unsupported_reasoning.status_code == 403
    assert client.get("/api/threads", headers=TENANT_HEADERS).json() == []


def test_model_catalog_applies_workspace_policy_and_redacts_provider_secrets():
    provider_store = InMemoryModelProviderStore()
    provider_store.upsert_provider(
        ModelProviderUpsert(
            tenant_id="tenant_acme",
            id="disabled-provider",
            base_url="https://disabled.example/v1",
            api_key_secret_ref_id="secret_disabled",
            default_model="deepseek-chat",
            model_ids=["deepseek-chat"],
        )
    )
    provider_store.set_status("tenant_acme", "disabled-provider", "disabled")
    client = create_chat_client(model_provider_store=provider_store)

    response = client.get(
        "/api/model-catalog?workspace_id=workspace_sales",
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 200
    assert response.json() == [
        {
            "provider_id": "deepseek",
            "model_id": "deepseek-chat",
            "display_name": "DeepSeek / deepseek-chat",
            "reasoning_efforts": ["low", "medium", "high"],
        }
    ]
    serialized = response.text.lower()
    for forbidden in [
        "base_url",
        "api_key",
        "secret_ref",
        "credential",
        "private.deepseek.example",
        "secret_deepseek_prod",
        "disabled-provider",
        "other-workspace",
        "other-tenant",
        "deepseek-reasoner",
    ]:
        assert forbidden not in serialized


def test_model_catalog_and_selection_refresh_after_provider_is_disabled():
    provider_store = InMemoryModelProviderStore()
    provider_store.upsert_provider(
        ModelProviderUpsert(
            tenant_id="tenant_acme",
            id="stored-deepseek",
            display_name="Stored DeepSeek",
            base_url="https://stored.example/v1",
            api_key_secret_ref_id="secret_stored",
            default_model="deepseek-chat",
            model_ids=["deepseek-chat"],
            reasoning_efforts=["low", "medium"],
            default_reasoning_effort="medium",
            workspace_id="workspace_sales",
        )
    )
    settings = chat_settings(model_gateway_providers=[])
    client = create_chat_client(
        settings=settings,
        model_provider_store=provider_store,
    )

    before = client.get(
        "/api/model-catalog?workspace_id=workspace_sales",
        headers=TENANT_HEADERS,
    )
    provider_store.set_status(
        "tenant_acme", "stored-deepseek", "disabled", updated_by_user_id="admin"
    )
    after = client.get(
        "/api/model-catalog?workspace_id=workspace_sales",
        headers=TENANT_HEADERS,
    )
    rejected = client.post(
        "/api/threads",
        headers=TENANT_HEADERS,
        json={"workspace_id": "workspace_sales"},
    )

    assert before.json()[0]["provider_id"] == "stored-deepseek"
    assert after.json() == []
    assert rejected.status_code == 403


def test_chat_mutations_emit_safe_audit_once_across_idempotent_replay():
    store = InMemoryControlPlaneStore()
    client = create_chat_client(store=store)
    thread = create_thread(client, title="Audited")
    client.patch(
        f"/api/threads/{thread['id']}",
        headers=TENANT_HEADERS,
        json={"title": "Audited update"},
    )
    headers = {**TENANT_HEADERS, "Idempotency-Key": "audited-message"}
    payload = {
        "content": "Sensitive customer text",
        "attachments": ["storage://secret.csv"],
        "resource_refs": [{"type": "skill", "id": "secret-skill"}],
    }
    first = client.post(
        f"/api/threads/{thread['id']}/messages",
        headers=headers,
        json=payload,
    )
    replay = client.post(
        f"/api/threads/{thread['id']}/messages",
        headers=headers,
        json=payload,
    )

    assert first.status_code == replay.status_code == 202
    chat_events = [
        event
        for event in store.list_audit_events("tenant_acme")
        if event.event_type.startswith("chat.")
    ]
    assert [event.event_type for event in chat_events] == [
        "chat.thread.created",
        "chat.thread.updated",
        "chat.message.accepted",
        "chat.run.queued",
    ]
    serialized = str([event.metadata for event in chat_events])
    for secret in [
        "Sensitive customer text",
        "storage://secret.csv",
        "secret-skill",
    ]:
        assert secret not in serialized
    assert chat_events[2].metadata["attachment_count"] == 1
    assert chat_events[2].metadata["resource_ref_count"] == 1


def test_sql_restart_preserves_thread_message_and_run_snapshot(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'chat.sqlite3'}"
    settings = chat_settings(
        database_url=database_url,
        control_plane_store_backend="sql",
    )
    first_client = create_chat_client(settings=settings)
    thread = create_thread(first_client)
    sent = first_client.post(
        f"/api/threads/{thread['id']}/messages",
        headers=TENANT_HEADERS,
        json={"content": "Persist this", "delivery_mode": "queue"},
    ).json()

    restarted = create_chat_client(settings=settings)
    persisted_thread = restarted.get(
        f"/api/threads/{thread['id']}", headers=TENANT_HEADERS
    ).json()
    persisted_messages = restarted.get(
        f"/api/threads/{thread['id']}/messages", headers=TENANT_HEADERS
    ).json()
    persisted_run = restarted.get(
        f"/api/runs/{sent['run_id']}", headers=TENANT_HEADERS
    ).json()

    assert persisted_thread["provider_id"] == "deepseek"
    assert persisted_messages[0]["id"] == sent["message_id"]
    assert persisted_run["trigger_message_id"] == sent["message_id"]
    assert persisted_run["provider_id"] == "deepseek"
