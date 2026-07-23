import hashlib
import json
import re
import stat
from textwrap import dedent
import unicodedata
import zipfile
from enum import Enum
from io import BytesIO
from pathlib import PurePosixPath
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

from taroai.skills.manifest import (
    SkillManifest,
    SkillRuntime,
    SkillType,
    SkillVisibility,
)


class SkillPackageError(ValueError):
    """Raised when an imported skill package is structurally unsafe or invalid."""


class SkillPackageKind(str, Enum):
    PACKAGE = "package"
    LEGACY_MANIFEST = "legacy_manifest"


class SkillPackageSourceType(str, Enum):
    ZIP = "zip"
    GITHUB = "github"


class SkillPackageFileKind(str, Enum):
    INSTRUCTIONS = "instructions"
    GOVERNANCE = "governance"
    SCRIPT = "script"
    REFERENCE = "reference"
    ASSET = "asset"
    EXAMPLE = "example"
    EVALUATION = "evaluation"
    RELEASE_NOTES = "release_notes"
    OTHER = "other"


class SkillPackageLimits(BaseModel):
    max_archive_bytes: int = Field(default=32 * 1024 * 1024, ge=1)
    max_files: int = Field(default=256, ge=1)
    max_file_bytes: int = Field(default=4 * 1024 * 1024, ge=1)
    max_total_uncompressed_bytes: int = Field(default=32 * 1024 * 1024, ge=1)
    max_compression_ratio: float = Field(default=100.0, ge=1.0)
    max_skill_md_bytes: int = Field(default=512 * 1024, ge=1)
    max_frontmatter_lines: int = Field(default=64, ge=2)

    model_config = ConfigDict(extra="forbid", frozen=True)


class SkillFrontmatter(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4000)
    license: str | None = Field(default=None, max_length=200)
    compatibility: str | None = Field(default=None, max_length=1000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid", frozen=True)


class SkillDependency(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=100)
    package_digest: str | None = None

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_pin(self) -> "SkillDependency":
        _validate_identifier(self.id, "dependency id")
        _validate_version(self.version)
        if self.package_digest is not None:
            _validate_sha256(self.package_digest, "dependency package digest")
        return self


class SkillPackageProvenance(BaseModel):
    source_type: SkillPackageSourceType
    source_digest: str
    source_url: str | None = Field(default=None, max_length=2000)
    source_ref: str | None = Field(default=None, max_length=500)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_source_digest(self) -> "SkillPackageProvenance":
        _validate_sha256(self.source_digest, "source digest")
        return self


class SkillPackageFile(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    kind: SkillPackageFileKind
    size_bytes: int = Field(ge=0)
    content_digest: str
    content: bytes = Field(repr=False, exclude=True)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_content(self) -> "SkillPackageFile":
        normalized = normalize_package_path(self.path)
        if normalized != self.path:
            raise ValueError("skill package file path must already be normalized")
        if self.size_bytes != len(self.content):
            raise ValueError("skill package file size does not match its content")
        _validate_sha256(self.content_digest, "file content digest")
        if sha256_hex(self.content) != self.content_digest:
            raise ValueError("skill package file digest does not match its content")
        return self


class SkillPackage(BaseModel):
    manifest: SkillManifest
    frontmatter: SkillFrontmatter
    skill_md: str
    taroai_config: dict[str, Any] = Field(default_factory=dict)
    files: tuple[SkillPackageFile, ...]
    package_digest: str
    provenance: SkillPackageProvenance
    resolved_dependencies: tuple[SkillDependency, ...] = ()
    package_kind: SkillPackageKind = SkillPackageKind.PACKAGE
    release_notes: str | None = None

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_package(self) -> "SkillPackage":
        _validate_identifier(self.manifest.id, "skill id")
        _validate_version(self.manifest.version)
        _validate_sha256(self.package_digest, "package digest")
        if self.package_kind != SkillPackageKind.PACKAGE:
            raise ValueError("SkillPackage content must use package kind 'package'")
        paths = [item.path.casefold() for item in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("skill package contains case-insensitive duplicate paths")
        if "skill.md" not in paths:
            raise ValueError("SKILL.md is required")
        return self

    @property
    def skill_id(self) -> str:
        return self.manifest.id

    @property
    def version(self) -> str:
        return self.manifest.version

    def get_file(self, path: str) -> SkillPackageFile:
        normalized = normalize_package_path(path)
        for item in self.files:
            if item.path == normalized:
                return item
        raise KeyError(normalized)

    def list_files(self, kind: SkillPackageFileKind | None = None) -> list[SkillPackageFile]:
        return [item for item in self.files if kind is None or item.kind == kind]


class ParsedSkillArchive(BaseModel):
    files: tuple[SkillPackageFile, ...]
    source_digest: str

    model_config = ConfigDict(extra="forbid", frozen=True)


_FRONTMATTER_KEYS = {"name", "description", "license", "compatibility", "metadata"}
_IGNORED_FRONTMATTER_KEYS = {
    "allowed-tools",
    "args",
    "argument-hint",
    "disable-model-invocation",
    "homepage",
    "tools",
    "user-invokable",
}
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_-]{0,99}$")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CREDENTIAL_FILENAMES = {
    ".env",
    ".npmrc",
    ".pypirc",
    "auth.json",
    "credentials.json",
    "service-account.json",
    "service_account.json",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "known_hosts",
}
_CREDENTIAL_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".jks")
_RELEASE_NOTE_PATHS = ("RELEASE_NOTES.md", "CHANGELOG.md", "RELEASE.md")


def sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def normalize_package_path(raw_path: str) -> str:
    if not isinstance(raw_path, str) or not raw_path:
        raise SkillPackageError("skill package path must be a non-empty string")
    if "\x00" in raw_path:
        raise SkillPackageError("skill package path contains a NUL byte")
    path = unicodedata.normalize("NFC", raw_path).replace("\\", "/")
    if path.startswith("/") or path.startswith("//") or _DRIVE_RE.match(path):
        raise SkillPackageError(f"absolute skill package path is forbidden: {raw_path!r}")
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise SkillPackageError(f"unsafe skill package path: {raw_path!r}")
    for part in parts:
        if any(ord(character) < 32 or ord(character) == 127 for character in part):
            raise SkillPackageError(f"control character in skill package path: {raw_path!r}")
        if part.rstrip(". ") != part:
            raise SkillPackageError(f"ambiguous skill package path segment: {raw_path!r}")
    normalized = str(PurePosixPath(*parts))
    if len(normalized) > 1000:
        raise SkillPackageError("skill package path exceeds 1000 characters")
    return normalized


def classify_skill_file(path: str) -> SkillPackageFileKind:
    normalized = normalize_package_path(path)
    if normalized == "SKILL.md":
        return SkillPackageFileKind.INSTRUCTIONS
    if normalized == "taroai.yaml":
        return SkillPackageFileKind.GOVERNANCE
    if normalized in _RELEASE_NOTE_PATHS:
        return SkillPackageFileKind.RELEASE_NOTES
    root = normalized.split("/", 1)[0]
    return {
        "scripts": SkillPackageFileKind.SCRIPT,
        "references": SkillPackageFileKind.REFERENCE,
        "assets": SkillPackageFileKind.ASSET,
        "examples": SkillPackageFileKind.EXAMPLE,
        "evals": SkillPackageFileKind.EVALUATION,
    }.get(root, SkillPackageFileKind.OTHER)


def parse_skill_frontmatter(
    skill_md: str,
    *,
    max_lines: int = 64,
) -> tuple[SkillFrontmatter, str]:
    text = skill_md.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    if not lines or lines[0] != "---":
        raise SkillPackageError("SKILL.md must begin with restricted YAML frontmatter")
    end_index: int | None = None
    for index, line in enumerate(lines[1 : max_lines + 1], start=1):
        if line == "---":
            end_index = index
            break
    if end_index is None:
        raise SkillPackageError("SKILL.md frontmatter is missing a closing delimiter")
    values: dict[str, Any] = {}
    seen: set[str] = set()
    frontmatter_lines = lines[1:end_index]
    index = 0
    while index < len(frontmatter_lines):
        line = frontmatter_lines[index]
        line_number = index + 2
        index += 1
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line.startswith((" ", "\t")) or ":" not in line:
            raise SkillPackageError(
                f"SKILL.md frontmatter line {line_number} must be a top-level key/value"
            )
        key, raw_value = line.split(":", 1)
        key = key.strip()
        if key not in _FRONTMATTER_KEYS | _IGNORED_FRONTMATTER_KEYS:
            raise SkillPackageError(f"unsupported SKILL.md frontmatter key: {key}")
        if key in seen:
            raise SkillPackageError(f"duplicate SKILL.md frontmatter key: {key}")
        seen.add(key)
        raw_value = raw_value.strip()
        if re.fullmatch(r"[>|][+-]?", raw_value):
            block: list[str] = []
            while index < len(frontmatter_lines):
                block_line = frontmatter_lines[index]
                if block_line and not block_line.startswith((" ", "\t")):
                    break
                if block_line.lstrip(" ").startswith("\t"):
                    raise SkillPackageError(
                        f"tabs are forbidden in SKILL.md frontmatter block: {key}"
                    )
                block.append(block_line)
                index += 1
            value = dedent("\n".join(block)).strip()
            if raw_value.startswith(">"):
                value = re.sub(r"(?<!\n)\n(?!\n)", " ", value)
            if not value:
                raise SkillPackageError(f"SKILL.md frontmatter value is empty: {key}")
        else:
            value = _parse_restricted_frontmatter_value(raw_value, key)
        if key in _FRONTMATTER_KEYS:
            values[key] = value
    try:
        frontmatter = SkillFrontmatter.model_validate(values)
    except Exception as error:
        raise SkillPackageError(f"invalid SKILL.md frontmatter: {error}") from error
    return frontmatter, "\n".join(lines[end_index + 1 :]).lstrip("\n")


