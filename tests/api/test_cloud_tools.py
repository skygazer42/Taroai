import base64
import json
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from taroai.config import Settings
from taroai.sandbox.adapter import SandboxExecutionError
from taroai.sandbox.e2b import E2BSandboxAdapter
from taroai.sandbox.factory import build_sandbox_adapter
from taroai.sandbox.models import (
    SandboxCommand,
    SandboxCreateRequest,
    SandboxFileWrite,
)
from taroai.tool_gateway import ToolExecutionError, ToolGateway, ToolGatewayRequest
from taroai.store import NotFoundError
from taroai.web_search import register_web_search_tool_handler


def test_tavily_search_returns_compact_sources_without_exposing_the_key():
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {
                    "results": [
                        {
                            "title": "Exam\x00ple",
                            "url": "https://example.com/source\x00",
                            "content": "Verified\ud800 excerpt",
                            "published_date": "2026-07-15\x00",
                            "raw_content": "must not escape",
                        },
                        {
                            "title": "Provider ignored include_domains",
                            "url": "https://untrusted.example.net/source",
                            "content": "must be discarded",
                        },
                    ]
                }
            ).encode()

    def requester(request, timeout):
        captured["authorization"] = request.headers["Authorization"]
        captured["payload"] = json.loads(request.data)
        captured["timeout"] = timeout
        return Response()

    gateway = ToolGateway()
    register_web_search_tool_handler(
        gateway,
        "secret-search-key",
        timeout_seconds=9,
        requester=requester,
    )
    policy = gateway.policies["web.search"]
    assert "prefer primary sources over aggregators" in policy.description
    assert "set time_range to year" in policy.description
    assert "only when search excerpts" in gateway.policies["web.fetch"].description
    assert "always requires this tool" in gateway.policies["web.fetch"].description
    assert (
        policy.input_schema["properties"]["include_domains"]["description"]
        == "Hostnames that search results must come from."
    )
    result = gateway.execute_request(
        ToolGatewayRequest(
            tenant_id="tenant_1",
            workspace_id="workspace_1",
            user_id="user_1",
            run_id="run_1",
            step_id="step_1",
            tool_name="web.search",
            tool_input={
                "query": "current facts",
                "time_range": "month",
                "include_domains": ["example.com"],
            },
        )
    )

    assert result.output["query"] == "current facts"
    assert result.output["topic"] == "general"
    assert result.output["time_range"] == "month"
    assert result.output["include_domains"] == ["example.com"]
    datetime.fromisoformat(result.output["searched_at"])
    assert result.output["results"] == [
        {
            "title": "Example",
            "url": "https://example.com/source",
            "content": "Verified? excerpt",
            "published_date": "2026-07-15",
        }
    ]
    assert captured["payload"]["include_raw_content"] is False
    assert captured["payload"]["search_depth"] == "advanced"
    assert captured["payload"]["include_domains"] == ["example.com"]
    assert captured["payload"]["time_range"] == "month"
    assert captured["payload"]["max_results"] == 5
    assert captured["timeout"] == 9
    assert "secret-search-key" not in json.dumps(result.output)


