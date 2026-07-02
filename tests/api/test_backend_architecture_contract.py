from pathlib import Path


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
        root / "sandbox" / "tools.py",
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
        root / "onboarding" / "__init__.py",
        root / "onboarding" / "models.py",
        root / "onboarding" / "readiness.py",
        root / "workers" / "__init__.py",
        root / "workers" / "models.py",
        root / "workers" / "queue.py",
        root / "workers" / "agent_worker.py",
        root / "workers" / "billing_worker.py",
        root / "workers" / "runner.py",
    ]

    missing = [str(path) for path in expected_files if not path.exists()]

    assert missing == []


def test_runtime_is_not_kept_as_a_top_level_monolith():
    assert not Path("apps/api/src/taroai/runtime.py").exists()


def test_product_source_does_not_reference_test_adapters():
    root = Path("apps/api/src/taroai")
    source_paths = sorted(root.rglob("*.py"))
    forbidden_terms = [
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
        "tests.api.adapters",
    ]
    violations = [
        f"{path}:{term}"
        for path in source_paths
        for term in forbidden_terms
        if term in path.read_text()
    ]

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
