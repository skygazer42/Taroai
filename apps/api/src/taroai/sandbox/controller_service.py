from contextlib import asynccontextmanager
from pathlib import Path
from secrets import compare_digest

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from taroai.domain import utc_now
from taroai.errors import NotFoundError
from taroai.sandbox.adapter import (
    SandboxAdapter,
    SandboxExecutionError,
    SandboxProviderUnavailableError,
)
from taroai.sandbox.docker import DockerSandboxAdapter
from taroai.sandbox.image_policy import (
    sandbox_runtime_allowed_image_policy_failure_details,
    sandbox_runtime_normalize_allowed_images,
)
from taroai.sandbox.kubernetes import KubernetesSandboxAdapter
from taroai.sandbox.models import (
    SandboxCommand,
    SandboxCommandResult,
    SandboxControllerCapabilities,
    SandboxCreateRequest,
    SandboxFileRef,
    SandboxFileWrite,
    SandboxSession,
    SandboxSessionStatus,
    SandboxSnapshot,
)


SANDBOX_CONTROLLER_API_KEY_MIN_LENGTH = 32


class SandboxSnapshotRequest(BaseModel):
    tenant_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class SandboxControllerServiceSettings(BaseSettings):
    api_key: str = Field(default="", repr=False)
    provider: str = Field(default="docker", min_length=1)
    root_dir: Path = Path("/data/taroai/sandboxes")
    session_ttl_seconds: int = Field(default=1800, ge=0)
    max_sessions: int = Field(default=50, ge=1)
    max_sessions_per_tenant: int = Field(default=20, ge=1)
    max_sessions_per_run: int = Field(default=3, ge=1)
    docker_memory_limit: str = Field(default="1g", min_length=1)
    docker_cpus: float = Field(default=1.0, gt=0)
    docker_pids_limit: int = Field(default=256, ge=1)
    docker_user: str = Field(default="65532:65532", min_length=1)
    docker_read_only_rootfs: bool = True
    docker_drop_all_capabilities: bool = True
    docker_security_opts: list[str] = Field(
        default_factory=lambda: ["no-new-privileges:true"]
    )
    docker_tmpfs_mounts: list[str] = Field(
        default_factory=lambda: ["/tmp:rw,noexec,nosuid,size=256m"]
    )
    kubernetes_namespace: str = Field(default="default", min_length=1)
    kubernetes_kubectl_binary: str = Field(default="kubectl", min_length=1)
    kubernetes_service_account_name: str = Field(
        default="sandbox-runner",
        min_length=1,
    )
    kubernetes_runtime_class_name: str = ""
    kubernetes_runtime_class_required: bool = False
    kubernetes_allowed_images: list[str] = Field(default_factory=list)
    kubernetes_orphan_cleanup_enabled: bool = False
    kubernetes_image_pull_policy: str = Field(default="IfNotPresent", min_length=1)
    kubernetes_pod_ready_timeout_seconds: int = Field(default=60, ge=1)
    kubernetes_memory_limit: str = Field(default="1Gi", min_length=1)
    kubernetes_cpu_limit: str = Field(default="1000m", min_length=1)
    kubernetes_ephemeral_storage_limit: str = Field(default="2Gi", min_length=1)
    kubernetes_run_as_user: int = Field(default=65532, ge=1)
    kubernetes_run_as_group: int = Field(default=65532, ge=1)

    model_config = SettingsConfigDict(
        env_prefix="TAROAI_SANDBOX_CONTROLLER_",
        extra="forbid",
    )

    @field_validator("api_key")
    @classmethod
    def validate_api_key_length(cls, value: str) -> str:
        if value.strip() and len(value.strip()) < SANDBOX_CONTROLLER_API_KEY_MIN_LENGTH:
            raise ValueError(
                f"api_key must be at least {SANDBOX_CONTROLLER_API_KEY_MIN_LENGTH} characters when configured"
            )
        return value

    @model_validator(mode="after")
    def validate_kubernetes_image_policy(self) -> "SandboxControllerServiceSettings":
        if self.provider not in {"kubernetes", "k8s"}:
            return self
        if not self.kubernetes_runtime_class_required:
            raise ValueError(
                "kubernetes_runtime_class_required must be true for kubernetes provider"
            )
        runtime_class_name = self.kubernetes_runtime_class_name.strip()
        if not runtime_class_name:
            raise ValueError(
                "kubernetes_runtime_class_name is required when kubernetes provider is enabled"
            )
        self.kubernetes_runtime_class_name = runtime_class_name
        details = sandbox_runtime_allowed_image_policy_failure_details(
            self.kubernetes_allowed_images,
            context="kubernetes_allowed_images",
        )
        if details:
            raise ValueError("; ".join(details))
        allowed_images = sandbox_runtime_normalize_allowed_images(
            self.kubernetes_allowed_images
        )
        self.kubernetes_allowed_images = allowed_images
        return self

    @classmethod
    def from_env(cls) -> "SandboxControllerServiceSettings":
        return cls()


