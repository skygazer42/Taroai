from __future__ import annotations

import difflib
import json
from html import escape
from typing import Any

from taroai.artifacts.models import (
    ArtifactPreview,
    ArtifactRenderPolicy,
    ArtifactDiff,
    ArtifactSource,
    DashboardSpec,
    RichArtifactCreate,
)


class ArtifactService:
    def __init__(self, *, store: Any, storage_catalog: Any, object_storage: Any) -> None:
        self.store = store
        self.storage_catalog = storage_catalog
        self.object_storage = object_storage

    def create(self, tenant_id: str, payload: RichArtifactCreate):
        run = self.store.get_run(tenant_id, payload.run_id)
        storage_object = None
        if payload.storage_object_id is not None:
            storage_object = self.storage_catalog.get(
                tenant_id, payload.storage_object_id
            )
            if storage_object.workspace_id != run.workspace_id:
                raise ValueError("Artifact storage object is outside the Run workspace")
        policy = ArtifactRenderPolicy()
        content_type = payload.content_type or (
            storage_object.content_type if storage_object is not None else "application/json"
        )
        uri = (
            storage_object.uri
            if storage_object is not None
            else f"artifact://{tenant_id}/{run.id}/{payload.name}"
        )
        return self.store.create_artifact(
            tenant_id=tenant_id,
            run_id=run.id,
            name=payload.name,
            artifact_type=payload.artifact_type,
            uri=uri,
            workspace_id=run.workspace_id,
            thread_id=payload.thread_id or run.thread_id,
            message_id=payload.message_id,
            storage_object_id=payload.storage_object_id,
            content_type=content_type,
            size_bytes=storage_object.size_bytes if storage_object is not None else 0,
            preview_payload=payload.preview_payload,
            dashboard_payload=(
                payload.dashboard.model_dump(mode="json")
                if payload.dashboard is not None
                else None
            ),
            render_policy=policy.model_dump(mode="json"),
            metadata=payload.metadata,
        )

    def preview(self, tenant_id: str, artifact_id: str) -> ArtifactPreview:
        artifact = self.store.get_artifact(tenant_id, artifact_id)
        policy = ArtifactRenderPolicy.model_validate(artifact.render_policy or {})
        download_url = f"/api/artifacts/{artifact.id}/download"
        content_type = artifact.content_type or "application/octet-stream"
        if artifact.dashboard_payload is not None:
            return ArtifactPreview(
                artifact_id=artifact.id,
                mode="dashboard",
                content_type="application/json",
                dashboard=DashboardSpec.model_validate(artifact.dashboard_payload),
                download_url=download_url,
            )
        if artifact.storage_object_id is None:
            text = str(artifact.preview_payload.get("text") or "")
            return ArtifactPreview(
                artifact_id=artifact.id,
                mode="text" if text else "download",
                content_type=content_type,
                text=text or None,
                download_url=download_url,
            )
        storage_object = self.storage_catalog.get(
            tenant_id, artifact.storage_object_id
        )
        if content_type.startswith("image/"):
            return ArtifactPreview(
                artifact_id=artifact.id,
                mode="image",
                content_type=content_type,
                download_url=download_url,
            )
        if content_type == "application/pdf":
            return ArtifactPreview(
                artifact_id=artifact.id,
                mode="pdf",
                content_type=content_type,
                download_url=download_url,
            )
        download = self.object_storage.download(storage_object)
        content = download.content[: policy.max_preview_bytes]
        truncated = len(download.content) > len(content)
        if content_type in {"text/html", "application/xhtml+xml"}:
            html = content.decode("utf-8", errors="replace")
            csp = escape(policy.content_security_policy, quote=True)
            srcdoc = (
                f'<meta http-equiv="Content-Security-Policy" content="{csp}">' + html
            )
            return ArtifactPreview(
                artifact_id=artifact.id,
                mode="iframe",
                content_type="text/html",
                srcdoc=srcdoc,
                download_url=download_url,
                truncated=truncated,
                iframe_sandbox=policy.iframe_sandbox,
                content_security_policy=policy.content_security_policy,
            )
        if content_type.startswith("text/") or content_type in {
            "application/json",
            "application/xml",
            "application/javascript",
        }:
            return ArtifactPreview(
                artifact_id=artifact.id,
                mode="text",
                content_type=content_type,
                text=content.decode("utf-8", errors="replace"),
                download_url=download_url,
                truncated=truncated,
            )
        return ArtifactPreview(
            artifact_id=artifact.id,
            mode="download",
            content_type=content_type,
            download_url=download_url,
        )

    def download(self, tenant_id: str, artifact_id: str):
        artifact = self.store.get_artifact(tenant_id, artifact_id)
        if artifact.storage_object_id is None:
            raise ValueError("Artifact does not have downloadable storage content")
        storage_object = self.storage_catalog.get(
            tenant_id, artifact.storage_object_id
        )
        return artifact, self.object_storage.download(storage_object)

    def source(self, tenant_id: str, artifact_id: str) -> ArtifactSource:
        artifact = self.store.get_artifact(tenant_id, artifact_id)
        content_type = artifact.content_type or "text/plain"
        source = ""
        truncated = False
        if artifact.storage_object_id is not None:
            storage_object = self.storage_catalog.get(
                tenant_id, artifact.storage_object_id
            )
            download = self.object_storage.download(storage_object)
            limit = ArtifactRenderPolicy().max_preview_bytes
            raw = download.content[:limit]
            truncated = len(download.content) > len(raw)
            source = raw.decode("utf-8", errors="replace")
        elif artifact.dashboard_payload is not None:
            content_type = "application/json"
            source = json.dumps(
                artifact.dashboard_payload, ensure_ascii=False, indent=2
            )
        else:
            payload = artifact.preview_payload or {}
            value = next(
                (
                    payload[key]
                    for key in ("source", "code", "text", "content")
                    if key in payload
                ),
                payload,
            )
            source = (
                value
                if isinstance(value, str)
                else json.dumps(value, ensure_ascii=False, indent=2)
            )
        return ArtifactSource(
            artifact_id=artifact.id,
            name=artifact.name,
            content_type=content_type,
            source=source,
            truncated=truncated,
        )

    def diff(
        self,
        tenant_id: str,
        artifact_id: str,
        compare_to_artifact_id: str | None = None,
    ) -> ArtifactDiff:
        artifact = self.store.get_artifact(tenant_id, artifact_id)
        comparison = None
        if compare_to_artifact_id is not None:
            comparison = self.store.get_artifact(tenant_id, compare_to_artifact_id)
            if comparison.workspace_id != artifact.workspace_id:
                raise ValueError("Artifacts must belong to the same workspace")
        else:
            candidates = [
                item
                for item in self.store.list_artifacts(tenant_id, artifact.run_id)
                if item.id != artifact.id
                and item.name == artifact.name
                and item.created_at < artifact.created_at
            ]
            comparison = candidates[-1] if candidates else None
        current = self.source(tenant_id, artifact.id).source
        previous = (
            self.source(tenant_id, comparison.id).source if comparison else ""
        )
        lines = difflib.unified_diff(
            previous.splitlines(keepends=True),
            current.splitlines(keepends=True),
            fromfile=comparison.name if comparison else "/dev/null",
            tofile=artifact.name,
        )
        rendered = "".join(lines)
        return ArtifactDiff(
            artifact_id=artifact.id,
            compare_to_artifact_id=comparison.id if comparison else None,
            compare_to_name=comparison.name if comparison else None,
            diff=rendered,
            has_changes=bool(rendered),
        )

