from pathlib import Path

from pydantic import BaseModel

from taroai.app import RequestContext
from taroai.config import Settings
from taroai.store import InMemoryControlPlaneStore


def test_backend_management_primitives_are_pydantic_models():
    assert issubclass(Settings, BaseModel)
    assert issubclass(RequestContext, BaseModel)
    assert issubclass(InMemoryControlPlaneStore, BaseModel)


def test_in_memory_store_state_is_declared_as_pydantic_fields():
    assert set(InMemoryControlPlaneStore.model_fields) >= {
        "runs",
        "run_events",
        "artifacts",
        "billing_meters",
        "audit_events",
    }


def test_backend_source_does_not_use_future_annotations_import():
    backend_files = Path("apps/api/src").rglob("*.py")

    offenders = [
        str(path)
        for path in backend_files
        if "from __future__ import annotations" in path.read_text()
    ]

    assert offenders == []