class SandboxControllerRuntime(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    known_tenant_ids: set[str] = Field(default_factory=set)


def create_sandbox_controller_app(
    adapter: SandboxAdapter | None = None,
    settings: SandboxControllerServiceSettings | None = None,
) -> FastAPI:
    service_settings = settings or SandboxControllerServiceSettings.from_env()
    sandbox_adapter = adapter or build_sandbox_controller_adapter(service_settings)
    runtime = SandboxControllerRuntime()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            close = getattr(sandbox_adapter, "close", None)
            if callable(close):
                close()

    sandbox_app = FastAPI(title="Taroai Sandbox Controller", lifespan=lifespan)
    sandbox_app.state.sandbox_adapter = sandbox_adapter
    sandbox_app.state.settings = service_settings
    sandbox_app.state.runtime = runtime

    def require_controller_auth(
        authorization: str | None = Header(default=None),
    ) -> None:
        expected_api_key = service_settings.api_key.strip()
        if not expected_api_key:
            return
        prefix = "Bearer "
        if authorization is None or not authorization.startswith(prefix):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="sandbox controller authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        provided_api_key = authorization[len(prefix):]
        if not compare_digest(provided_api_key, expected_api_key):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="sandbox controller authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )

    def prepare_controller_request(
        _auth: None = Depends(require_controller_auth),
    ) -> None:
        cleanup_expired_sandbox_sessions(
            sandbox_adapter,
            service_settings,
            runtime,
        )
        cleanup_orphaned_sandbox_sessions(
            sandbox_adapter,
            service_settings,
            runtime,
        )

    @sandbox_app.exception_handler(NotFoundError)
    async def not_found_handler(_request, error: NotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))

    @sandbox_app.exception_handler(SandboxExecutionError)
    async def execution_error_handler(_request, error: SandboxExecutionError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        )

    @sandbox_app.exception_handler(SandboxProviderUnavailableError)
    async def provider_error_handler(_request, error: SandboxProviderUnavailableError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        )

    @sandbox_app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "taroai-sandbox-controller"}

    @sandbox_app.get("/capabilities")
    def capabilities(
        _request: None = Depends(prepare_controller_request),
    ) -> SandboxControllerCapabilities:
        return sandbox_controller_capabilities(service_settings)

    @sandbox_app.post("/sessions", status_code=status.HTTP_201_CREATED)
    def create_session(
        request: SandboxCreateRequest,
        _request: None = Depends(prepare_controller_request),
    ) -> SandboxSession:
        runtime.known_tenant_ids.add(request.tenant_id)
        cleanup_expired_sandbox_sessions(
            sandbox_adapter,
            service_settings,
            runtime,
        )
        enforce_sandbox_session_limits(
            sandbox_adapter,
            service_settings,
            runtime,
            request,
        )
        session = sandbox_adapter.create(request)
        return session

    @sandbox_app.get("/sessions")
    def list_sessions(
        tenant_id: str | None = Query(default=None, min_length=1),
        _request: None = Depends(prepare_controller_request),
    ) -> dict[str, list[SandboxSession]]:
        if tenant_id is not None:
            runtime.known_tenant_ids.add(tenant_id)
            return {"sessions": sandbox_adapter.list_sessions(tenant_id)}
        return {"sessions": list_known_sandbox_sessions(sandbox_adapter, runtime)}

    @sandbox_app.get("/sessions/{session_id}")
    def get_session(
        session_id: str,
        tenant_id: str = Query(min_length=1),
        _request: None = Depends(prepare_controller_request),
    ) -> SandboxSession:
        runtime.known_tenant_ids.add(tenant_id)
        return sandbox_adapter.get_session(tenant_id=tenant_id, session_id=session_id)

    @sandbox_app.delete("/sessions/{session_id}")
    def destroy_session(
        session_id: str,
        tenant_id: str = Query(min_length=1),
        _request: None = Depends(prepare_controller_request),
    ) -> SandboxSession:
        runtime.known_tenant_ids.add(tenant_id)
        return sandbox_adapter.destroy(tenant_id=tenant_id, session_id=session_id)

    @sandbox_app.post("/commands")
    def execute_command(
        command: SandboxCommand,
        _request: None = Depends(prepare_controller_request),
    ) -> SandboxCommandResult:
        runtime.known_tenant_ids.add(command.tenant_id)
        require_sandbox_session_scope(
            sandbox_adapter,
            tenant_id=command.tenant_id,
            session_id=command.session_id,
            workspace_id=command.workspace_id,
            run_id=command.run_id,
        )
        return sandbox_adapter.execute(command)

    @sandbox_app.post("/files", status_code=status.HTTP_201_CREATED)
    def upload_file(
        file_write: SandboxFileWrite,
        _request: None = Depends(prepare_controller_request),
    ) -> SandboxFileRef:
        runtime.known_tenant_ids.add(file_write.tenant_id)
        require_sandbox_session_scope(
            sandbox_adapter,
            tenant_id=file_write.tenant_id,
            session_id=file_write.session_id,
            workspace_id=file_write.workspace_id,
            run_id=file_write.run_id,
        )
        return sandbox_adapter.upload_file(file_write)

    @sandbox_app.get("/files")
    def get_files(
        tenant_id: str = Query(min_length=1),
        session_id: str = Query(min_length=1),
        workspace_id: str = Query(min_length=1),
        run_id: str = Query(min_length=1),
        path: str | None = Query(default=None),
        _request: None = Depends(prepare_controller_request),
    ) -> SandboxFileRef | dict[str, list[SandboxFileRef]]:
        runtime.known_tenant_ids.add(tenant_id)
        require_sandbox_session_scope(
            sandbox_adapter,
            tenant_id=tenant_id,
            session_id=session_id,
            workspace_id=workspace_id,
            run_id=run_id,
        )
        if path is not None:
            return sandbox_adapter.download_file(
                tenant_id=tenant_id,
                session_id=session_id,
                path=path,
            )
        return {
            "files": sandbox_adapter.list_files(
                tenant_id=tenant_id,
                session_id=session_id,
            )
        }

    @sandbox_app.post("/snapshots", status_code=status.HTTP_201_CREATED)
    def create_snapshot(
        request: SandboxSnapshotRequest,
        _request: None = Depends(prepare_controller_request),
    ) -> SandboxSnapshot:
        runtime.known_tenant_ids.add(request.tenant_id)
        require_sandbox_session_scope(
            sandbox_adapter,
            tenant_id=request.tenant_id,
            session_id=request.session_id,
            workspace_id=request.workspace_id,
            run_id=request.run_id,
        )
        return sandbox_adapter.snapshot(
            tenant_id=request.tenant_id,
            session_id=request.session_id,
        )

    return sandbox_app


