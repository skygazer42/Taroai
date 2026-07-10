from pathlib import Path


def test_private_upgrade_rollback_runbook_documents_required_operator_path():
    runbook = Path("docs/operations/private-upgrade-rollback.md")

    text = runbook.read_text()

    required_phrases = [
        "backup",
        "migration compatibility",
        "license check",
        "package integrity",
        "release-transfer-evidence.json",
        "verify-release-package.sh",
        "image availability",
        "downtime window",
        "rollback prerequisites",
        "data migration caveats",
        "support bundle",
        "redaction",
        "python -m taroai.db.migration_cli",
        "--apply",
    ]
    for phrase in required_phrases:
        assert phrase in text


def test_air_gapped_runbook_documents_offline_constraints_before_sales_commit():
    runbook = Path("docs/operations/air-gapped-install.md")

    text = runbook.read_text()

    required_phrases = [
        "no outbound internet",
        "package transfer",
        "release-transfer-evidence.json",
        "verify-release-package.sh",
        "image import",
        "license file import",
        "offline dependency mirrors",
        "internal model gateway",
        "internal sandbox provider",
        "support bundle",
        "redaction",
    ]
    for phrase in required_phrases:
        assert phrase in text


def test_license_import_contract_documents_endpoint_security_and_response_shape():
    contract = Path("docs/contracts/license-import-contract.md")

    text = contract.read_text()

    required_phrases = [
        "POST /api/licenses/import",
        "licenses.manage",
        "signed offline license envelope",
        "tenant mismatch",
        "activated=true",
        "license.imported",
        "signature material",
    ]
    for phrase in required_phrases:
        assert phrase in text


def test_upgrade_matrix_records_app_migration_database_and_chart_versions():
    matrix_path = Path("infra/package/upgrade-matrix.md")

    text = matrix_path.read_text()

    required_headers = [
        "App Version",
        "Chart Version",
        "Migration Range",
        "PostgreSQL Version",
        "Redis Version",
        "Rollback Boundary",
    ]
    for header in required_headers:
        assert header in text
    assert "0.1.0" in text
    assert "001_initial" in text
    assert "PostgreSQL 16" in text
    assert "Redis 7" in text
    assert "private-upgrade-rollback.md" in text
