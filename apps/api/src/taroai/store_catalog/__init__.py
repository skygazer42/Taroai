import io
import json
import zipfile
from dataclasses import dataclass
from importlib import resources
from pathlib import PurePosixPath
from typing import Any

from taroai.skills import SkillInstallationStatus, SkillPackage, SkillStatus
from taroai.skills.package import (
    SkillPackageParser,
    canonical_json_bytes,
    sha256_hex,
)
from taroai.solution_packs import SolutionPackManifest, SolutionPackStatus
from taroai.store import NotFoundError


@dataclass(frozen=True)
class BuiltinStoreSkill:
    package: SkillPackage
    archive_bytes: bytes


class StoreInstallConflictError(ValueError):
    pass


@dataclass(frozen=True)
class BuiltinStoreItem:
    manifest: SolutionPackManifest
    category: str
    publisher: str
    featured: bool
    skills: tuple[BuiltinStoreSkill, ...]

    @property
    def digest(self) -> str:
        return sha256_hex(
            canonical_json_bytes(
                {
                    "manifest": self.manifest.model_dump(mode="json"),
                    "package_digests": [
                        skill.package.package_digest for skill in self.skills
                    ],
                }
            )
        )

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.manifest.id,
            "version": self.manifest.version,
            "name": self.manifest.name,
            "description": self.manifest.description,
            "category": self.category,
            "publisher": self.publisher,
            "featured": self.featured,
            "kind": "solution_pack",
            "origin": "builtin",
            "digest": self.digest,
            "skill_count": len(self.skills),
            "requires_external_credentials": False,
            "risk_level": _highest_risk(self.skills),
            "approval_required": any(
                _requires_approval(skill.package) for skill in self.skills
            ),
            "license": _license(self.skills),
        }

    def detail(self) -> dict[str, Any]:
        return self.summary() | {
            "manifest": self.manifest.model_dump(mode="json"),
            "packages": [
                {
                    "skill_id": skill.package.skill_id,
                    "version": skill.package.version,
                    "package_digest": skill.package.package_digest,
                    "source_digest": skill.package.provenance.source_digest,
                    "risk_level": skill.package.manifest.risk_level,
                    "approval_required": bool(
                        skill.package.manifest.approval_required
                    ),
                }
                for skill in self.skills
            ],
        }


class BuiltinStoreCatalog:
    """Read-only catalog loaded from application resources, never tenant storage."""

    def __init__(self, parser: SkillPackageParser | None = None):
        self._parser = parser or SkillPackageParser()
        self._items = self._load_items()

    def list_items(self) -> list[BuiltinStoreItem]:
        return sorted(self._items.values(), key=lambda item: item.manifest.id)

    def list_skills(self) -> list[dict[str, Any]]:
        skills: dict[str, dict[str, Any]] = {}
        for item in self.list_items():
            for asset in item.skills:
                manifest = asset.package.manifest
                skills.setdefault(
                    manifest.id,
                    {
                        "id": manifest.id,
                        "displayName": manifest.name,
                        "description": manifest.description,
                        "tags": [item.category],
                        "owner": manifest.owner,
                    },
                )
        return sorted(skills.values(), key=lambda skill: skill["id"])

    def get(self, item_id: str) -> BuiltinStoreItem:
        item = self._items.get(item_id)
        if item is None:
            raise NotFoundError(f"Store item not found: {item_id}")
        return item

    def _load_items(self) -> dict[str, BuiltinStoreItem]:
        root = resources.files(__package__)
        payload = json.loads((root / "catalog.json").read_text(encoding="utf-8"))
        items: dict[str, BuiltinStoreItem] = {}
        for raw in payload.get("items", []):
            item_id = str(raw["id"])
            if item_id in items:
                raise ValueError(f"Duplicate builtin store item: {item_id}")
            skill_assets = tuple(
                self._load_skill(root, item_id, str(path))
                for path in raw.get("skills", [])
            )
            if not skill_assets:
                raise ValueError(f"Builtin store item has no skills: {item_id}")
            manifest = SolutionPackManifest.model_validate(
                {
                    "id": item_id,
                    "version": raw["version"],
                    "name": raw["name"],
                    "description": raw["description"],
                    "industry": raw.get("industry", "general"),
                    "use_cases": raw.get("use_cases", []),
                    "skills": [asset.package.manifest for asset in skill_assets],
                    "success_metrics": raw.get("success_metrics", []),
                    "rollout_checklist": raw.get("rollout_checklist", []),
                }
            )
            items[item_id] = BuiltinStoreItem(
                manifest=manifest,
                category=str(raw.get("category", "general")),
                publisher=str(raw.get("publisher", "Taroai")),
                featured=bool(raw.get("featured", False)),
                skills=skill_assets,
            )
        return items

    def _load_skill(self, root, item_id: str, raw_path: str) -> BuiltinStoreSkill:
        path = PurePosixPath(raw_path)
        if path.is_absolute() or ".." in path.parts or path.parts[:1] != ("items",):
            raise ValueError(f"Unsafe builtin store resource path: {raw_path}")
        archive_bytes = _resource_archive(root.joinpath(*path.parts))
        return BuiltinStoreSkill(
            package=self._parser.parse_zip(
                archive_bytes,
                source_ref=f"builtin:{item_id}",
            ),
            archive_bytes=archive_bytes,
        )


