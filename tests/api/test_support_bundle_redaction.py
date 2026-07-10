import json
import zipfile
from pathlib import Path

import pytest

from taroai.support.redaction import (
    SupportBundleRedactionConfig,
    SupportBundleRedactionReport,
    redact_support_bundle_archive,
)


def write_zip(path: Path, entries: dict[str, bytes | str]) -> None:
    with zipfile.ZipFile(path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            data = content.encode("utf-8") if isinstance(content, str) else content
            archive.writestr(name, data)


def test_support_bundle_redactor_writes_sanitized_archive_and_evidence(
    tmp_path: Path,
):
    input_path = tmp_path / "support-bundle.zip"
    output_path = tmp_path / "support-bundle-redacted.zip"
    evidence_path = tmp_path / "support-bundle-redaction.json"
    key_value = "sk-" + ("A" * 24)
    signed_url = (
        "https://minio.local/taroai/report.md?"
        "X-Amz-Algorithm=AWS4-HMAC-SHA256&"
        "X-Amz-Credential=tenant-access&"
        "X-Amz-Signature=abcdef1234567890"
    )
    bearer_token = "Bearer user-session-token-1234567890"
    connection_url = "postgresql://app_user:tenant_password@postgres:5432/taroai"
    prompt_text = "summarize private renewal notes"
    connector_body = {"customer": "Acme", "token": "connector-secret"}
    write_zip(
        input_path,
        {
            "logs/api.log": "\n".join(
                [
                    f"model_key={key_value}",
                    f"authorization={bearer_token}",
                    f"artifact_url={signed_url}",
                    f"database_url={connection_url}",
                ]
            ),
            "logs/worker.jsonl": json.dumps(
                {
                    "prompt": prompt_text,
                    "connector_payload": connector_body,
                    "safe": "kept",
                }
            ),
        },
    )

    report = redact_support_bundle_archive(
        SupportBundleRedactionConfig(
            input_path=input_path,
            output_path=output_path,
            evidence_path=evidence_path,
        )
    )

    assert report.valid is True
    assert report.redacted_entry_count == 2
    assert report.finding_count_by_category["api_key"] == 1
    assert report.finding_count_by_category["bearer_token"] == 1
    assert report.finding_count_by_category["signed_url"] == 1
    assert report.finding_count_by_category["connection_string"] == 1
    assert report.finding_count_by_category["sensitive_field"] == 2

    with zipfile.ZipFile(output_path) as archive:
        api_log = archive.read("logs/api.log").decode("utf-8")
        worker_log = archive.read("logs/worker.jsonl").decode("utf-8")

    assert key_value not in api_log
    assert bearer_token not in api_log
    assert signed_url not in api_log
    assert connection_url not in api_log
    assert prompt_text not in worker_log
    assert "connector-secret" not in worker_log
    assert '"safe": "kept"' in worker_log
    assert "[REDACTED:api_key]" in api_log
    assert "[REDACTED:bearer_token]" in api_log
    assert "[REDACTED:signed_url]" in api_log
    assert "[REDACTED:connection_string]" in api_log
    assert "[REDACTED:sensitive_field]" in worker_log

    evidence = evidence_path.read_text()
    parsed = SupportBundleRedactionReport.model_validate_json(evidence)
    assert parsed.output_path == output_path
    assert key_value not in evidence
    assert bearer_token not in evidence
    assert signed_url not in evidence
    assert connection_url not in evidence
    assert prompt_text not in evidence
    assert "connector-secret" not in evidence


def test_support_bundle_redactor_preserves_binary_entries(tmp_path: Path):
    input_path = tmp_path / "support-bundle.zip"
    output_path = tmp_path / "support-bundle-redacted.zip"
    evidence_path = tmp_path / "support-bundle-redaction.json"
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00"
    write_zip(
        input_path,
        {
            "captures/browser.png": png_bytes,
            "logs/api.log": "Authorization: Bearer session-token-abcdefgh",
        },
    )

    report = redact_support_bundle_archive(
        SupportBundleRedactionConfig(
            input_path=input_path,
            output_path=output_path,
            evidence_path=evidence_path,
        )
    )

    with zipfile.ZipFile(output_path) as archive:
        assert archive.read("captures/browser.png") == png_bytes
        assert "session-token-abcdefgh" not in archive.read("logs/api.log").decode("utf-8")

    assert report.binary_entry_count == 1
    assert report.redacted_entry_count == 1


def test_support_bundle_redactor_redacts_plain_text_sensitive_assignments(
    tmp_path: Path,
):
    input_path = tmp_path / "support-bundle.zip"
    output_path = tmp_path / "support-bundle-redacted.zip"
    evidence_path = tmp_path / "support-bundle-redaction.json"
    prompt_value = "summarize confidential renewal notes"
    connector_payload = '{"customer":"Acme","token":"connector-secret"}'
    access_token = "session-token-plain-text"
    write_zip(
        input_path,
        {
            "logs/plain.log": "\n".join(
                [
                    f'prompt="{prompt_value}"',
                    f"connector_payload={connector_payload}",
                    f"access_token={access_token}",
                    "safe_field=kept",
                ]
            ),
        },
    )

    report = redact_support_bundle_archive(
        SupportBundleRedactionConfig(
            input_path=input_path,
            output_path=output_path,
            evidence_path=evidence_path,
        )
    )

    with zipfile.ZipFile(output_path) as archive:
        log_text = archive.read("logs/plain.log").decode("utf-8")

    assert prompt_value not in log_text
    assert connector_payload not in log_text
    assert "connector-secret" not in log_text
    assert access_token not in log_text
    assert "safe_field=kept" in log_text
    assert log_text.count("[REDACTED:sensitive_field]") == 3
    assert report.finding_count_by_category["sensitive_field"] == 3
    assert prompt_value not in evidence_path.read_text()
    assert "connector-secret" not in evidence_path.read_text()


def test_support_bundle_redactor_redacts_sensitive_headers_and_cookies(
    tmp_path: Path,
):
    input_path = tmp_path / "support-bundle.zip"
    output_path = tmp_path / "support-bundle-redacted.zip"
    evidence_path = tmp_path / "support-bundle-redaction.json"
    api_key = "provider-key-value-123456"
    basic_token = "Basic dXNlcjpzZWNyZXQ="
    cookie_value = "taroai_session=session-secret; theme=dark"
    set_cookie_value = "refresh_token=refresh-secret; HttpOnly; Secure"
    write_zip(
        input_path,
        {
            "logs/headers.log": "\n".join(
                [
                    f"X-API-Key: {api_key}",
                    f"Authorization: {basic_token}",
                    f"Cookie: {cookie_value}",
                    f"Set-Cookie: {set_cookie_value}",
                    "Content-Type: application/json",
                ]
            ),
        },
    )

    report = redact_support_bundle_archive(
        SupportBundleRedactionConfig(
            input_path=input_path,
            output_path=output_path,
            evidence_path=evidence_path,
        )
    )

    with zipfile.ZipFile(output_path) as archive:
        log_text = archive.read("logs/headers.log").decode("utf-8")

    assert api_key not in log_text
    assert basic_token not in log_text
    assert "session-secret" not in log_text
    assert "refresh-secret" not in log_text
    assert "Content-Type: application/json" in log_text
    assert log_text.count("[REDACTED:sensitive_field]") == 4
    assert report.finding_count_by_category["sensitive_field"] == 4
    evidence = evidence_path.read_text()
    assert api_key not in evidence
    assert "session-secret" not in evidence
    assert "refresh-secret" not in evidence


def test_support_bundle_redactor_redacts_credentialed_http_urls(
    tmp_path: Path,
):
    input_path = tmp_path / "support-bundle.zip"
    output_path = tmp_path / "support-bundle-redacted.zip"
    evidence_path = tmp_path / "support-bundle-redaction.json"
    userinfo_url = "https://agent:secret-value@api.customer.local/v1/runs"
    query_token_url = (
        "https://api.customer.local/callback?access_token=secret-value"
        "&state=kept"
    )
    write_zip(
        input_path,
        {
            "logs/api.log": "\n".join(
                [
                    f"request_url={userinfo_url}",
                    json.dumps({"callback_url": query_token_url, "safe": "kept"}),
                ]
            ),
        },
    )

    report = redact_support_bundle_archive(
        SupportBundleRedactionConfig(
            input_path=input_path,
            output_path=output_path,
            evidence_path=evidence_path,
        )
    )

    with zipfile.ZipFile(output_path) as archive:
        log_text = archive.read("logs/api.log").decode("utf-8")

    assert userinfo_url not in log_text
    assert query_token_url not in log_text
    assert "secret-value" not in log_text
    assert '"safe": "kept"' in log_text
    assert "[REDACTED:credentialed_url]" in log_text
    assert report.finding_count_by_category["credentialed_url"] == 2
    evidence = evidence_path.read_text()
    assert userinfo_url not in evidence
    assert query_token_url not in evidence
    assert "secret-value" not in evidence


def test_support_bundle_redaction_config_rejects_overwriting_source(tmp_path: Path):
    bundle_path = tmp_path / "support-bundle.zip"
    write_zip(bundle_path, {"logs/api.log": "safe"})

    with pytest.raises(ValueError, match="output_path must differ"):
        SupportBundleRedactionConfig(
            input_path=bundle_path,
            output_path=bundle_path,
        )


def test_support_bundle_redactor_preserves_existing_output_archive_when_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    input_path = tmp_path / "support-bundle.zip"
    output_path = tmp_path / "support-bundle-redacted.zip"
    write_zip(
        input_path,
        {"logs/api.log": "Authorization: Bearer session-token-abcdefgh"},
    )
    write_zip(output_path, {"existing.txt": "keep"})

    original_writestr = zipfile.ZipFile.writestr

    def failing_writestr(self, zinfo_or_arcname, data, *args, **kwargs):
        archive_path = Path(getattr(self, "filename", ""))
        if archive_path.parent == tmp_path and output_path.name in archive_path.name:
            raise OSError("redacted archive write failed")
        return original_writestr(self, zinfo_or_arcname, data, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "writestr", failing_writestr)

    with pytest.raises(OSError, match="redacted archive write failed"):
        redact_support_bundle_archive(
            SupportBundleRedactionConfig(
                input_path=input_path,
                output_path=output_path,
            )
        )

    with zipfile.ZipFile(output_path) as archive:
        assert archive.read("existing.txt") == b"keep"
    assert not list(tmp_path.glob(f".{output_path.name}*.tmp"))


def test_support_bundle_redactor_preserves_existing_evidence_when_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    input_path = tmp_path / "support-bundle.zip"
    output_path = tmp_path / "support-bundle-redacted.zip"
    evidence_path = tmp_path / "support-bundle-redaction.json"
    original_evidence = '{"valid": true, "existing": "keep"}\n'
    write_zip(input_path, {"logs/api.log": "safe"})
    evidence_path.write_text(original_evidence, encoding="utf-8")

    original_write_text = Path.write_text

    def failing_write_text(self, data, *args, **kwargs):
        if self.parent == tmp_path and evidence_path.name in self.name:
            original_write_text(self, '{"partial": ', *args, **kwargs)
            raise OSError("evidence write failed")
        return original_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", failing_write_text)

    with pytest.raises(OSError, match="evidence write failed"):
        redact_support_bundle_archive(
            SupportBundleRedactionConfig(
                input_path=input_path,
                output_path=output_path,
                evidence_path=evidence_path,
            )
        )

    assert evidence_path.read_text(encoding="utf-8") == original_evidence
    assert not list(tmp_path.glob(f".{evidence_path.name}*.tmp"))
