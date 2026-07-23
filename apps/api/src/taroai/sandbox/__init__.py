from taroai.sandbox.adapter import (
    SandboxAdapter,
    SandboxExecutionError,
    SandboxProviderUnavailableError,
)
from taroai.sandbox.browser import (
    BrowserController,
    BrowserControllerCapabilities,
    BrowserProviderUnavailableError,
    HttpBrowserController,
    PlaywrightBrowserController,
)
from taroai.sandbox.factory import build_sandbox_adapter
from taroai.sandbox.http import HttpSandboxAdapter
from taroai.sandbox.kubernetes import KubernetesSandboxAdapter
from taroai.sandbox.models import (
    BrowserAction,
    BrowserActionRequest,
    BrowserActionType,
    BrowserObservation,
    BrowserSession,
    SandboxCommand,
    SandboxCommandRequest,
    SandboxCommandResult,
    SandboxCommandStatus,
    SandboxControllerCapabilities,
    SandboxCreateRequest,
    SandboxFileRef,
    SandboxFileWrite,
    SandboxFileWriteRequest,
    SandboxNetworkMode,
    SandboxSession,
    SandboxSessionCreateRequest,
    SandboxSessionStatus,
    SandboxSnapshot,
)
from taroai.sandbox.docker import DockerSandboxAdapter
from taroai.sandbox.process import LocalProcessSandboxAdapter


def register_sandbox_tool_handlers(*args, **kwargs):
    from taroai.sandbox.tools import register_sandbox_tool_handlers as register

    return register(*args, **kwargs)


def register_browser_tool_handlers(*args, **kwargs):
    from taroai.sandbox.tools import register_browser_tool_handlers as register

    return register(*args, **kwargs)


def __getattr__(name: str):
    if name == "E2BSandboxAdapter":
        from taroai.sandbox.e2b import E2BSandboxAdapter

        return E2BSandboxAdapter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BrowserAction",
    "BrowserActionRequest",
    "BrowserActionType",
    "BrowserController",
    "BrowserControllerCapabilities",
    "BrowserObservation",
    "BrowserProviderUnavailableError",
    "BrowserSession",
    "DockerSandboxAdapter",
    "E2BSandboxAdapter",
    "HttpBrowserController",
    "HttpSandboxAdapter",
    "KubernetesSandboxAdapter",
    "LocalProcessSandboxAdapter",
    "PlaywrightBrowserController",
    "SandboxAdapter",
    "SandboxCommand",
    "SandboxCommandRequest",
    "SandboxCommandResult",
    "SandboxCommandStatus",
    "SandboxControllerCapabilities",
    "SandboxCreateRequest",
    "SandboxExecutionError",
    "SandboxFileRef",
    "SandboxFileWrite",
    "SandboxFileWriteRequest",
    "SandboxNetworkMode",
    "SandboxProviderUnavailableError",
    "SandboxSession",
    "SandboxSessionCreateRequest",
    "SandboxSessionStatus",
    "SandboxSnapshot",
    "build_sandbox_adapter",
    "register_sandbox_tool_handlers",
    "register_browser_tool_handlers",
]
