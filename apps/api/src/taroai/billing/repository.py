import json
from datetime import datetime

from pydantic import BaseModel, Field

from taroai.billing.models import (
    BillingInvoice,
    BillingInvoiceRecord,
    BillingPricingRuleRecord,
    BillingPricingRuleUpsert,
)
from taroai.db import DatabaseConfig
from taroai.db.connection import connect_database
from taroai.domain import utc_now
from taroai.store import NotFoundError


class BillingInvoiceStore(BaseModel):
    def create_invoice(self, record: BillingInvoiceRecord) -> BillingInvoiceRecord:
        raise NotImplementedError

    def list_invoices(self, tenant_id: str) -> list[BillingInvoiceRecord]:
        raise NotImplementedError

    def get_invoice(self, tenant_id: str, invoice_id: str) -> BillingInvoiceRecord:
        raise NotImplementedError


class InMemoryBillingInvoiceStore(BillingInvoiceStore):
    invoices: dict[str, BillingInvoiceRecord] = Field(default_factory=dict)

    def create_invoice(self, record: BillingInvoiceRecord) -> BillingInvoiceRecord:
        self.invoices[self._key(record.tenant_id, record.invoice_id)] = record
        return record

    def list_invoices(self, tenant_id: str) -> list[BillingInvoiceRecord]:
        return sorted(
            [
                record
                for record in self.invoices.values()
                if record.tenant_id == tenant_id
            ],
            key=lambda record: (record.created_at, record.invoice_id),
        )

    def get_invoice(self, tenant_id: str, invoice_id: str) -> BillingInvoiceRecord:
        record = self.invoices.get(self._key(tenant_id, invoice_id))
        if record is None:
            raise NotFoundError(f"Billing invoice not found: {invoice_id}")
        return record

    def _key(self, tenant_id: str, invoice_id: str) -> str:
        return f"{tenant_id}:{invoice_id}"


