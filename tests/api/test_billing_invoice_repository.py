from pathlib import Path

from taroai.billing import (
    BillingInvoice,
    BillingInvoiceLine,
    BillingInvoiceRecord,
    SqlBillingInvoiceStore,
)
from taroai.db import DatabaseConfig, MigrationRunner
from taroai.domain import utc_now


def test_sql_billing_invoice_store_persists_invoice_snapshots(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'billing-invoices.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    store = SqlBillingInvoiceStore(config=DatabaseConfig(url=database_url))
    invoice = BillingInvoice(
        tenant_id="tenant_acme",
        currency="USD",
        group_by="workspace_id",
        meter_event_count=2,
        unpriced_event_count=1,
        total_cost_estimate=0.03,
        lines=[
            BillingInvoiceLine(
                group_by="workspace_id",
                group_value="workspace_sales",
                meter_type="storage_bytes",
                unit="bytes",
                quantity=128,
                event_count=2,
                cost_estimate=0.03,
                unpriced_event_count=1,
            )
        ],
    )

    created = store.create_invoice(
        BillingInvoiceRecord(
            invoice_id="invoice_1",
            tenant_id="tenant_acme",
            invoice=invoice,
            created_by_user_id="admin_1",
            created_at=utc_now(),
        )
    )

    restarted = SqlBillingInvoiceStore(config=DatabaseConfig(url=database_url))
    listed = restarted.list_invoices("tenant_acme")
    loaded = restarted.get_invoice("tenant_acme", "invoice_1")

    assert created.invoice_id == "invoice_1"
    assert [record.invoice_id for record in listed] == ["invoice_1"]
    assert loaded.invoice.total_cost_estimate == 0.03
    assert loaded.invoice.lines[0].group_value == "workspace_sales"
    assert loaded.created_by_user_id == "admin_1"
    assert restarted.list_invoices("tenant_other") == []
