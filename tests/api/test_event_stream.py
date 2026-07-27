import asyncio
import threading
import time
from pathlib import Path

from taroai.db import DatabaseConfig, SqlControlPlaneRepository
from taroai.domain import ChatThreadCreate, RunCreate
from taroai.event_stream import ThreadEventHub
from taroai.store import InMemoryControlPlaneStore

from tests.api.test_chat_threads_api import (
    TENANT_HEADERS,
    chat_settings,
    create_chat_client,
    create_thread,
)


def test_notify_without_a_bound_loop_is_a_noop():
    hub = ThreadEventHub()

    hub.notify("thread_1")

    assert hub.has_waiter_entry("thread_1") is False


def test_wait_times_out_and_drops_the_empty_waiter_entry():
    hub = ThreadEventHub()

    async def scenario() -> bool:
        hub.bind_running_loop()
        return await hub.wait("thread_1", timeout=0.05)

    assert asyncio.run(scenario()) is False
    assert hub.has_waiter_entry("thread_1") is False


def test_notify_from_a_worker_thread_wakes_the_waiter_promptly():
    hub = ThreadEventHub()

    async def scenario() -> tuple[bool, float]:
        hub.bind_running_loop()
        loop = asyncio.get_running_loop()
        notifier = threading.Thread(target=hub.notify, args=("thread_1",))
        loop.call_later(0.05, notifier.start)
        started = loop.time()
        woken = await hub.wait("thread_1", timeout=5.0)
        elapsed = loop.time() - started
        notifier.join()
        return woken, elapsed

    woken, elapsed = asyncio.run(scenario())
    assert woken is True
    assert elapsed < 1.0
    assert hub.has_waiter_entry("thread_1") is False


def test_notify_only_wakes_waiters_for_the_matching_thread():
    hub = ThreadEventHub()

    async def scenario() -> tuple[bool, bool]:
        hub.bind_running_loop()
        loop = asyncio.get_running_loop()
        loop.call_later(0.05, hub.notify, "thread_1")
        matching, other = await asyncio.gather(
            hub.wait("thread_1", timeout=5.0),
            hub.wait("thread_2", timeout=0.5),
        )
        return matching, other

    matching, other = asyncio.run(scenario())
    assert matching is True
    assert other is False
    assert hub.has_waiter_entry("thread_1") is False
    assert hub.has_waiter_entry("thread_2") is False


def test_in_memory_store_reports_thread_events_to_the_notifier():
    store = InMemoryControlPlaneStore()
    store.register_workspace("tenant_acme", "workspace_sales", "user_1")
    thread = store.create_chat_thread(
        "tenant_acme",
        "user_1",
        ChatThreadCreate(workspace_id="workspace_sales"),
    )
    run = store.create_run(
        "tenant_acme",
        "user_1",
        RunCreate(
            workspace_id="workspace_sales",
            message="Trace events",
            thread_id=thread.id,
        ),
    )
    threadless_run = store.create_run(
        "tenant_acme",
        "user_1",
        RunCreate(workspace_id="workspace_sales", message="No thread"),
    )
    notified_thread_ids: list[str] = []
    store.event_notifier = notified_thread_ids.append

    store.append_run_event(run, "note.recorded", {"text": "pushed"})
    store.append_run_event(threadless_run, "note.recorded", {"text": "quiet"})

    assert notified_thread_ids == [thread.id]


