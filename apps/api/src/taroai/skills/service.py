import difflib

from taroai.skills.discovery import (
    SkillDiscoveryService,
    SkillDiscoverySummary,
    SkillLoadedContent,
)
from taroai.skills.evaluation import (
    SkillEvaluationGate,
    SkillEvaluationRun,
    SkillEvaluationRunner,
    SkillEvaluationSuite,
    load_evaluation_suite,
)
from taroai.skills.import_service import (
    GithubArchiveFetcher,
    GithubFetchPolicy,
    GithubSkillSource,
    SkillPackageFileMetadata,
    SkillPackageImportService,
    SkillPackageScanner,
)
from taroai.skills.manifest import SkillManifest
from taroai.skills.materializer import (
    SkillMaterializationPlan,
    SkillMaterializer,
)
from taroai.skills.package import SkillPackage, SkillPackageFile, SkillPackageParser
from taroai.skills.registry import SkillInstallation, SkillPackageRecord


class SkillService:
    """Application service for package import, governance, discovery and evaluation."""

    def __init__(
        self,
        *,
        registry,
        parser: SkillPackageParser | None = None,
        scanner: SkillPackageScanner | None = None,
        github_fetcher: GithubArchiveFetcher | None = None,
        github_policy: GithubFetchPolicy | None = None,
        evaluation_runner: SkillEvaluationRunner | None = None,
        evaluation_gate: SkillEvaluationGate | None = None,
        materializer: SkillMaterializer | None = None,
    ):
        self.registry = registry
        self.imports = SkillPackageImportService(
            registry=registry,
            parser=parser,
            scanner=scanner,
            github_fetcher=github_fetcher,
            github_policy=github_policy,
        )
        self.discovery = SkillDiscoveryService(registry)
        self.materializer = materializer or SkillMaterializer()
        self.evaluation_runner = evaluation_runner
        self.evaluation_gate = evaluation_gate or SkillEvaluationGate()

    def import_zip(
        self,
        *,
        tenant_id: str,
        created_by_user_id: str,
        archive_bytes: bytes,
        manifest: SkillManifest | None = None,
        source_url: str | None = None,
        source_ref: str | None = None,
        subdirectory: str | None = None,
    ) -> SkillPackage:
        return self.imports.import_zip(
            tenant_id=tenant_id,
            created_by_user_id=created_by_user_id,
            archive_bytes=archive_bytes,
            manifest=manifest,
            source_url=source_url,
            source_ref=source_ref,
            subdirectory=subdirectory,
        )

    def import_github(
        self,
        *,
        tenant_id: str,
        created_by_user_id: str,
        source: GithubSkillSource,
        manifest: SkillManifest | None = None,
    ) -> SkillPackage:
        return self.imports.import_github(
            tenant_id=tenant_id,
            created_by_user_id=created_by_user_id,
            source=source,
            manifest=manifest,
        )

    def evaluate(
        self,
        *,
        tenant_id: str,
        workspace_id: str | None,
        skill_id: str,
        version: str,
        created_by_user_id: str,
        suite: SkillEvaluationSuite | None = None,
    ) -> SkillEvaluationRun:
        if self.evaluation_runner is None:
            raise ValueError("skill evaluation runner is not configured")
        package = self.registry.get_package_version(tenant_id, skill_id, version)
        resolved_suite = suite or load_evaluation_suite(package)
        run = self.evaluation_runner.run(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            created_by_user_id=created_by_user_id,
            package=package,
            suite=resolved_suite,
        )
        return self.registry.record_evaluation_run(run)

    def publish(
        self,
        *,
        tenant_id: str,
        skill_id: str,
        version: str,
        evaluation_run_id: str | None = None,
    ) -> SkillPackageRecord:
        package = self.registry.get_package_version(tenant_id, skill_id, version)
        if evaluation_run_id is None:
            evaluation_run = self.registry.latest_evaluation_run(
                tenant_id,
                skill_id,
                version,
            )
        else:
            candidates = [
                run
                for run in self.registry.list_evaluation_runs(
                    tenant_id,
                    skill_id,
                    version,
                )
                if run.id == evaluation_run_id
            ]
            if not candidates:
                raise ValueError(f"skill evaluation run not found: {evaluation_run_id}")
            evaluation_run = candidates[0]
        self.evaluation_gate.assert_publishable(package, evaluation_run)
        return self.registry.publish_package(tenant_id, skill_id, version)

    def disable(
        self,
        *,
        tenant_id: str,
        skill_id: str,
        version: str,
    ) -> SkillPackageRecord:
        return self.registry.disable_package(tenant_id, skill_id, version)

    def list_package_versions(
        self,
        *,
        tenant_id: str,
        skill_id: str,
    ) -> list[SkillPackageRecord]:
        return self.registry.list_package_records(tenant_id, skill_id)

    def get_package(
        self,
        *,
        tenant_id: str,
        skill_id: str,
        version: str,
    ) -> SkillPackage:
        return self.registry.get_package_version(tenant_id, skill_id, version)

    def install(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        skill_id: str,
        version: str,
        package_digest: str,
        installed_by_user_id: str,
    ) -> SkillInstallation:
        return self.registry.install_for_workspace(
            tenant_id,
            workspace_id,
            skill_id,
            installed_by_user_id,
            version=version,
            package_digest=package_digest,
        )

    def upgrade(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        skill_id: str,
        target_version: str,
        updated_by_user_id: str,
        expected_package_digest: str | None = None,
    ) -> SkillInstallation:
        return self.registry.upgrade_for_workspace(
            tenant_id,
            workspace_id,
            skill_id,
            target_version,
            updated_by_user_id,
            expected_package_digest=expected_package_digest,
        )

    def rollback(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        skill_id: str,
        target_version: str,
        rolled_back_by_user_id: str,
        expected_package_digest: str | None = None,
    ) -> SkillInstallation:
        return self.registry.rollback_for_workspace(
            tenant_id,
            workspace_id,
            skill_id,
            target_version,
            rolled_back_by_user_id,
            expected_package_digest=expected_package_digest,
        )

    def uninstall(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        skill_id: str,
    ) -> SkillInstallation:
        return self.registry.uninstall_for_workspace(
            tenant_id, workspace_id, skill_id
        )

    def enable(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        skill_id: str,
    ) -> SkillInstallation:
        return self.registry.enable_for_workspace(tenant_id, workspace_id, skill_id)

    def list_files(
        self,
        tenant_id: str,
        skill_id: str,
        version: str,
    ) -> list[SkillPackageFileMetadata]:
        return self.imports.list_files(tenant_id, skill_id, version)

    def get_file(
        self,
        tenant_id: str,
        skill_id: str,
        version: str,
        path: str,
    ) -> SkillPackageFile:
        return self.imports.get_file(tenant_id, skill_id, version, path)

    def get_release_notes(
        self,
        tenant_id: str,
        skill_id: str,
        version: str,
    ) -> str | None:
        return self.imports.get_release_notes(tenant_id, skill_id, version)

    def diff_versions(
        self,
        tenant_id: str,
        skill_id: str,
        version: str,
        compare_to: str,
    ) -> dict:
        current = self.get_package(
            tenant_id=tenant_id, skill_id=skill_id, version=version
        )
        previous = self.get_package(
            tenant_id=tenant_id, skill_id=skill_id, version=compare_to
        )
        current_files = {item.path: item for item in current.files}
        previous_files = {item.path: item for item in previous.files}
        added = sorted(set(current_files) - set(previous_files))
        removed = sorted(set(previous_files) - set(current_files))
        changed = sorted(
            path
            for path in set(current_files) & set(previous_files)
            if current_files[path].content_digest
            != previous_files[path].content_digest
        )
        patches = []
        for path in [*added, *removed, *changed]:
            before = previous_files.get(path)
            after = current_files.get(path)
            try:
                before_text = before.content.decode("utf-8") if before else ""
                after_text = after.content.decode("utf-8") if after else ""
            except UnicodeDecodeError:
                patches.append(
                    {
                        "path": path,
                        "binary": True,
                        "diff": "",
                        "before_size": before.size_bytes if before else 0,
                        "after_size": after.size_bytes if after else 0,
                    }
                )
                continue
            patches.append(
                {
                    "path": path,
                    "binary": False,
                    "diff": "".join(
                        difflib.unified_diff(
                            before_text.splitlines(keepends=True),
                            after_text.splitlines(keepends=True),
                            fromfile=f"{compare_to}/{path}",
                            tofile=f"{version}/{path}",
                        )
                    ),
                }
            )
        before_scopes = set(previous.manifest.required_scopes)
        after_scopes = set(current.manifest.required_scopes)
        before_dependencies = {
            f"{item.id}@{item.version}" for item in previous.resolved_dependencies
        }
        after_dependencies = {
            f"{item.id}@{item.version}" for item in current.resolved_dependencies
        }
        return {
            "skill_id": skill_id,
            "from_version": compare_to,
            "to_version": version,
            "from_package_digest": previous.package_digest,
            "to_package_digest": current.package_digest,
            "files": {"added": added, "removed": removed, "changed": changed},
            "required_scopes": {
                "added": sorted(after_scopes - before_scopes),
                "removed": sorted(before_scopes - after_scopes),
            },
            "dependencies": {
                "added": sorted(after_dependencies - before_dependencies),
                "removed": sorted(before_dependencies - after_dependencies),
            },
            "release_notes": current.release_notes,
            "patches": patches,
        }

    def discover(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        department_id: str | None = None,
    ) -> list[SkillDiscoverySummary]:
        return self.discovery.discover(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            department_id=department_id,
        )

    def load_skill(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        skill_id: str,
        expected_version: str | None = None,
        expected_package_digest: str | None = None,
        expected_source_digest: str | None = None,
    ) -> SkillLoadedContent:
        return self.discovery.load_skill(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            skill_id=skill_id,
            expected_version=expected_version,
            expected_package_digest=expected_package_digest,
            expected_source_digest=expected_source_digest,
        )

    def materialization_plan(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        skill_id: str,
    ) -> SkillMaterializationPlan:
        package = self.registry.get_installed_package(
            tenant_id,
            workspace_id,
            skill_id,
        )
        return self.materializer.plan(package)