def install_builtin_store_item(
    *,
    item: BuiltinStoreItem,
    skill_service,
    solution_pack_registry,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
) -> dict[str, Any]:
    registry = skill_service.registry
    existing: dict[str, Any] = {}
    installations: dict[str, Any] = {}

    pack_versions = solution_pack_registry.list_versions(
        tenant_id, item.manifest.id
    )
    matching_pack = next(
        (
            entry
            for entry in pack_versions
            if entry.manifest.version == item.manifest.version
        ),
        None,
    )
    if matching_pack is not None and matching_pack.manifest != item.manifest:
        raise StoreInstallConflictError(
            f"Store solution pack manifest conflict: {item.manifest.id}@{item.manifest.version}"
        )
    try:
        pack_installation = solution_pack_registry.get_installation(
            tenant_id, item.manifest.id
        )
    except NotFoundError:
        pack_installation = None
    if (
        pack_installation is not None
        and pack_installation.version != item.manifest.version
    ):
        raise StoreInstallConflictError(
            f"Store solution pack version conflict: {item.manifest.id}"
        )

    # Preflight every package before writing, so a digest conflict cannot partially install a pack.
    for asset in item.skills:
        package = asset.package
        try:
            record = registry.get_package_record(
                tenant_id, package.skill_id, package.version
            )
        except NotFoundError:
            record = None
        if record is not None and record.package.package_digest != package.package_digest:
            raise StoreInstallConflictError(
                f"Store package digest conflict: {package.skill_id}@{package.version}"
            )
        if record is None and any(
            entry.manifest.version == package.version
            and entry.manifest != package.manifest
            for entry in registry.list_versions(tenant_id, package.skill_id)
        ):
            raise StoreInstallConflictError(
                f"Store package manifest conflict: {package.skill_id}@{package.version}"
            )
        try:
            installation = registry.get_installation(
                tenant_id, workspace_id, package.skill_id
            )
        except NotFoundError:
            installation = None
        if (
            installation is not None
            and installation.package_digest != package.package_digest
        ):
            raise StoreInstallConflictError(
                f"Workspace skill digest conflict: {package.skill_id}"
            )
        existing[package.skill_id] = record
        installations[package.skill_id] = installation

    if matching_pack is None:
        solution_pack_registry.register_for_tenant(
            tenant_id=tenant_id,
            created_by_user_id=user_id,
            manifest=item.manifest.model_copy(deep=True),
        )
    pack_entry = solution_pack_registry.get_for_tenant(
        tenant_id, item.manifest.id
    )
    if pack_entry.status != SolutionPackStatus.PUBLISHED:
        solution_pack_registry.publish(tenant_id, item.manifest.id)

    results: list[dict[str, Any]] = []
    for asset in item.skills:
        package = asset.package
        record = existing[package.skill_id]
        if record is None:
            skill_service.import_zip(
                tenant_id=tenant_id,
                created_by_user_id=user_id,
                archive_bytes=asset.archive_bytes,
                source_ref=f"builtin:{item.manifest.id}@{item.manifest.version}",
            )
            record = registry.get_package_record(
                tenant_id, package.skill_id, package.version
            )
        if record.status != SkillStatus.PUBLISHED:
            evaluation = skill_service.evaluate(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                skill_id=package.skill_id,
                version=package.version,
                created_by_user_id=user_id,
            )
            skill_service.publish(
                tenant_id=tenant_id,
                skill_id=package.skill_id,
                version=package.version,
                evaluation_run_id=evaluation.id,
            )
        installation = installations[package.skill_id]
        if installation is None:
            installation = skill_service.install(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                skill_id=package.skill_id,
                version=package.version,
                package_digest=package.package_digest,
                installed_by_user_id=user_id,
            )
        requires_approval = _requires_approval(package)
        if requires_approval and installation.status != SkillInstallationStatus.DISABLED:
            installation = registry.disable_for_workspace(
                tenant_id, workspace_id, package.skill_id
            )
        results.append(
            {
                "skill_id": package.skill_id,
                "version": package.version,
                "package_digest": package.package_digest,
                "status": installation.status.value,
                "requires_approval": requires_approval,
            }
        )
    recorded_installation = solution_pack_registry.record_installation(
        tenant_id=tenant_id,
        pack_id=item.manifest.id,
        version=item.manifest.version,
        workspace_ids=sorted(
            set(pack_installation.workspace_ids if pack_installation else [])
            | {workspace_id}
        ),
        installed_skill_ids=sorted(
            set(pack_installation.installed_skill_ids if pack_installation else [])
            | {skill.package.skill_id for skill in item.skills}
        ),
        installed_by_user_id=user_id,
    )
    return {
        "skills": results,
        "installation": recorded_installation.model_dump(mode="json"),
    }


def _resource_archive(root) -> bytes:
    if not root.is_dir():
        raise ValueError("Builtin store skill resource is not a directory")
    files: list[tuple[str, bytes]] = []

    def collect(directory, prefix: PurePosixPath = PurePosixPath()) -> None:
        for child in sorted(directory.iterdir(), key=lambda item: item.name):
            path = prefix / child.name
            if child.is_dir():
                collect(child, path)
            elif child.is_file():
                files.append((str(path), child.read_bytes()))

    collect(root)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, content in files:
            info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
    return buffer.getvalue()


def _requires_approval(package: SkillPackage) -> bool:
    return package.manifest.risk_level.casefold() in {"high", "critical"} or bool(
        package.manifest.approval_required
    )


def _highest_risk(skills: tuple[BuiltinStoreSkill, ...]) -> str:
    levels = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    return max(
        (skill.package.manifest.risk_level for skill in skills),
        key=lambda value: levels.get(value.casefold(), 4),
    )


def _license(skills: tuple[BuiltinStoreSkill, ...]) -> str | None:
    licenses = {
        skill.package.frontmatter.license
        for skill in skills
        if skill.package.frontmatter.license
    }
    if not licenses:
        return None
    return next(iter(licenses)) if len(licenses) == 1 else "Mixed"
