from datetime import timedelta

from taroai.billing import (
    BillingInvoiceQuery,
    BillingInvoiceService,
    BillingPricingRule,
    BillingPricingService,
)
from taroai.domain import BillingMeterEvent, utc_now


def test_billing_pricing_uses_most_specific_matching_rule():
    service = BillingPricingService(
        rules=[
            BillingPricingRule(
                meter_type="model_tokens_input",
                unit="token",
                price_per_unit=0.006,
                pricing_unit_quantity=1000,
            ),
            BillingPricingRule(
                meter_type="model_tokens_input",
                unit="token",
                tenant_id="tenant_acme",
                price_per_unit=0.004,
                pricing_unit_quantity=1000,
            ),
            BillingPricingRule(
                meter_type="model_tokens_input",
                unit="token",
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                provider="openai_compatible",
                model="gpt-enterprise-planner",
                price_per_unit=0.003,
                pricing_unit_quantity=1000,
            ),
        ]
    )

    specific = service.estimate_cost(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        meter_type="model_tokens_input",
        quantity=120,
        unit="token",
        provider="openai_compatible",
        model="gpt-enterprise-planner",
    )
    tenant = service.estimate_cost(
        tenant_id="tenant_acme",
        workspace_id="workspace_support",
        meter_type="model_tokens_input",
        quantity=120,
        unit="token",
        provider="another_provider",
        model="another-model",
    )
    generic = service.estimate_cost(
        tenant_id="tenant_other",
        workspace_id="workspace_sales",
        meter_type="model_tokens_input",
        quantity=120,
        unit="token",
        provider="another_provider",
        model="another-model",
    )
    missing = service.estimate_cost(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        meter_type="model_tokens_output",
        quantity=120,
        unit="token",
        provider="openai_compatible",
        model="gpt-enterprise-planner",
    )

    assert specific == 0.00036
    assert tenant == 0.00048
    assert generic == 0.00072
    assert missing is None


def test_billing_pricing_supports_per_call_rules():
    service = BillingPricingService(
        rules=[
            BillingPricingRule(
                meter_type="embedding_call_count",
                unit="call",
                provider="openai_compatible",
                price_per_unit=0.0001,
            )
        ]
    )

    assert service.estimate_cost(
        meter_type="embedding_call_count",
        quantity=3,
        unit="call",
        provider="openai_compatible",
        model="text-embedding-3-small",
    ) == 0.0003


def test_billing_pricing_matches_skill_specific_rule_before_generic_skill_rule():
    service = BillingPricingService(
        rules=[
            BillingPricingRule(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                meter_type="skill_call_count",
                unit="call",
                price_per_unit=0.20,
            ),
            BillingPricingRule(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                skill_id="sales.crm_lookup",
                meter_type="skill_call_count",
                unit="call",
                price_per_unit=0.08,
            ),
        ]
    )

    specific = service.estimate_cost(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        skill_id="sales.crm_lookup",
        meter_type="skill_call_count",
        quantity=3,
        unit="call",
    )
    generic = service.estimate_cost(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        skill_id="support.ticket_triage",
        meter_type="skill_call_count",
        quantity=3,
        unit="call",
    )

    assert specific == 0.24
    assert generic == 0.60


def test_billing_invoice_service_groups_period_usage_and_unpriced_events():
    now = utc_now()
    service = BillingInvoiceService()
    invoice = service.create_invoice(
        tenant_id="tenant_acme",
        meters=[
            BillingMeterEvent(
                id="meter_storage_1",
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                user_id="user_1",
                run_id="run_1",
                agent_id="agent_sales",
                meter_type="storage_bytes",
                quantity=128,
                unit="bytes",
                cost_estimate=0.03,
                created_at=now,
            ),
            BillingMeterEvent(
                id="meter_storage_2",
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                user_id="user_1",
                run_id="run_1",
                agent_id="agent_sales",
                meter_type="storage_bytes",
                quantity=64,
                unit="bytes",
                cost_estimate=None,
                created_at=now + timedelta(minutes=1),
            ),
            BillingMeterEvent(
                id="meter_model_1",
                tenant_id="tenant_acme",
                workspace_id="workspace_support",
                user_id="user_2",
                run_id="run_2",
                agent_id="agent_support",
                meter_type="model_call_count",
                quantity=1,
                unit="call",
                provider="openai_compatible",
                model="gpt-enterprise",
                cost_estimate=0.02,
                created_at=now + timedelta(minutes=2),
            ),
            BillingMeterEvent(
                id="meter_outside_period",
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                user_id="user_1",
                run_id="run_3",
                agent_id="agent_sales",
                meter_type="storage_bytes",
                quantity=999,
                unit="bytes",
                cost_estimate=9.99,
                created_at=now - timedelta(days=3),
            ),
        ],
        query=BillingInvoiceQuery(
            period_start=now - timedelta(hours=1),
            period_end=now + timedelta(hours=1),
            group_by="workspace_id",
        ),
    )

    assert invoice.tenant_id == "tenant_acme"
    assert invoice.currency == "USD"
    assert invoice.meter_event_count == 3
    assert invoice.unpriced_event_count == 1
    assert invoice.total_cost_estimate == 0.05
    assert [
        (
            line.group_value,
            line.meter_type,
            line.quantity,
            line.event_count,
            line.cost_estimate,
            line.unpriced_event_count,
        )
        for line in invoice.lines
    ] == [
        ("workspace_sales", "storage_bytes", 192.0, 2, 0.03, 1),
        ("workspace_support", "model_call_count", 1.0, 1, 0.02, 0),
    ]