class SqlBillingInvoiceStore(BillingInvoiceStore):
    config: DatabaseConfig

    def create_invoice(self, record: BillingInvoiceRecord) -> BillingInvoiceRecord:
        with self._connect() as connection:
            self._ensure_tenant(connection, record.tenant_id)
            connection.execute(
                """
                INSERT INTO billing_invoices (
                    invoice_id, tenant_id, period_start, period_end, currency,
                    group_by, meter_event_count, unpriced_event_count,
                    total_cost_estimate, invoice, created_by_user_id, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.invoice_id,
                    record.tenant_id,
                    self._dt_optional(record.invoice.period_start),
                    self._dt_optional(record.invoice.period_end),
                    record.invoice.currency,
                    record.invoice.group_by,
                    record.invoice.meter_event_count,
                    record.invoice.unpriced_event_count,
                    record.invoice.total_cost_estimate,
                    self._json(record.invoice.model_dump(mode="json")),
                    record.created_by_user_id,
                    self._dt(record.created_at),
                ),
            )
        return record

    def list_invoices(self, tenant_id: str) -> list[BillingInvoiceRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM billing_invoices
                WHERE tenant_id = ?
                ORDER BY created_at, invoice_id
                """,
                (tenant_id,),
            ).fetchall()
        return [self._invoice_record_from_row(row) for row in rows]

    def get_invoice(self, tenant_id: str, invoice_id: str) -> BillingInvoiceRecord:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM billing_invoices
                WHERE tenant_id = ? AND invoice_id = ?
                """,
                (tenant_id, invoice_id),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Billing invoice not found: {invoice_id}")
        return self._invoice_record_from_row(row)

    def _invoice_record_from_row(self, row) -> BillingInvoiceRecord:
        return BillingInvoiceRecord(
            invoice_id=row["invoice_id"],
            tenant_id=row["tenant_id"],
            invoice=BillingInvoice.model_validate(self._loads(row["invoice"])),
            created_by_user_id=row["created_by_user_id"],
            created_at=self._parse_dt(row["created_at"]),
        )

    def _connect(self):
        return connect_database(self.config)

    def _ensure_tenant(self, connection, tenant_id: str) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO tenants (id, name, created_at) VALUES (?, ?, ?)",
            (tenant_id, tenant_id, self._dt(utc_now())),
        )

    def _json(self, value) -> str:
        return json.dumps(value)

    def _loads(self, value: str):
        return json.loads(value)

    def _dt_optional(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        return self._dt(value)

    def _dt(self, value: datetime) -> str:
        return value.isoformat()

    def _parse_dt(self, value: str) -> datetime:
        return datetime.fromisoformat(value)


class BillingPricingRuleStore(BaseModel):
    def upsert_rule(self, request: BillingPricingRuleUpsert) -> BillingPricingRuleRecord:
        raise NotImplementedError

    def list_rules(self, tenant_id: str) -> list[BillingPricingRuleRecord]:
        raise NotImplementedError

    def list_all_rules(self) -> list[BillingPricingRuleRecord]:
        raise NotImplementedError


class InMemoryBillingPricingRuleStore(BillingPricingRuleStore):
    rules: dict[str, BillingPricingRuleRecord] = Field(default_factory=dict)

    def upsert_rule(self, request: BillingPricingRuleUpsert) -> BillingPricingRuleRecord:
        request.to_pricing_rule()
        now = utc_now()
        key = self._key(
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
            skill_id=request.skill_id,
            meter_type=request.meter_type,
            unit=request.unit,
            provider=request.provider,
            model=request.model,
            currency=request.currency,
        )
        existing = self.rules.get(key)
        record = BillingPricingRuleRecord(
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
            skill_id=request.skill_id,
            meter_type=request.meter_type,
            unit=request.unit,
            price_per_unit=request.price_per_unit,
            pricing_unit_quantity=request.pricing_unit_quantity,
            provider=request.provider,
            model=request.model,
            currency=request.currency,
            updated_by_user_id=request.updated_by_user_id,
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
        )
        self.rules[key] = record
        return record

    def list_rules(self, tenant_id: str) -> list[BillingPricingRuleRecord]:
        return self._sort_rules(
            [rule for rule in self.rules.values() if rule.tenant_id == tenant_id]
        )

    def list_all_rules(self) -> list[BillingPricingRuleRecord]:
        return self._sort_rules(list(self.rules.values()))

    def _key(
        self,
        tenant_id: str,
        workspace_id: str | None,
        skill_id: str | None,
        meter_type: str,
        unit: str,
        provider: str | None,
        model: str | None,
        currency: str,
    ) -> str:
        return ":".join(
            [
                tenant_id,
                workspace_id or "",
                skill_id or "",
                meter_type,
                unit,
                provider or "",
                model or "",
                currency,
            ]
        )

    def _sort_rules(
        self,
        rules: list[BillingPricingRuleRecord],
    ) -> list[BillingPricingRuleRecord]:
        return sorted(
            rules,
            key=lambda rule: (
                rule.tenant_id,
                rule.workspace_id is not None,
                rule.workspace_id or "",
                rule.skill_id is not None,
                rule.skill_id or "",
                rule.meter_type,
                rule.unit,
                rule.provider or "",
                rule.model or "",
                rule.currency,
            ),
        )


class SqlBillingPricingRuleStore(BillingPricingRuleStore):
    config: DatabaseConfig

    def upsert_rule(self, request: BillingPricingRuleUpsert) -> BillingPricingRuleRecord:
        request.to_pricing_rule()
        now = utc_now()
        existing = self._get_rule_optional(request)
        record = BillingPricingRuleRecord(
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
            skill_id=request.skill_id,
            meter_type=request.meter_type,
            unit=request.unit,
            price_per_unit=request.price_per_unit,
            pricing_unit_quantity=request.pricing_unit_quantity,
            provider=request.provider,
            model=request.model,
            currency=request.currency,
            updated_by_user_id=request.updated_by_user_id,
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
        )
        with self._connect() as connection:
            self._ensure_tenant(connection, record.tenant_id)
            if record.workspace_id is not None:
                self._ensure_workspace(connection, record.tenant_id, record.workspace_id)
            connection.execute(
                """
                INSERT INTO billing_pricing_rules (
                    tenant_id, workspace_id, skill_id, meter_type, unit, provider, model,
                    currency, price_per_unit, pricing_unit_quantity,
                    updated_by_user_id, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, workspace_id, skill_id, meter_type, unit, provider, model, currency)
                DO UPDATE SET
                    price_per_unit = excluded.price_per_unit,
                    pricing_unit_quantity = excluded.pricing_unit_quantity,
                    updated_by_user_id = excluded.updated_by_user_id,
                    updated_at = excluded.updated_at
                """,
                (
                    record.tenant_id,
                    self._db_optional(record.workspace_id),
                    self._db_optional(record.skill_id),
                    record.meter_type,
                    record.unit,
                    self._db_optional(record.provider),
                    self._db_optional(record.model),
                    record.currency,
                    record.price_per_unit,
                    record.pricing_unit_quantity,
                    record.updated_by_user_id,
                    self._dt(record.created_at),
                    self._dt(record.updated_at),
                ),
            )
        return record

    def list_rules(self, tenant_id: str) -> list[BillingPricingRuleRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM billing_pricing_rules
                WHERE tenant_id = ?
                ORDER BY workspace_id, skill_id, meter_type, unit, provider, model, currency
                """,
                (tenant_id,),
            ).fetchall()
        return [self._record_from_row(row) for row in rows]

    def list_all_rules(self) -> list[BillingPricingRuleRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM billing_pricing_rules
                ORDER BY tenant_id, workspace_id, skill_id, meter_type, unit, provider, model, currency
                """
            ).fetchall()
        return [self._record_from_row(row) for row in rows]

    def _get_rule_optional(
        self,
        request: BillingPricingRuleUpsert,
    ) -> BillingPricingRuleRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM billing_pricing_rules
                WHERE tenant_id = ?
                    AND workspace_id = ?
                    AND skill_id = ?
                    AND meter_type = ?
                    AND unit = ?
                    AND provider = ?
                    AND model = ?
                    AND currency = ?
                """,
                (
                    request.tenant_id,
                    self._db_optional(request.workspace_id),
                    self._db_optional(request.skill_id),
                    request.meter_type,
                    request.unit,
                    self._db_optional(request.provider),
                    self._db_optional(request.model),
                    request.currency,
                ),
            ).fetchone()
        if row is None:
            return None
        return self._record_from_row(row)

    def _connect(self):
        return connect_database(self.config)

    def _ensure_tenant(self, connection, tenant_id: str) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO tenants (id, name, created_at) VALUES (?, ?, ?)",
            (tenant_id, tenant_id, self._dt(utc_now())),
        )

    def _ensure_workspace(self, connection, tenant_id: str, workspace_id: str) -> None:
        connection.execute(
            """
            INSERT OR IGNORE INTO workspaces (id, tenant_id, name, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (workspace_id, tenant_id, workspace_id, self._dt(utc_now())),
        )

    def _record_from_row(self, row) -> BillingPricingRuleRecord:
        return BillingPricingRuleRecord(
            tenant_id=row["tenant_id"],
            workspace_id=self._model_optional(row["workspace_id"]),
            skill_id=self._model_optional(row["skill_id"]),
            meter_type=row["meter_type"],
            unit=row["unit"],
            price_per_unit=row["price_per_unit"],
            pricing_unit_quantity=row["pricing_unit_quantity"],
            provider=self._model_optional(row["provider"]),
            model=self._model_optional(row["model"]),
            currency=row["currency"],
            updated_by_user_id=row["updated_by_user_id"],
            created_at=self._parse_dt(row["created_at"]),
            updated_at=self._parse_dt(row["updated_at"]),
        )

    def _db_optional(self, value: str | None) -> str:
        return value or ""

    def _model_optional(self, value: str) -> str | None:
        if value == "":
            return None
        return value

    def _dt(self, value: datetime) -> str:
        return value.isoformat()

    def _parse_dt(self, value: str) -> datetime:
        return datetime.fromisoformat(value)