def test_sql_repository_reports_thread_events_to_the_notifier(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    repository = SqlControlPlaneRepository(config=DatabaseConfig(url=database_url))
    repository.initialize_schema(Path("apps/api/migrations"))
    repository.register_workspace("tenant_acme", "workspace_sales", "user_1")
    thread = repository.create_chat_thread(
        "tenant_acme",
        "user_1",
        ChatThreadCreate(workspace_id="workspace_sales"),
    )
    run = repository.create_run(
        tenant_id="tenant_acme",
        user_id="user_1",
        payload=RunCreate(
            workspace_id="workspace_sales",
            message="Trace events",
            thread_id=thread.id,
        ),
    )
    notified_thread_ids: list[str] = []
    repository.event_notifier = notified_thread_ids.append

    repository.append_run_event(run, "note.recorded", {"text": "pushed"})

    assert notified_thread_ids == [thread.id]


def test_store_write_survives_a_failing_notifier():
    store = InMemoryControlPlaneStore()
    store.register_workspace("tenant_acme", "workspace_sales", "user_1")
    thread = store.create_chat_thread(
        "tenant_acme",
        "user_1",
        ChatThreadCreate(workspace_id="workspace_sales"),
    )
    run = store.create_run(
        "tenant_acme",
        "user_1",
        RunCreate(
            workspace_id="workspace_sales",
            message="Trace events",
            thread_id=thread.id,
        ),
    )

    def failing_notifier(thread_id: str) -> None:
        raise RuntimeError("listener misbehaved")

    store.event_notifier = failing_notifier

    event = store.append_run_event(run, "note.recorded", {"text": "pushed"})

    assert event.thread_id == thread.id


def test_follow_request_pushes_an_appended_event_before_the_heartbeat_interval():
    # The TestClient buffers streaming bodies until the response completes, so
    # the ASGI app is invoked directly to observe chunk arrival times.
    client = create_chat_client(
        settings=chat_settings(
            event_stream_follow_seconds=10,
            event_stream_heartbeat_seconds=30,
        )
    )
    thread = create_thread(client)
    hub = client.app.state.thread_event_hub
    store = client.app.state.store
    run = store.create_run(
        "tenant_acme",
        "user_luke",
        RunCreate(
            workspace_id="workspace_sales",
            message="Trace events",
            thread_id=thread["id"],
        ),
    )

    async def scenario() -> list[float]:
        loop = asyncio.get_running_loop()
        push_seen = asyncio.Event()
        push_times: list[float] = []
        started = loop.time()
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": f"/api/threads/{thread['id']}/events",
            "raw_path": f"/api/threads/{thread['id']}/events".encode(),
            "query_string": b"follow=true",
            "root_path": "",
            "headers": [
                (b"host", b"testserver"),
                (b"x-tenant-id", b"tenant_acme"),
                (b"x-user-id", b"user_luke"),
            ],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        }

        async def receive() -> dict:
            await push_seen.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict) -> None:
            if message["type"] != "http.response.body":
                return
            if b"note.recorded" in message.get("body", b""):
                push_times.append(loop.time() - started)
                push_seen.set()

        def append_after_delay() -> None:
            time.sleep(0.3)
            store.append_run_event(run, "note.recorded", {"text": "pushed"})

        writer = loop.run_in_executor(None, append_after_delay)
        await client.app(scope, receive, send)
        await writer
        return push_times

    push_times = asyncio.run(scenario())

    assert push_times
    # Well below the 10s follow window and the 30s heartbeat interval: the
    # follower is woken by the store notification, not by a timer.
    assert push_times[0] < 3.0
    # The disconnect released the hub registration.
    assert hub.has_waiter_entry(thread["id"]) is False


def test_thread_events_replay_honours_last_event_id_without_follow():
    client = create_chat_client()
    thread = create_thread(client)
    store = client.app.state.store
    run = store.create_run(
        "tenant_acme",
        "user_luke",
        RunCreate(
            workspace_id="workspace_sales",
            message="Trace events",
            thread_id=thread["id"],
        ),
    )
    first = store.append_run_event(run, "note.recorded", {"text": "one"})
    second = store.append_run_event(run, "note.recorded", {"text": "two"})

    response = client.get(
        f"/api/threads/{thread['id']}/events",
        headers={**TENANT_HEADERS, "Last-Event-ID": str(first.thread_sequence)},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    assert f"id: {second.thread_sequence}\n" in response.text
    assert '"text":"two"' in response.text
    assert '"text":"one"' not in response.text
