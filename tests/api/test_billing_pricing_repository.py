from pathlib import Path

from taroai.billing import BillingPricingRuleUpsert, SqlBillingPricingRuleStore
from taroai.db import DatabaseConfig, MigrationRunner


def test_sql_billing_pricing_rule_store_persists_tenant_and_workspace_rules(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'billing-pricing.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    store = SqlBillingPricingRuleStore(config=DatabaseConfig(url=database_url))

    tenant_rule = store.upsert_rule(
        BillingPricingRuleUpsert(
            tenant_id="tenant_acme",
            meter_type="model_tokens_input",
            unit="token",
            provider="openai_compatible",
            model="gpt-enterprise",
            price_per_unit=0.004,
            pricing_unit_quantity=1000,
            currency="USD",
            updated_by_user_id="admin_1",
        )
    )
    workspace_rule = store.upsert_rule(
        BillingPricingRuleUpsert(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            meter_type="model_tokens_input",
            unit="token",
            provider="openai_compatible",
            model="gpt-enterprise",
            price_per_unit=0.003,
            pricing_unit_quantity=1000,
            currency="USD",
            updated_by_user_id="admin_2",
        )
    )

    restarted = SqlBillingPricingRuleStore(config=DatabaseConfig(url=database_url))
    rules = restarted.list_rules("tenant_acme")
    all_rules = restarted.list_all_rules()

    assert tenant_rule.workspace_id is None
    assert workspace_rule.workspace_id == "workspace_sales"
    assert [rule.workspace_id for rule in rules] == [None, "workspace_sales"]
    assert rules[0].price_per_unit == 0.004
    assert rules[1].price_per_unit == 0.003
    assert rules[1].updated_by_user_id == "admin_2"
    assert rules[1].to_pricing_rule().workspace_id == "workspace_sales"
    assert restarted.list_rules("tenant_other") == []
    assert [rule.tenant_id for rule in all_rules] == ["tenant_acme", "tenant_acme"]


def test_sql_billing_pricing_rule_store_persists_skill_specific_rules(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'billing-skill-pricing.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    store = SqlBillingPricingRuleStore(config=DatabaseConfig(url=database_url))

    store.upsert_rule(
        BillingPricingRuleUpsert(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            meter_type="skill_call_count",
            unit="call",
            price_per_unit=0.20,
            currency="USD",
            updated_by_user_id="admin_1",
        )
    )
    store.upsert_rule(
        BillingPricingRuleUpsert(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            skill_id="sales.crm_lookup",
            meter_type="skill_call_count",
            unit="call",
            price_per_unit=0.08,
            currency="USD",
            updated_by_user_id="admin_2",
        )
    )

    rules = SqlBillingPricingRuleStore(
        config=DatabaseConfig(url=database_url)
    ).list_rules("tenant_acme")

    assert [(rule.skill_id, rule.price_per_unit) for rule in rules] == [
        (None, 0.20),
        ("sales.crm_lookup", 0.08),
    ]
    assert rules[1].to_pricing_rule().skill_id == "sales.crm_lookup"
