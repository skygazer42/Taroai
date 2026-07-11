from pathlib import Path


PLACEHOLDER_FLOW_TERMS = [
    "Mock",
    "mock",
    "Fake",
    "fake",
    "Dummy",
    "dummy",
    "Stub",
    "stub",
    "Deterministic",
    "deterministic",
    "MockModelProvider",
    "tests.api.adapters",
]


HISTORICAL_PLACEHOLDER_PHRASES = [
    "production detector adapters",
    "artifact/memory",
    "memory-write approval resume",
    "memory-write guardrail approval resume",
    "memory-level approval-required",
    "short-term memory approval workflow",
    "durable short-term memory review storage",
    "external detector adapters",
    "broader prompt-injection/exfiltration detectors",
    "Dedicated prompt-injection/exfiltration detectors",
    "run-trace finding presentation",
    "Runtime step spans, trace exporters",
    "trace exporters remain implementation work",
    "Trace exporters,",
]


FLOW_TEXT_SUFFIXES = {
    "",
    ".env",
    ".example",
    ".json",
    ".md",
    ".py",
    ".sql",
    ".yaml",
    ".yml",
}


def find_forbidden_terms(paths: list[Path], terms: list[str]) -> list[str]:
    return [
        f"{path}:{term}"
        for path in paths
        for term in terms
        if term in path.read_text(encoding="utf-8")
    ]


def is_flow_text_file(path: Path) -> bool:
    return (
        path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix in FLOW_TEXT_SUFFIXES
    )


def test_backend_contexts_are_split_into_packages():
    root = Path("apps/api/src/taroai")

    expected_files = [
        root / "agent" / "__init__.py",
        root / "agent" / "runtime.py",
        root / "agent" / "state.py",
        root / "agent" / "planning.py",
        root / "agent" / "tools.py",
        root / "agent" / "graph.py",
        root / "model_gateway" / "__init__.py",
        root / "model_gateway" / "models.py",
        root / "model_gateway" / "gateway.py",
        root / "tool_gateway" / "__init__.py",
        root / "tool_gateway" / "models.py",
        root / "tool_gateway" / "schema.py",
        root / "tool_gateway" / "service.py",
        root / "connectors" / "__init__.py",
        root / "connectors" / "models.py",
        root / "connectors" / "service.py",
        root / "connectors" / "repository.py",
        root / "connectors" / "sync.py",
        root / "connectors" / "invocation.py",
        root / "connectors" / "dispatch.py",
        root / "audit" / "__init__.py",
        root / "audit" / "models.py",
        root / "audit" / "service.py",
        root / "knowledge" / "__init__.py",
        root / "knowledge" / "models.py",
        root / "knowledge" / "service.py",
        root / "knowledge" / "repository.py",
        root / "knowledge" / "retrieval.py",
        root / "sandbox" / "__init__.py",
        root / "sandbox" / "models.py",
        root / "sandbox" / "adapter.py",
        root / "sandbox" / "browser.py",
        root / "sandbox" / "factory.py",
        root / "sandbox" / "tools.py",
        root / "sandbox" / "process.py",
        root / "db" / "__init__.py",
        root / "db" / "models.py",
        root / "db" / "migrations.py",
        root / "db" / "repository.py",
        root / "skills" / "__init__.py",
        root / "skills" / "manifest.py",
        root / "skills" / "registry.py",
        root / "skills" / "repository.py",
        root / "memory" / "__init__.py",
        root / "memory" / "models.py",
        root / "memory" / "service.py",
        root / "memory" / "repository.py",
        root / "storage" / "__init__.py",
        root / "storage" / "models.py",
        root / "storage" / "catalog.py",
        root / "storage" / "repository.py",
        root / "storage" / "adapter.py",
        root / "identity" / "__init__.py",
        root / "identity" / "models.py",
        root / "identity" / "service.py",
        root / "identity" / "repository.py",
        root / "policy" / "__init__.py",
        root / "policy" / "models.py",
        root / "policy" / "service.py",
        root / "auth" / "__init__.py",
        root / "auth" / "models.py",
        root / "auth" / "service.py",
        root / "auth" / "sessions.py",
        root / "secrets" / "__init__.py",
        root / "secrets" / "models.py",
        root / "secrets" / "service.py",
        root / "licensing" / "__init__.py",
        root / "licensing" / "models.py",
        root / "licensing" / "service.py",
        root / "licensing" / "signing.py",
        root / "deployment" / "__init__.py",
        root / "deployment" / "models.py",
        root / "deployment" / "validation.py",
        root / "onboarding" / "__init__.py",
        root / "onboarding" / "models.py",
        root / "onboarding" / "readiness.py",
        root / "triggers" / "__init__.py",
        root / "triggers" / "models.py",
        root / "triggers" / "repository.py",
        root / "triggers" / "scheduler.py",
        root / "triggers" / "service.py",
        root / "workers" / "__init__.py",
        root / "workers" / "models.py",
        root / "workers" / "queue.py",
        root / "workers" / "agent_worker.py",
        root / "workers" / "billing_worker.py",
        root / "workers" / "scheduler_worker.py",
        root / "workers" / "trigger_worker.py",
        root / "workers" / "runner.py",
    ]

    missing = [str(path) for path in expected_files if not path.exists()]

    assert missing == []


