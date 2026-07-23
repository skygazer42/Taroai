from fastapi.testclient import TestClient

from taroai.app import create_app
from taroai.config import Settings
from taroai.domain import (
    ChatMessageCreate,
    ChatThreadCreate,
    RunCreate,
    RunMode,
    RunStatus,
)


def test_agent_run_notifications_persist_and_are_isolated(tmp_path):
    settings = Settings(
        control_plane_store_backend="sql",
        database_url=f"sqlite:///{tmp_path / 'notifications.sqlite3'}",
        _env_file=None,
    )
    app = create_app(settings=settings)
    store = app.state.store
    first = store.create_run(
        "tenant_acme",
        "user_first",
        RunCreate(
            workspace_id="workspace_first",
            agent_id="agent_first",
            message="Run in the background",
            mode=RunMode.AUTONOMOUS,
        ),
    )
    for run_status in (
        RunStatus.AWAITING_APPROVAL,
        RunStatus.FAILED,
        RunStatus.SUCCEEDED,
    ):
        store.update_run_status("tenant_acme", first.id, run_status)
    child_thread = store.create_chat_thread(
        "tenant_acme",
        "user_first",
        ChatThreadCreate(workspace_id="workspace_first", title="Internal task"),
    )
    child_message = store.append_chat_message(
        "tenant_acme",
        child_thread.id,
        "user_first",
        ChatMessageCreate(content="Internal task", kind="workflow_task"),
    )
    child = store.create_run(
        "tenant_acme",
        "user_first",
        RunCreate(
            workspace_id="workspace_first",
            agent_id="agent_first",
            message="Internal workflow task",
            mode=RunMode.AUTONOMOUS,
            thread_id=child_thread.id,
            trigger_message_id=child_message.id,
        ),
    )
    store.update_run_status("tenant_acme", child.id, RunStatus.SUCCEEDED)
    second = store.create_run(
        "tenant_acme",
        "user_second",
        RunCreate(
            workspace_id="workspace_second",
            agent_id="agent_second",
            message="Another background run",
            mode=RunMode.AUTONOMOUS,
        ),
    )
    store.update_run_status("tenant_acme", second.id, RunStatus.FAILED)

    client = TestClient(create_app(settings=settings))
    first_headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": "user_first"}
    second_headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": "user_second"}
    listed = client.get("/api/notifications", headers=first_headers)
    items = listed.json()["items"]

    assert listed.status_code == 200
    assert {item["type"] for item in items} == {
        "agent.run.awaiting_approval",
        "agent.run.failed",
        "agent.run.succeeded",
    }
    assert set(items[0]) == {
        "id",
        "tenant_id",
        "user_id",
        "type",
        "title",
        "body",
        "run_id",
        "thread_id",
        "created_at",
        "read_at",
    }
    assert client.get(
        "/api/notifications/unread-count", headers=first_headers
    ).json() == {"count": 3}
    assert len(client.get("/api/notifications", headers=second_headers).json()["items"]) == 1
    assert client.post(
        f"/api/notifications/{items[0]['id']}/read", headers=second_headers
    ).status_code == 404
    assert client.post(
        f"/api/notifications/{items[0]['id']}/read", headers=first_headers
    ).json()["read_at"] is not None
    assert client.post("/api/notifications/read-all", headers=first_headers).json() == {
        "updated": 2
    }
    assert client.get(
        "/api/notifications/unread-count", headers=first_headers
    ).json() == {"count": 0}
