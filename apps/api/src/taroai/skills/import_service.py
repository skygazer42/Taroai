import re
from typing import Protocol
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field, model_validator

from taroai.skills.manifest import SkillManifest
from taroai.skills.package import (
    SkillPackage,
    SkillPackageFile,
    SkillPackageParser,
    SkillPackageSourceType,
    sha256_hex,
)


class GithubSkillSource(BaseModel):
    owner: str = Field(min_length=1, max_length=100)
    repository: str = Field(min_length=1, max_length=100)
    ref: str = Field(min_length=1, max_length=250)
    subdirectory: str | None = Field(default=None, max_length=500)
    expected_source_digest: str | None = None

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_source(self) -> "GithubSkillSource":
        safe_name = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
        if not safe_name.fullmatch(self.owner) or not safe_name.fullmatch(self.repository):
            raise ValueError("GitHub owner and repository contain unsafe characters")
        if self.owner in {".", ".."} or self.repository in {".", ".."}:
            raise ValueError("invalid GitHub owner or repository")
        if (
            self.ref.startswith("-")
            or ".." in self.ref
            or "@{" in self.ref
            or any(ord(character) < 32 for character in self.ref)
        ):
            raise ValueError("GitHub ref contains an unsafe sequence")
        if self.expected_source_digest is not None and not re.fullmatch(
            r"[0-9a-f]{64}", self.expected_source_digest
        ):
            raise ValueError("expected source digest must be lowercase SHA-256")
        return self


class GithubFetchPolicy(BaseModel):
    allowed_hosts: tuple[str, ...] = (
        "github.com",
        "api.github.com",
        "codeload.github.com",
    )
    require_https: bool = True
    max_response_bytes: int = Field(default=32 * 1024 * 1024, ge=1)
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    max_redirects: int = Field(default=3, ge=0, le=10)

    model_config = ConfigDict(extra="forbid", frozen=True)


class FetchedGithubArchive(BaseModel):
    archive_bytes: bytes = Field(repr=False, exclude=True)
    final_url: str
    resolved_ref: str

    model_config = ConfigDict(extra="forbid", frozen=True)


class GithubArchiveFetcher(Protocol):
    def fetch(
        self,
        source: GithubSkillSource,
        policy: GithubFetchPolicy,
    ) -> FetchedGithubArchive: ...


class HttpsGithubArchiveFetcher:
    """Fetches only GitHub codeload archives built from validated owner/repo/ref fields."""

    def fetch(
        self,
        source: GithubSkillSource,
        policy: GithubFetchPolicy,
    ) -> FetchedGithubArchive:
        url = (
            f"https://codeload.github.com/{quote(source.owner, safe='')}/"
            f"{quote(source.repository, safe='')}/zip/{quote(source.ref, safe='')}"
        )
        request = Request(
            url,
            headers={"Accept": "application/zip", "User-Agent": "Taroai-Skill-Importer/1"},
        )
        with urlopen(request, timeout=policy.timeout_seconds) as response:
            final_url = response.geturl()
            parsed = urlparse(final_url)
            if policy.require_https and parsed.scheme != "https":
                raise ValueError("GitHub archive redirected outside HTTPS")
            if (parsed.hostname or "").lower() not in policy.allowed_hosts:
                raise ValueError("GitHub archive redirected to an untrusted host")
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > policy.max_response_bytes:
                raise ValueError("GitHub archive exceeds the configured size limit")
            archive = response.read(policy.max_response_bytes + 1)
        if len(archive) > policy.max_response_bytes:
            raise ValueError("GitHub archive exceeds the configured size limit")
        return FetchedGithubArchive(
            archive_bytes=archive,
            final_url=final_url,
            resolved_ref=source.ref,
        )


class SkillPackageScanFinding(BaseModel):
    code: str
    severity: str
    path: str | None = None
    message: str

    model_config = ConfigDict(extra="forbid", frozen=True)


class SkillPackageScanResult(BaseModel):
    allowed: bool
    scanner_version: str
    findings: tuple[SkillPackageScanFinding, ...] = ()

    model_config = ConfigDict(extra="forbid", frozen=True)


class SkillPackageScanner(Protocol):
    def scan(self, package: SkillPackage) -> SkillPackageScanResult: ...


class StructuralSkillPackageScanner:
    """Default hook: package parser safety checks are the structural scan."""

    def scan(self, package: SkillPackage) -> SkillPackageScanResult:
        return SkillPackageScanResult(
            allowed=True,
            scanner_version="taroai.structural-scan.v1",
        )