def parse_json_compatible_taroai_config(content: bytes) -> dict[str, Any]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise SkillPackageError("taroai.yaml must be UTF-8") from error
    try:
        value = json.loads(text, object_pairs_hook=_reject_duplicate_json_keys)
    except (json.JSONDecodeError, SkillPackageError) as error:
        raise SkillPackageError(
            "taroai.yaml must contain one JSON-compatible object without duplicate keys"
        ) from error
    if not isinstance(value, dict):
        raise SkillPackageError("taroai.yaml must contain a JSON object")
    _assert_json_shape(value)
    return value


class SkillPackageParser:
    def __init__(self, limits: SkillPackageLimits | None = None):
        self.limits = limits or SkillPackageLimits()

    def parse_zip(
        self,
        archive_bytes: bytes,
        *,
        manifest: SkillManifest | None = None,
        source_type: SkillPackageSourceType = SkillPackageSourceType.ZIP,
        source_url: str | None = None,
        source_ref: str | None = None,
        subdirectory: str | None = None,
    ) -> SkillPackage:
        parsed = self.read_zip(archive_bytes, subdirectory=subdirectory)
        file_map = {item.path: item for item in parsed.files}
        skill_file = file_map.get("SKILL.md")
        if skill_file is None:
            raise SkillPackageError("SKILL.md is required at the package root")
        if skill_file.size_bytes > self.limits.max_skill_md_bytes:
            raise SkillPackageError("SKILL.md exceeds the configured size limit")
        skill_md = _decode_utf8(skill_file.content, "SKILL.md")
        frontmatter, _body = parse_skill_frontmatter(
            skill_md,
            max_lines=self.limits.max_frontmatter_lines,
        )
        taroai_file = file_map.get("taroai.yaml")
        taroai_config = (
            parse_json_compatible_taroai_config(taroai_file.content)
            if taroai_file is not None
            else {}
        )
        resolved_manifest = _resolve_manifest(manifest, frontmatter, taroai_config)
        dependencies = _parse_dependencies(taroai_config)
        release_notes = _read_release_notes(file_map, taroai_config)
        package_digest = stable_package_digest(
            resolved_manifest,
            parsed.files,
            dependencies,
        )
        return SkillPackage(
            manifest=resolved_manifest,
            frontmatter=frontmatter,
            skill_md=skill_md,
            taroai_config=taroai_config,
            files=parsed.files,
            package_digest=package_digest,
            provenance=SkillPackageProvenance(
                source_type=source_type,
                source_url=source_url,
                source_ref=source_ref,
                source_digest=parsed.source_digest,
            ),
            resolved_dependencies=dependencies,
            release_notes=release_notes,
        )

    def read_zip(
        self,
        archive_bytes: bytes,
        *,
        subdirectory: str | None = None,
    ) -> ParsedSkillArchive:
        if not archive_bytes:
            raise SkillPackageError("skill package ZIP is empty")
        if len(archive_bytes) > self.limits.max_archive_bytes:
            raise SkillPackageError("skill package ZIP exceeds the archive size limit")
        try:
            archive = zipfile.ZipFile(BytesIO(archive_bytes))
        except (zipfile.BadZipFile, OSError) as error:
            raise SkillPackageError("skill package is not a valid ZIP archive") from error
        with archive:
            infos = archive.infolist()
            file_infos = [info for info in infos if not info.is_dir()]
            if len(file_infos) > self.limits.max_files:
                raise SkillPackageError("skill package ZIP contains too many files")
            total_size = 0
            raw_files: list[tuple[str, bytes]] = []
            for info in infos:
                original_name = getattr(info, "orig_filename", info.filename)
                normalized = normalize_package_path(original_name.rstrip("/"))
                _assert_safe_zip_member(info, normalized, self.limits)
                if info.is_dir():
                    continue
                total_size += info.file_size
                if total_size > self.limits.max_total_uncompressed_bytes:
                    raise SkillPackageError(
                        "skill package ZIP exceeds the total uncompressed size limit"
                    )
                _assert_not_credential_path(normalized)
                try:
                    content = archive.read(info)
                except (RuntimeError, zipfile.BadZipFile, OSError) as error:
                    raise SkillPackageError(f"unable to read ZIP member: {normalized}") from error
                if len(content) != info.file_size:
                    raise SkillPackageError(f"ZIP member size mismatch: {normalized}")
                raw_files.append((normalized, content))
        normalized_files = _normalize_archive_root(raw_files)
        if subdirectory is not None:
            normalized_files = _select_subdirectory(normalized_files, subdirectory)
        _assert_unique_paths(normalized_files)
        files = tuple(
            SkillPackageFile(
                path=path,
                kind=classify_skill_file(path),
                size_bytes=len(content),
                content_digest=sha256_hex(content),
                content=content,
            )
            for path, content in sorted(normalized_files, key=lambda item: item[0])
        )
        return ParsedSkillArchive(files=files, source_digest=sha256_hex(archive_bytes))


