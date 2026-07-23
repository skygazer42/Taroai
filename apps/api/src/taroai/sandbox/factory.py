from pathlib import Path
from typing import Any

from taroai.sandbox.adapter import SandboxAdapter
from taroai.sandbox.docker import DockerSandboxAdapter
from taroai.sandbox.http import HttpSandboxAdapter
from taroai.sandbox.process import LocalProcessSandboxAdapter


def build_sandbox_adapter(settings: Any) -> SandboxAdapter:
    if settings.sandbox_provider == "local_process":
        return LocalProcessSandboxAdapter(
            root_dir=Path(settings.sandbox_root_dir),
            max_sessions=settings.sandbox_max_sessions,
            max_sessions_per_tenant=settings.sandbox_max_sessions_per_tenant,
            max_sessions_per_run=settings.sandbox_max_sessions_per_run,
        )
    if settings.sandbox_provider == "docker":
        return DockerSandboxAdapter(
            root_dir=Path(settings.sandbox_root_dir),
            memory_limit=settings.sandbox_docker_memory_limit,
            cpus=settings.sandbox_docker_cpus,
            pids_limit=settings.sandbox_docker_pids_limit,
            max_sessions=settings.sandbox_max_sessions,
            max_sessions_per_tenant=settings.sandbox_max_sessions_per_tenant,
            max_sessions_per_run=settings.sandbox_max_sessions_per_run,
            container_user=settings.sandbox_docker_user,
            read_only_rootfs=settings.sandbox_docker_read_only_rootfs,
            drop_all_capabilities=settings.sandbox_docker_drop_all_capabilities,
            security_opts=settings.sandbox_docker_security_opts,
            tmpfs_mounts=settings.sandbox_docker_tmpfs_mounts,
        )
    if settings.sandbox_provider == "e2b" and settings.e2b_api_key:
        from taroai.sandbox.e2b import E2BSandboxAdapter

        return E2BSandboxAdapter(
            api_key=settings.e2b_api_key,
            template=settings.e2b_template,
            default_runtime_image=settings.sandbox_runtime_image,
            request_timeout_seconds=settings.e2b_request_timeout_seconds,
            max_session_ttl_seconds=settings.e2b_max_session_ttl_seconds,
            max_sessions=settings.sandbox_max_sessions,
            max_sessions_per_tenant=settings.sandbox_max_sessions_per_tenant,
            max_sessions_per_run=settings.sandbox_max_sessions_per_run,
        )
    if settings.sandbox_provider in {"k8s", "e2b"}:
        return HttpSandboxAdapter(
            provider=settings.sandbox_provider,
            base_url=settings.sandbox_controller_base_url,
            api_key=settings.sandbox_controller_api_key,
            timeout_seconds=settings.sandbox_controller_timeout_seconds,
        )
    return SandboxAdapter(provider=settings.sandbox_provider)
