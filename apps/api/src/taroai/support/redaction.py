import argparse
import json
import os
import re
import tempfile
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


RedactionCategory = Literal[
    "api_key",
    "bearer_token",
    "credentialed_url",
    "signed_url",
    "connection_string",
    "sensitive_field",
]


class SupportBundleRedactionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_path: Path
    output_path: Path
    evidence_path: Path | None = None

    @model_validator(mode="after")
    def validate_paths(self) -> "SupportBundleRedactionConfig":
        input_path = self.input_path.resolve()
        output_path = self.output_path.resolve()
        if input_path == output_path:
            raise ValueError("output_path must differ from input_path")
        self.input_path = input_path
        self.output_path = output_path
        if self.evidence_path is not None:
            self.evidence_path = self.evidence_path.resolve()
        return self


class SupportBundleRedactionFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry: str = Field(min_length=1)
    category: RedactionCategory
    count: int = Field(ge=1)


class SupportBundleRedactionReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_path: Path
    output_path: Path
    evidence_path: Path | None = None
    valid: bool
    file_count: int = Field(ge=0)
    text_entry_count: int = Field(ge=0)
    binary_entry_count: int = Field(ge=0)
    redacted_entry_count: int = Field(ge=0)
    finding_count_by_category: dict[str, int] = Field(default_factory=dict)
    findings: list[SupportBundleRedactionFinding] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


SENSITIVE_FIELD_NAMES = {
    "access_token",
    "api_key",
    "authorization",
    "connection_string",
    "connector_payload",
    "cookie",
    "database_url",
    "model_prompt",
    "password",
    "prompt",
    "raw_connector_payload",
    "refresh_token",
    "secret",
    "set_cookie",
    "signed_url",
    "token",
    "x_api_key",
}