def stable_package_digest(
    manifest: SkillManifest,
    files: tuple[SkillPackageFile, ...],
    dependencies: tuple[SkillDependency, ...] = (),
) -> str:
    payload = {
        "schema": "taroai.skill-package.v1",
        "manifest": manifest.model_dump(mode="json"),
        "files": [
            {
                "path": item.path,
                "kind": item.kind.value,
                "size_bytes": item.size_bytes,
                "content_digest": item.content_digest,
            }
            for item in sorted(files, key=lambda value: value.path)
        ],
        "resolved_dependencies": [
            item.model_dump(mode="json")
            for item in sorted(dependencies, key=lambda value: (value.id, value.version))
        ],
    }
    return sha256_hex(canonical_json_bytes(payload))


def _parse_restricted_frontmatter_value(raw_value: str, key: str) -> Any:
    if not raw_value:
        raise SkillPackageError(f"SKILL.md frontmatter value is empty: {key}")
    if raw_value[0] in "|>!&*":
        raise SkillPackageError(f"advanced YAML is forbidden in SKILL.md frontmatter: {key}")
    if raw_value[0] in "\"'{[":
        candidate = raw_value
        if raw_value[0] == "'":
            if len(raw_value) < 2 or raw_value[-1] != "'":
                raise SkillPackageError(f"unterminated frontmatter string: {key}")
            return raw_value[1:-1]
        try:
            return json.loads(candidate, object_pairs_hook=_reject_duplicate_json_keys)
        except (json.JSONDecodeError, SkillPackageError) as error:
            raise SkillPackageError(f"frontmatter value must be JSON-compatible: {key}") from error
    return raw_value


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SkillPackageError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _assert_json_shape(value: Any, *, depth: int = 0) -> None:
    if depth > 20:
        raise SkillPackageError("taroai.yaml nesting exceeds 20 levels")
    if isinstance(value, dict):
        if len(value) > 256:
            raise SkillPackageError("taroai.yaml object contains too many keys")
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 200:
                raise SkillPackageError("taroai.yaml contains an invalid object key")
            _assert_json_shape(item, depth=depth + 1)
    elif isinstance(value, list):
        if len(value) > 1024:
            raise SkillPackageError("taroai.yaml array contains too many items")
        for item in value:
            _assert_json_shape(item, depth=depth + 1)
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise SkillPackageError("taroai.yaml contains a non-JSON value")