def test_tavily_fetch_reads_one_page_without_exposing_the_key():
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {
                    "results": [
                        {
                            "url": "https://www.python.org/downloads/",
                            "raw_content": "Python 3.14.6 is the latest release.\x00",
                        }
                    ],
                    "failed_results": [],
                }
            ).encode()

    def requester(request, timeout):
        captured["authorization"] = request.headers["Authorization"]
        captured["payload"] = json.loads(request.data)
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return Response()

    gateway = ToolGateway()
    register_web_search_tool_handler(
        gateway,
        "secret-search-key",
        timeout_seconds=9,
        requester=requester,
    )
    result = gateway.execute_request(
        ToolGatewayRequest(
            tenant_id="tenant_1",
            workspace_id="workspace_1",
            user_id="user_1",
            run_id="run_1",
            step_id="step_1",
            tool_name="web.fetch",
            tool_input={
                "url": "https://www.python.org/downloads/",
                "query": "latest Python 3.14 release",
            },
        )
    )

    assert result.output["url"] == "https://www.python.org/downloads/"
    assert result.output["content"] == "Python 3.14.6 is the latest release."
    datetime.fromisoformat(result.output["fetched_at"])
    assert captured["url"] == "https://api.tavily.com/extract"
    assert captured["payload"] == {
        "urls": ["https://www.python.org/downloads/"],
        "extract_depth": "basic",
        "include_images": False,
        "format": "markdown",
    }
    assert captured["timeout"] == 9
    assert "secret-search-key" not in json.dumps(result.output)

    with pytest.raises(ToolExecutionError, match="must use HTTP or HTTPS"):
        gateway.execute_request(
            ToolGatewayRequest(
                tenant_id="tenant_1",
                workspace_id="workspace_1",
                user_id="user_1",
                run_id="run_1",
                step_id="step_2",
                tool_name="web.fetch",
                tool_input={"url": "file:///etc/passwd"},
            )
        )