def test_runtime_is_not_kept_as_a_top_level_monolith():
    assert not Path("apps/api/src/taroai/runtime.py").exists()


def test_product_source_does_not_reference_test_adapters():
    root = Path("apps/api/src/taroai")
    source_paths = sorted(root.rglob("*.py"))
    violations = find_forbidden_terms(source_paths, PLACEHOLDER_FLOW_TERMS)

    assert violations == []


def test_runtime_and_delivery_flow_do_not_reference_placeholder_components():
    source_paths = [
        path
        for root in [
            Path("apps/api/src/taroai"),
            Path("docs"),
            Path("infra"),
        ]
        for path in sorted(root.rglob("*"))
        if is_flow_text_file(path) and Path("docs/plans") not in path.parents
    ]
    violations = find_forbidden_terms(
        source_paths,
        PLACEHOLDER_FLOW_TERMS + HISTORICAL_PLACEHOLDER_PHRASES,
    )

    assert violations == []


def test_sandbox_local_adapters_are_not_product_flow_components():
    root = Path("apps/api/src/taroai/sandbox")
    source_paths = sorted(root.rglob("*.py"))
    forbidden_terms = [
        "InMemorySandboxAdapter",
        "InMemoryBrowserController",
    ]
    violations = [
        f"{path}:{term}"
        for path in source_paths
        for term in forbidden_terms
        if term in path.read_text()
    ]

    assert violations == []


def test_sandbox_provider_modules_use_lightweight_shared_errors():
    sandbox_root = Path("apps/api/src/taroai/sandbox")
    source_paths = [
        sandbox_root / "__init__.py",
        sandbox_root / "browser.py",
        sandbox_root / "docker.py",
        sandbox_root / "playwright_service.py",
        sandbox_root / "process.py",
    ]
    violations = [
        str(path)
        for path in source_paths
        if "from taroai.store import" in path.read_text()
    ]

    assert violations == []


def test_restore_drill_evidence_validation_lives_in_lifecycle_package():
    app_source = Path("apps/api/src/taroai/app.py").read_text()
    lifecycle_source = Path("apps/api/src/taroai/lifecycle/restore_drill.py").read_text()

    assert "RestoreDrillVerificationResult.model_validate_json" not in app_source
    assert "def validated_restore_drill_evidence_object_id" not in app_source
    assert "RestoreDrillEvidenceValidationRequest" in lifecycle_source
    assert "validate_restore_drill_evidence_object" in lifecycle_source