def _resolve_manifest(
    supplied: SkillManifest | None,
    frontmatter: SkillFrontmatter,
    config: Mapping[str, Any],
) -> SkillManifest:
    configured_manifest = config.get("manifest")
    if supplied is not None and configured_manifest is not None:
        parsed = SkillManifest.model_validate(configured_manifest)
        if parsed != supplied:
            raise SkillPackageError("supplied manifest conflicts with taroai.yaml manifest")
        manifest = supplied
    elif supplied is not None:
        manifest = supplied
    elif configured_manifest is not None:
        manifest = SkillManifest.model_validate(configured_manifest)
    else:
        metadata = _mapping(config.get("metadata"))
        spec = _mapping(config.get("spec"))
        runtime = _mapping(spec.get("runtime"))
        frontmatter_metadata = _mapping(frontmatter.metadata)
        skill_id = str(
            metadata.get("id")
            or frontmatter_metadata.get("id")
            or _slugify(frontmatter.name)
        )
        version = str(
            metadata.get("version")
            or frontmatter_metadata.get("version")
            or "0.0.0"
        )
        approvals = spec.get("approvalRequired")
        if approvals is None:
            approvals_map = _mapping(spec.get("approvals"))
            approvals = [
                action for action, policy in approvals_map.items() if policy == "required"
            ]
        manifest = SkillManifest(
            id=skill_id,
            version=version,
            name=frontmatter.name,
            description=frontmatter.description,
            type=SkillType(str(spec.get("type", SkillType.WORKFLOW.value))),
            owner=str(metadata.get("owner") or frontmatter_metadata.get("owner") or "package"),
            input_schema=_mapping(spec.get("inputSchema")) or {"type": "object"},
            output_schema=_mapping(spec.get("outputSchema")) or {"type": "object"},
            required_scopes=_string_list(spec.get("requiredScopes")),
            risk_level=str(spec.get("riskLevel", "low")),
            approval_required=_string_list(approvals),
            visibility=SkillVisibility(str(spec.get("visibility", SkillVisibility.TENANT.value))),
            visible_to_department_ids=_string_list(spec.get("visibleToDepartmentIds")),
            visible_to_workspace_ids=_string_list(spec.get("visibleToWorkspaceIds")),
            visible_to_user_ids=_string_list(spec.get("visibleToUserIds")),
            runtime=SkillRuntime(
                sandbox=str(runtime.get("sandbox") or runtime.get("image") or "skill-package"),
                timeout_seconds=int(runtime.get("timeoutSeconds", 1800)),
            ),
            billing_meters=_string_list(spec.get("billingMeters")),
            tests=_string_list(spec.get("tests")),
            evals=_string_list(spec.get("evals")),
        )
    _validate_identifier(manifest.id, "skill id")
    _validate_version(manifest.version)
    metadata = _mapping(config.get("metadata"))
    if metadata.get("id") is not None and metadata["id"] != manifest.id:
        raise SkillPackageError("taroai.yaml metadata.id conflicts with the manifest")
    if metadata.get("version") is not None and metadata["version"] != manifest.version:
        raise SkillPackageError("taroai.yaml metadata.version conflicts with the manifest")
    return manifest


def _parse_dependencies(config: Mapping[str, Any]) -> tuple[SkillDependency, ...]:
    spec = _mapping(config.get("spec"))
    raw_dependencies = spec.get("dependencies", [])
    if raw_dependencies is None:
        return ()
    if not isinstance(raw_dependencies, list):
        raise SkillPackageError("taroai.yaml spec.dependencies must be an array")
    dependencies: list[SkillDependency] = []
    seen: set[str] = set()
    for raw in raw_dependencies:
        if not isinstance(raw, dict):
            raise SkillPackageError("each dependency must be a JSON object")
        try:
            dependency = SkillDependency(
                id=raw["id"],
                version=raw["version"],
                package_digest=raw.get("packageDigest") or raw.get("package_digest"),
            )
        except (KeyError, ValueError) as error:
            raise SkillPackageError(f"invalid pinned dependency: {error}") from error
        if dependency.id.casefold() in seen:
            raise SkillPackageError(f"duplicate dependency: {dependency.id}")
        seen.add(dependency.id.casefold())
        dependencies.append(dependency)
    return tuple(sorted(dependencies, key=lambda value: (value.id, value.version)))


def _read_release_notes(
    files: Mapping[str, SkillPackageFile],
    config: Mapping[str, Any],
) -> str | None:
    for path in _RELEASE_NOTE_PATHS:
        item = files.get(path)
        if item is not None:
            return _decode_utf8(item.content, path)
    metadata = _mapping(config.get("metadata"))
    value = metadata.get("releaseNotes")
    if value is None:
        return None
    if not isinstance(value, str):
        raise SkillPackageError("taroai.yaml metadata.releaseNotes must be a string")
    return value