def test_e2b_adapter_executes_the_existing_sandbox_contract(monkeypatch):
    import taroai.sandbox.e2b as e2b_module

    class Files:
        def __init__(self):
            self.values = {}

        def write(self, path, data, **_kwargs):
            self.values[path] = bytes(data)

        def read(self, path, **_kwargs):
            return self.values[path]

        def remove(self, path, **_kwargs):
            self.values.pop(path, None)

        def list(self, _path, **_kwargs):
            return [
                SimpleNamespace(
                    path=path,
                    size=len(value),
                    type=SimpleNamespace(value="file"),
                )
                for path, value in self.values.items()
            ]

    class Commands:
        timeouts = []

        def __init__(self):
            self.next_pid = 1000
            self.block_commands = set()
            self.started = threading.Event()
            self.cancelled = threading.Event()
            self.killed_pids = []

        def run(self, command, **_kwargs):
            self.timeouts.append(_kwargs.get("timeout"))
            if not _kwargs.get("background"):
                return SimpleNamespace(exit_code=0, stdout=f"ran:{command}", stderr="")
            pid = self.next_pid
            self.next_pid += 1
            commands = self

            class Handle:
                def __init__(self):
                    self.pid = pid

                def wait(self):
                    if command in commands.block_commands:
                        commands.started.set()
                        commands.cancelled.wait(timeout=2)
                    exit_code = 137 if pid in commands.killed_pids else 0
                    return SimpleNamespace(
                        exit_code=exit_code,
                        stdout=f"ran:{command}" if exit_code == 0 else "",
                        stderr="" if exit_code == 0 else "killed",
                    )

                def kill(self):
                    return commands.kill(pid)

            return Handle()

        def kill(self, pid, **_kwargs):
            self.killed_pids.append(pid)
            self.cancelled.set()
            return True

    class Paginator:
        def __init__(self, values):
            self.values = values
            self.has_next = True

        def next_items(self):
            self.has_next = False
            return self.values

    class FakeSandbox:
        instances = {}
        infos = {}
        templates = []
        allow_internet_access = None
        paused = set()
        pause_api_key = None
        pause_request_timeout = None
        info_request_timeout = None
        connect_count = 0

        def __init__(self, sandbox_id):
            self.sandbox_id = sandbox_id
            self.commands = Commands()
            self.files = Files()

        @classmethod
        def create(cls, metadata, allow_internet_access, **_kwargs):
            sandbox = cls("e2b_sandbox_1")
            cls.instances[sandbox.sandbox_id] = sandbox
            cls.allow_internet_access = allow_internet_access
            cls.templates.append(_kwargs.get("template"))
            cls.infos[sandbox.sandbox_id] = SimpleNamespace(
                sandbox_id=sandbox.sandbox_id,
                template_id=_kwargs.get("template") or "base",
                metadata=metadata,
                started_at=datetime.now(timezone.utc),
            )
            return sandbox

        @classmethod
        def connect(cls, sandbox_id, **_kwargs):
            cls.connect_count += 1
            return cls.instances[sandbox_id]

        @classmethod
        def list(cls, query, **_kwargs):
            values = [
                info
                for info in cls.infos.values()
                if all(info.metadata.get(key) == value for key, value in query.metadata.items())
            ]
            return Paginator(values)

        def get_info(self_or_id, **_kwargs):
            sandbox_id = getattr(self_or_id, "sandbox_id", self_or_id)
            FakeSandbox.info_request_timeout = _kwargs.get("request_timeout")
            return FakeSandbox.infos[sandbox_id]

        def kill(self_or_id, **_kwargs):
            sandbox_id = getattr(self_or_id, "sandbox_id", self_or_id)
            FakeSandbox.instances.pop(sandbox_id, None)
            FakeSandbox.infos.pop(sandbox_id, None)
            return True

        def beta_pause(self_or_id, api_key, **_kwargs):
            sandbox_id = getattr(self_or_id, "sandbox_id", self_or_id)
            FakeSandbox.paused.add(sandbox_id)
            FakeSandbox.pause_api_key = api_key
            FakeSandbox.pause_request_timeout = _kwargs.get("request_timeout")

    monkeypatch.setattr(e2b_module, "Sandbox", FakeSandbox)
    settings = Settings(
        sandbox_provider="e2b",
        e2b_api_key="secret-e2b-key",
        e2b_template="global-template",
        sandbox_runtime_image="configured-runtime-image",
    )
    adapter = build_sandbox_adapter(settings)
    assert isinstance(adapter, E2BSandboxAdapter)
    assert adapter.get_capabilities().command_cancellation_supported is True

    session = adapter.create(
        SandboxCreateRequest(
            tenant_id="tenant_1",
            workspace_id="workspace_1",
            run_id="run_1",
            thread_id="thread_1",
            metadata={"taroai_tenant_id": "spoofed_tenant"},
        )
    )
    result = adapter.execute(
        SandboxCommand(
            tenant_id="tenant_1",
            workspace_id="workspace_1",
            run_id="run_1",
            session_id=session.id,
            command="printf ok",
            timeout_seconds=600,
        )
    )
    uploaded = adapter.upload_file(
        SandboxFileWrite(
            tenant_id="tenant_1",
            workspace_id="workspace_1",
            run_id="run_1",
            session_id=session.id,
            path="artifacts/report.txt",
            content="report",
        )
    )

    assert result.exit_code == 0
    assert FakeSandbox.templates == ["global-template"]
    assert session.image == "global-template"
    assert Commands.timeouts[-1] == session.timeout_seconds == 300
    assert session.tenant_id == "tenant_1"
    assert result.stdout == "ran:printf ok"
    assert uploaded.path == "/workspace/artifacts/report.txt"
    assert adapter.download_file(
        "tenant_1", session.id, uploaded.path
    ).content == "report"
    binary_content = b"\x89PNG\r\n\x1a\n\x00\xff"
    binary_file = adapter.upload_file(
        SandboxFileWrite(
            tenant_id="tenant_1",
            workspace_id="workspace_1",
            run_id="run_1",
            session_id=session.id,
            path="artifacts/image.png",
            content_base64=base64.b64encode(binary_content).decode("ascii"),
            content_type="image/png",
        )
    )
    assert binary_file.content_bytes() == binary_content
    binary_download = adapter.download_file("tenant_1", session.id, binary_file.path)
    assert binary_download.content is None
    assert binary_download.content_bytes() == binary_content
    assert [item.path for item in adapter.list_files("tenant_1", session.id)] == [
        "/workspace/artifacts/report.txt",
        "/workspace/artifacts/image.png",
    ]
    assert adapter.snapshot("tenant_1", session.id).uri == (
        f"e2b://sandboxes/{session.id}"
    )
    assert FakeSandbox.allow_internet_access is False
    resumed_result = adapter.execute(
        SandboxCommand(
            tenant_id="tenant_1",
            workspace_id="workspace_1",
            run_id="run_2",
            thread_id="thread_1",
            session_id=session.id,
            command="printf reused",
        )
    )
    assert resumed_result.run_id == "run_2"
    with pytest.raises(NotFoundError, match="Sandbox session not found"):
        adapter.execute(
            SandboxCommand(
                tenant_id="tenant_1",
                workspace_id="workspace_1",
                run_id="run_2",
                thread_id="thread_other",
                session_id=session.id,
                command="printf denied",
            )
        )

    remote_sandbox = FakeSandbox.instances[session.id]
    remote_sandbox.commands.block_commands.add("sleep 60")
    execution = {}

    def execute_blocked_command():
        execution["result"] = adapter.execute(
            SandboxCommand(
                id="step_cancel",
                tenant_id="tenant_1",
                workspace_id="workspace_1",
                run_id="run_1",
                session_id=session.id,
                command="sleep 60",
            )
        )

    worker = threading.Thread(target=execute_blocked_command)
    worker.start()
    assert remote_sandbox.commands.started.wait(timeout=2)
    cancelling_adapter = E2BSandboxAdapter(api_key="secret-e2b-key")
    assert cancelling_adapter.cancel_command(
        "tenant_1", session.id, "step_cancel"
    ) is True
    worker.join(timeout=2)

    assert worker.is_alive() is False
    assert execution["result"].exit_code == 137
    assert remote_sandbox.commands.killed_pids == [1002]
    assert session.id in FakeSandbox.instances
    assert not any(
        path.startswith("/tmp/taroai-command-")
        for path in remote_sandbox.files.values
    )
    with pytest.raises(NotFoundError, match="Sandbox session not found"):
        adapter.pause("tenant_other", session.id)
    assert FakeSandbox.paused == set()

    connect_count = FakeSandbox.connect_count
    adapter.pause("tenant_1", session.id)
    assert FakeSandbox.paused == {session.id}
    assert FakeSandbox.pause_api_key == "secret-e2b-key"
    assert FakeSandbox.info_request_timeout == 5
    assert FakeSandbox.pause_request_timeout == 5
    assert FakeSandbox.connect_count == connect_count
    with pytest.raises(SandboxExecutionError, match="outside sandbox workspace"):
        adapter.upload_file(
            SandboxFileWrite(
                tenant_id="tenant_1",
                workspace_id="workspace_1",
                run_id="run_1",
                session_id=session.id,
                path="../escape.txt",
                content="blocked",
            )
        )
    assert adapter.destroy("tenant_1", session.id).status.value == "destroyed"

    skill_session = adapter.create(
        SandboxCreateRequest(
            tenant_id="tenant_1",
            workspace_id="workspace_1",
            run_id="run_skill",
            image="skill-template",
        )
    )
    assert FakeSandbox.templates[-1] == "skill-template"
    assert skill_session.image == "skill-template"
    adapter.destroy("tenant_1", skill_session.id)

    stale = FakeSandbox("stale_sandbox")
    FakeSandbox.instances[stale.sandbox_id] = stale
    FakeSandbox.infos[stale.sandbox_id] = SimpleNamespace(
        sandbox_id=stale.sandbox_id,
        template_id="base",
        metadata={
            "taroai": "1",
            "taroai_tenant_id": "tenant_1",
            "taroai_workspace_id": "workspace_1",
            "taroai_run_id": "stale_run",
            "taroai_network_mode": "disabled",
            "taroai_timeout_seconds": "60",
        },
        started_at=datetime.now(timezone.utc) - timedelta(minutes=2),
    )
    expiring_adapter = E2BSandboxAdapter(
        api_key="secret-e2b-key",
        max_session_ttl_seconds=60,
        max_sessions=1,
        max_sessions_per_tenant=1,
        max_sessions_per_run=1,
    )
    replacement = expiring_adapter.create(
        SandboxCreateRequest(
            tenant_id="tenant_1",
            workspace_id="workspace_1",
            run_id="run_2",
            timeout_seconds=60,
        )
    )
    assert replacement.id == "e2b_sandbox_1"
    assert "stale_sandbox" not in FakeSandbox.infos