TEXT_PATTERNS: list[tuple[RedactionCategory, re.Pattern[str]]] = [
    ("api_key", re.compile(r"sk-[A-Za-z0-9_-]{16,}")),
    (
        "bearer_token",
        re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", flags=re.IGNORECASE),
    ),
    (
        "credentialed_url",
        re.compile(
            r"https?://[^\s\"'<>/@]+:[^\s\"'<>/@]+@[^\s\"'<>]+",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "credentialed_url",
        re.compile(
            r"https?://[^\s\"'<>]*(?:[?&](?:access_token|refresh_token|"
            r"id_token|api_key|x-api-key|token|signature|sig|client_secret|"
            r"password)=)"
            r"[^\s\"'<>]*",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "signed_url",
        re.compile(
            r"https?://[^\s\"'<>]*(?:X-Amz-Signature|X-Amz-Credential|Signature=)"
            r"[^\s\"'<>]*",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "connection_string",
        re.compile(
            r"\b(?:postgresql|postgres|mysql|redis|mongodb(?:\+srv)?|amqp|s3)"
            r"://[^\s\"'<>]+",
            flags=re.IGNORECASE,
        ),
    ),
]


SENSITIVE_FIELD_ASSIGNMENT_PATTERN = re.compile(
    r"(?P<prefix>\b(?P<key>[A-Za-z_][A-Za-z0-9_-]*)\b\s*(?:=|:)\s*)"
    r"(?P<quote>[\"'])?(?P<value>[^\r\n]*?)(?P=quote)?(?=$|\r?\n)",
    flags=re.IGNORECASE,
)


def redact_support_bundle_archive(
    config: SupportBundleRedactionConfig,
) -> SupportBundleRedactionReport:
    findings: list[SupportBundleRedactionFinding] = []
    errors: list[str] = []
    text_entry_count = 0
    binary_entry_count = 0
    redacted_entries: set[str] = set()
    file_count = 0

    config.output_path.parent.mkdir(parents=True, exist_ok=True)
    if config.evidence_path is not None:
        config.evidence_path.parent.mkdir(parents=True, exist_ok=True)

    temp_output_path: Path | None = None
    try:
        with zipfile.ZipFile(config.input_path) as source:
            temp_output_path = make_atomic_output_path(config.output_path)
            with zipfile.ZipFile(
                temp_output_path,
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
            ) as target:
                for item in source.infolist():
                    if item.is_dir():
                        target.writestr(clone_zip_info(item), b"")
                        continue

                    file_count += 1
                    content = source.read(item.filename)
                    text = decode_text_entry(content)
                    if text is None:
                        binary_entry_count += 1
                        target.writestr(clone_zip_info(item), content)
                        continue

                    text_entry_count += 1
                    redacted_text, entry_findings = redact_text_entry(item.filename, text)
                    if entry_findings:
                        redacted_entries.add(item.filename)
                        findings.extend(entry_findings)
                    target.writestr(
                        clone_zip_info(item),
                        redacted_text.encode("utf-8"),
                    )
            os.replace(temp_output_path, config.output_path)
            temp_output_path = None
    except zipfile.BadZipFile as error:
        errors.append(f"support bundle is not a readable zip file: {error}")
    except FileNotFoundError as error:
        errors.append(f"support bundle file is missing: {error}")
    finally:
        if temp_output_path is not None:
            temp_output_path.unlink(missing_ok=True)

    finding_counts = Counter()
    for finding in findings:
        finding_counts[finding.category] += finding.count

    report = SupportBundleRedactionReport(
        input_path=config.input_path,
        output_path=config.output_path,
        evidence_path=config.evidence_path,
        valid=not errors,
        file_count=file_count,
        text_entry_count=text_entry_count,
        binary_entry_count=binary_entry_count,
        redacted_entry_count=len(redacted_entries),
        finding_count_by_category=dict(sorted(finding_counts.items())),
        findings=findings,
        errors=errors,
    )
    if config.evidence_path is not None:
        atomic_write_text(
            config.evidence_path,
            report.model_dump_json(indent=2),
            encoding="utf-8",
        )
    return report


def redact_text_entry(
    entry: str,
    text: str,
) -> tuple[str, list[SupportBundleRedactionFinding]]:
    findings: list[SupportBundleRedactionFinding] = []
    redacted_text, sensitive_field_count = redact_json_lines(text)

    for category, pattern in TEXT_PATTERNS:
        redacted_text, count = pattern.subn(redaction_marker(category), redacted_text)
        if count:
            findings.append(
                SupportBundleRedactionFinding(
                    entry=entry,
                    category=category,
                    count=count,
                )
            )
    redacted_text, text_sensitive_field_count = redact_text_sensitive_assignments(
        redacted_text
    )
    sensitive_field_count += text_sensitive_field_count
    if sensitive_field_count:
        findings.append(
            SupportBundleRedactionFinding(
                entry=entry,
                category="sensitive_field",
                count=sensitive_field_count,
            )
        )
    return redacted_text, findings


def redact_text_sensitive_assignments(text: str) -> tuple[str, int]:
    count = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal count
        key = normalized_key(match.group("key"))
        value = match.group("value") or ""
        if not is_sensitive_field_key(key):
            return match.group(0)
        if value == redaction_marker("sensitive_field"):
            return match.group(0)
        if value.strip().startswith("[REDACTED:"):
            return match.group(0)
        if not value.strip():
            return match.group(0)
        count += 1
        return f'{match.group("prefix")}{redaction_marker("sensitive_field")}'

    return SENSITIVE_FIELD_ASSIGNMENT_PATTERN.sub(replace, text), count


def redact_json_lines(text: str) -> tuple[str, int]:
    redacted_lines: list[str] = []
    redaction_count = 0
    changed = False
    for line in text.splitlines(keepends=True):
        line_body = line.rstrip("\r\n")
        line_end = line[len(line_body) :]
        if not line_body.lstrip().startswith(("{", "[")):
            redacted_lines.append(line)
            continue
        try:
            parsed = json.loads(line_body)
        except json.JSONDecodeError:
            redacted_lines.append(line)
            continue
        redacted_value, count = redact_json_value(parsed)
        if count == 0:
            redacted_lines.append(line)
            continue
        changed = True
        redaction_count += count
        redacted_lines.append(
            json.dumps(redacted_value, ensure_ascii=False, sort_keys=True) + line_end
        )
    if not changed:
        return text, 0
    return "".join(redacted_lines), redaction_count


def redact_json_value(value: Any) -> tuple[Any, int]:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        count = 0
        for key, item in value.items():
            if is_sensitive_field_key(normalized_key(key)):
                redacted[key] = redaction_marker("sensitive_field")
                count += 1
                continue
            redacted_item, item_count = redact_json_value(item)
            redacted[key] = redacted_item
            count += item_count
        return redacted, count
    if isinstance(value, list):
        redacted_items = []
        count = 0
        for item in value:
            redacted_item, item_count = redact_json_value(item)
            redacted_items.append(redacted_item)
            count += item_count
        return redacted_items, count
    return value, 0


def normalized_key(value: str) -> str:
    return value.replace("-", "_").lower()


def is_sensitive_field_key(key: str) -> bool:
    if key in SENSITIVE_FIELD_NAMES:
        return True
    return key.endswith(
        (
            "_api_key",
            "_connection_string",
            "_password",
            "_secret",
            "_signed_url",
            "_token",
        )
    )


def redaction_marker(category: RedactionCategory) -> str:
    return f"[REDACTED:{category}]"


def make_atomic_output_path(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        delete=False,
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    ) as temp_file:
        return Path(temp_file.name)


def atomic_write_text(path: Path, content: str, encoding: str) -> None:
    temp_path = make_atomic_output_path(path)
    try:
        temp_path.write_text(content, encoding=encoding)
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def decode_text_entry(content: bytes) -> str | None:
    if b"\x00" in content:
        return None
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return None


def clone_zip_info(item: zipfile.ZipInfo) -> zipfile.ZipInfo:
    cloned = zipfile.ZipInfo(item.filename)
    cloned.date_time = item.date_time
    cloned.external_attr = item.external_attr
    cloned.comment = item.comment
    cloned.compress_type = zipfile.ZIP_DEFLATED
    return cloned


def parse_args(argv: list[str] | None = None) -> SupportBundleRedactionConfig:
    parser = argparse.ArgumentParser(
        description="Redact sensitive values from a customer support bundle zip."
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--evidence-output", default=None)
    parsed = parser.parse_args(argv)
    return SupportBundleRedactionConfig(
        input_path=Path(parsed.input),
        output_path=Path(parsed.output),
        evidence_path=Path(parsed.evidence_output) if parsed.evidence_output else None,
    )


def main(argv: list[str] | None = None) -> int:
    config = parse_args(argv)
    report = redact_support_bundle_archive(config)
    print(report.model_dump_json(indent=2))
    return 0 if report.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