def _assert_safe_zip_member(
    info: zipfile.ZipInfo,
    normalized_path: str,
    limits: SkillPackageLimits,
) -> None:
    if info.flag_bits & 0x1:
        raise SkillPackageError(f"encrypted ZIP members are forbidden: {normalized_path}")
    unix_mode = info.external_attr >> 16
    file_type = stat.S_IFMT(unix_mode)
    if file_type == stat.S_IFLNK:
        raise SkillPackageError(f"symbolic links are forbidden: {normalized_path}")
    if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
        raise SkillPackageError(f"special ZIP member is forbidden: {normalized_path}")
    if info.file_size > limits.max_file_bytes:
        raise SkillPackageError(f"ZIP member exceeds the file size limit: {normalized_path}")
    if info.file_size:
        if info.compress_size <= 0:
            raise SkillPackageError(f"invalid compressed size for ZIP member: {normalized_path}")
        ratio = info.file_size / info.compress_size
        if ratio > limits.max_compression_ratio:
            raise SkillPackageError(f"ZIP member exceeds compression-ratio limit: {normalized_path}")


def _assert_not_credential_path(path: str) -> None:
    basename = path.rsplit("/", 1)[-1].casefold()
    if basename in _CREDENTIAL_FILENAMES or basename.startswith(".env."):
        raise SkillPackageError(f"credential filename is forbidden in a skill package: {path}")
    if basename.endswith(_CREDENTIAL_SUFFIXES):
        raise SkillPackageError(f"credential-like file extension is forbidden: {path}")
    if "credential" in basename and basename.endswith((".json", ".yaml", ".yml")):
        raise SkillPackageError(f"credential-like filename is forbidden: {path}")


def _normalize_archive_root(files: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
    if any(path == "SKILL.md" for path, _content in files):
        return files
    first_parts = {path.split("/", 1)[0] for path, _content in files}
    if len(first_parts) != 1:
        raise SkillPackageError("SKILL.md is not at the package root")
    wrapper = next(iter(first_parts))
    prefix = f"{wrapper}/"
    stripped = [
        (path[len(prefix) :], content)
        for path, content in files
        if path.startswith(prefix) and len(path) > len(prefix)
    ]
    if not any(path == "SKILL.md" for path, _content in stripped):
        raise SkillPackageError("ZIP wrapper root does not contain SKILL.md")
    return stripped


def _select_subdirectory(
    files: list[tuple[str, bytes]],
    subdirectory: str,
) -> list[tuple[str, bytes]]:
    normalized = normalize_package_path(subdirectory.strip("/\\"))
    prefix = f"{normalized}/"
    selected = [
        (path[len(prefix) :], content)
        for path, content in files
        if path.startswith(prefix) and len(path) > len(prefix)
    ]
    if not selected:
        raise SkillPackageError(f"skill package subdirectory was not found: {normalized}")
    if not any(path == "SKILL.md" for path, _content in selected):
        raise SkillPackageError("selected skill package subdirectory has no SKILL.md")
    return selected


def _assert_unique_paths(files: list[tuple[str, bytes]]) -> None:
    seen: dict[str, str] = {}
    for path, _content in files:
        key = unicodedata.normalize("NFC", path).casefold()
        if key in seen:
            raise SkillPackageError(
                f"duplicate or case-colliding skill package paths: {seen[key]!r}, {path!r}"
            )
        seen[key] = path


def _decode_utf8(content: bytes, path: str) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise SkillPackageError(f"{path} must be UTF-8") from error


def _mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise SkillPackageError("expected a JSON object")
    return dict(value)


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise SkillPackageError("expected an array of non-empty strings")
    return list(value)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._").lower()
    if not slug:
        raise SkillPackageError("frontmatter name cannot be converted into a skill id")
    return slug[:200]


def _validate_identifier(value: str, label: str) -> None:
    if not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{label} must contain only letters, digits, dots, underscores, and hyphens")


def _validate_version(value: str) -> None:
    if not _VERSION_RE.fullmatch(value):
        raise ValueError("skill version contains unsafe characters")


def _validate_sha256(value: str, label: str) -> None:
    if not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA-256 hex digest")