class SkillPackageFileMetadata(BaseModel):
    path: str
    kind: str
    size_bytes: int
    content_digest: str

    model_config = ConfigDict(extra="forbid", frozen=True)


class SkillPackageImportService:
    def __init__(
        self,
        *,
        registry,
        parser: SkillPackageParser | None = None,
        scanner: SkillPackageScanner | None = None,
        github_fetcher: GithubArchiveFetcher | None = None,
        github_policy: GithubFetchPolicy | None = None,
    ):
        self.registry = registry
        self.parser = parser or SkillPackageParser()
        self.scanner = scanner or StructuralSkillPackageScanner()
        self.github_fetcher = github_fetcher
        self.github_policy = github_policy or GithubFetchPolicy(
            max_response_bytes=self.parser.limits.max_archive_bytes
        )

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
        package = self.parser.parse_zip(
            archive_bytes,
            manifest=manifest,
            source_type=SkillPackageSourceType.ZIP,
            source_url=source_url,
            source_ref=source_ref,
            subdirectory=subdirectory,
        )
        self._assert_scan_allowed(package)
        self.registry.register_package_for_tenant(
            tenant_id,
            created_by_user_id,
            package,
        )
        return package

    def import_github(
        self,
        *,
        tenant_id: str,
        created_by_user_id: str,
        source: GithubSkillSource,
        manifest: SkillManifest | None = None,
    ) -> SkillPackage:
        if self.github_fetcher is None:
            raise ValueError("GitHub skill import is not configured")
        fetched = self.github_fetcher.fetch(source, self.github_policy)
        self._assert_safe_github_response(fetched)
        source_digest = sha256_hex(fetched.archive_bytes)
        if (
            source.expected_source_digest is not None
            and source_digest != source.expected_source_digest
        ):
            raise ValueError("GitHub archive digest does not match the requested digest")
        package = self.parser.parse_zip(
            fetched.archive_bytes,
            manifest=manifest,
            source_type=SkillPackageSourceType.GITHUB,
            source_url=fetched.final_url,
            source_ref=fetched.resolved_ref,
            subdirectory=source.subdirectory,
        )
        self._assert_scan_allowed(package)
        self.registry.register_package_for_tenant(
            tenant_id,
            created_by_user_id,
            package,
        )
        return package

    def list_files(
        self,
        tenant_id: str,
        skill_id: str,
        version: str,
    ) -> list[SkillPackageFileMetadata]:
        package = self.registry.get_package_version(tenant_id, skill_id, version)
        return [
            SkillPackageFileMetadata(
                path=item.path,
                kind=item.kind.value,
                size_bytes=item.size_bytes,
                content_digest=item.content_digest,
            )
            for item in sorted(package.files, key=lambda value: value.path)
        ]

    def get_file(
        self,
        tenant_id: str,
        skill_id: str,
        version: str,
        path: str,
    ) -> SkillPackageFile:
        package = self.registry.get_package_version(tenant_id, skill_id, version)
        return package.get_file(path)

    def get_release_notes(
        self,
        tenant_id: str,
        skill_id: str,
        version: str,
    ) -> str | None:
        return self.registry.get_package_version(
            tenant_id,
            skill_id,
            version,
        ).release_notes

    def _assert_scan_allowed(self, package: SkillPackage) -> None:
        result = self.scanner.scan(package)
        if result.allowed:
            return
        codes = sorted({finding.code for finding in result.findings})
        raise ValueError(f"skill package scanner rejected the package: {codes}")

    def _assert_safe_github_response(self, fetched: FetchedGithubArchive) -> None:
        if not fetched.archive_bytes:
            raise ValueError("GitHub fetch returned an empty archive")
        if len(fetched.archive_bytes) > self.github_policy.max_response_bytes:
            raise ValueError("GitHub fetch exceeded the response size policy")
        parsed = urlparse(fetched.final_url)
        if self.github_policy.require_https and parsed.scheme != "https":
            raise ValueError("GitHub fetch final URL must use HTTPS")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("GitHub fetch final URL must not contain credentials")
        if parsed.hostname not in self.github_policy.allowed_hosts:
            raise ValueError("GitHub fetch redirected to a non-allowlisted host")
        if parsed.port not in {None, 443}:
            raise ValueError("GitHub fetch final URL uses a forbidden port")
        if not fetched.resolved_ref or any(
            ord(character) < 32 for character in fetched.resolved_ref
        ):
            raise ValueError("GitHub fetch did not return a safe resolved ref")