def build_sandbox_controller_adapter(
    settings: SandboxControllerServiceSettings,
) -> SandboxAdapter:
    if settings.provider in {"kubernetes", "k8s"}:
        return KubernetesSandboxAdapter(
            provider="kubernetes",
            namespace=settings.kubernetes_namespace,
            root_dir=settings.root_dir,
            kubectl_binary=settings.kubernetes_kubectl_binary,
            pod_ready_timeout_seconds=settings.kubernetes_pod_ready_timeout_seconds,
            service_account_name=settings.kubernetes_service_account_name,
            runtime_class_name=settings.kubernetes_runtime_class_name,
            runtime_class_required=settings.kubernetes_runtime_class_required,
            allowed_images=settings.kubernetes_allowed_images,
            image_pull_policy=settings.kubernetes_image_pull_policy,
            memory_limit=settings.kubernetes_memory_limit,
            cpu_limit=settings.kubernetes_cpu_limit,
            ephemeral_storage_limit=settings.kubernetes_ephemeral_storage_limit,
            max_session_ttl_seconds=settings.session_ttl_seconds,
            max_sessions=settings.max_sessions,
            max_sessions_per_tenant=settings.max_sessions_per_tenant,
            max_sessions_per_run=settings.max_sessions_per_run,
            run_as_user=settings.kubernetes_run_as_user,
            run_as_group=settings.kubernetes_run_as_group,
        )
    return DockerSandboxAdapter(
        provider=settings.provider,
        root_dir=settings.root_dir,
        memory_limit=settings.docker_memory_limit,
        cpus=settings.docker_cpus,
        pids_limit=settings.docker_pids_limit,
        max_sessions=settings.max_sessions,
        max_sessions_per_tenant=settings.max_sessions_per_tenant,
        max_sessions_per_run=settings.max_sessions_per_run,
        container_user=settings.docker_user,
        read_only_rootfs=settings.docker_read_only_rootfs,
        drop_all_capabilities=settings.docker_drop_all_capabilities,
        security_opts=settings.docker_security_opts,
        tmpfs_mounts=settings.docker_tmpfs_mounts,
    )


def require_sandbox_session_scope(
    adapter: SandboxAdapter,
    tenant_id: str,
    session_id: str,
    workspace_id: str,
    run_id: str,
) -> None:
    session = require_active_sandbox_session(
        adapter,
        tenant_id=tenant_id,
        session_id=session_id,
    )
    if session.workspace_id != workspace_id or session.run_id != run_id:
        raise NotFoundError(f"Sandbox session not found: {session_id}")


def require_active_sandbox_session(
    adapter: SandboxAdapter,
    tenant_id: str,
    session_id: str,
) -> SandboxSession:
    session = adapter.get_session(tenant_id=tenant_id, session_id=session_id)
    if session.status != SandboxSessionStatus.ACTIVE:
        raise NotFoundError(f"Sandbox session not found: {session_id}")
    return session


def sandbox_controller_capabilities(
    settings: SandboxControllerServiceSettings,
) -> SandboxControllerCapabilities:
    is_kubernetes = settings.provider in {"kubernetes", "k8s"}
    return SandboxControllerCapabilities(
        provider=settings.provider,
        network_isolation=True,
        filesystem_isolation=True,
        resource_limits=True,
        destroy_supported=True,
        session_ttl_enforced=settings.session_ttl_seconds > 0,
        runtime_isolation=(
            is_kubernetes
            and settings.kubernetes_runtime_class_required
            and bool(settings.kubernetes_runtime_class_name.strip())
        ),
        image_policy_enforced=(
            is_kubernetes and bool(settings.kubernetes_allowed_images)
        ),
        allowed_image_count=(
            len(settings.kubernetes_allowed_images) if is_kubernetes else None
        ),
        max_session_ttl_seconds=(
            settings.session_ttl_seconds if settings.session_ttl_seconds > 0 else None
        ),
        max_sessions=settings.max_sessions,
        max_sessions_per_tenant=settings.max_sessions_per_tenant,
        max_sessions_per_run=settings.max_sessions_per_run,
    )


def cleanup_expired_sandbox_sessions(
    adapter: SandboxAdapter,
    settings: SandboxControllerServiceSettings,
    runtime: SandboxControllerRuntime,
) -> None:
    if settings.session_ttl_seconds <= 0:
        return
    now = utc_now()
    for session in list_known_sandbox_sessions(adapter, runtime):
        if session.status != SandboxSessionStatus.ACTIVE:
            continue
        age_seconds = (now - session.created_at).total_seconds()
        if age_seconds <= settings.session_ttl_seconds:
            continue
        try:
            adapter.destroy(tenant_id=session.tenant_id, session_id=session.id)
        except NotFoundError:
            continue


def cleanup_orphaned_sandbox_sessions(
    adapter: SandboxAdapter,
    settings: SandboxControllerServiceSettings,
    runtime: SandboxControllerRuntime,
) -> None:
    if not settings.kubernetes_orphan_cleanup_enabled:
        return
    cleanup_orphaned_sessions = getattr(adapter, "cleanup_orphaned_sessions", None)
    if not callable(cleanup_orphaned_sessions):
        return
    active_session_ids = {
        session.id
        for session in list_known_sandbox_sessions(adapter, runtime)
        if session.status == SandboxSessionStatus.ACTIVE
    }
    if not active_session_ids and not runtime.known_tenant_ids:
        return
    cleanup_orphaned_sessions(known_active_session_ids=active_session_ids)


def enforce_sandbox_session_limits(
    adapter: SandboxAdapter,
    settings: SandboxControllerServiceSettings,
    runtime: SandboxControllerRuntime,
    request: SandboxCreateRequest,
) -> None:
    if settings.session_ttl_seconds > 0 and request.timeout_seconds > settings.session_ttl_seconds:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="sandbox session timeout exceeds controller limit",
        )
    sessions = [
        session
        for session in list_known_sandbox_sessions(adapter, runtime)
        if session.status == SandboxSessionStatus.ACTIVE
    ]
    tenant_sessions = [
        session for session in sessions if session.tenant_id == request.tenant_id
    ]
    run_sessions = [
        session
        for session in tenant_sessions
        if session.run_id == request.run_id
    ]
    if (
        len(sessions) >= settings.max_sessions
        or len(tenant_sessions) >= settings.max_sessions_per_tenant
        or len(run_sessions) >= settings.max_sessions_per_run
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="sandbox session limit reached",
        )


def list_known_sandbox_sessions(
    adapter: SandboxAdapter,
    runtime: SandboxControllerRuntime,
) -> list[SandboxSession]:
    session_by_id: dict[str, SandboxSession] = {}
    try:
        for session in adapter.list_sessions(None):
            session_by_id[session.id] = session
    except TypeError:
        pass
    for tenant_id in sorted(runtime.known_tenant_ids):
        for session in adapter.list_sessions(tenant_id):
            session_by_id[session.id] = session
    return sorted(
        session_by_id.values(),
        key=lambda session: (session.created_at, session.id),
    )


app = create_sandbox_controller_app()
