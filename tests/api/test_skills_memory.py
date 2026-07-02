from taroai.memory import InMemoryMemoryService, MemoryScopeType, MemoryWriteRequest
from taroai.skills import InMemorySkillRegistry, SkillManifest, SkillRuntime, SkillType


def test_skill_registry_registers_and_lists_pydantic_manifests():
    registry = InMemorySkillRegistry()
    manifest = SkillManifest(
        id="ecommerce.price_monitor",
        version="1.0.0",
        name="Price Monitor",
        description="Monitor competitor pricing.",
        type=SkillType.BROWSER,
        owner="solutions/ecommerce",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        required_scopes=["browser.read", "storage.write"],
        runtime=SkillRuntime(sandbox="browser", timeout_seconds=1800),
        billing_meters=["sandbox_minutes", "browser_actions"],
    )

    registered = registry.register(manifest)

    assert registered == manifest
    assert registry.get("ecommerce.price_monitor").version == "1.0.0"
    assert registry.list() == [manifest]


def test_skill_registry_manages_tenant_scoped_lifecycle():
    registry = InMemorySkillRegistry()
    manifest = SkillManifest(
        id="sales.crm_lookup",
        version="1.0.0",
        name="CRM Lookup",
        description="Look up account context from CRM.",
        type=SkillType.API,
        owner="solutions/sales",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        required_scopes=["crm.read"],
        runtime=SkillRuntime(sandbox="api", timeout_seconds=60),
        billing_meters=["tool_call_count"],
    )

    draft = registry.register_for_tenant(
        tenant_id="tenant_acme",
        created_by_user_id="user_1",
        manifest=manifest,
    )
    published = registry.publish("tenant_acme", "sales.crm_lookup")
    disabled = registry.disable("tenant_acme", "sales.crm_lookup")

    assert draft.status == "draft"
    assert published.status == "published"
    assert disabled.status == "disabled"
    assert registry.get_for_tenant("tenant_acme", "sales.crm_lookup").status == "disabled"
    assert registry.list_for_tenant("tenant_acme") == [disabled]
    assert registry.list_for_tenant("tenant_other") == []


def test_skill_registry_installs_and_toggles_workspace_skill():
    registry = InMemorySkillRegistry()
    manifest = SkillManifest(
        id="sales.crm_lookup",
        version="1.0.0",
        name="CRM Lookup",
        description="Look up account context from CRM.",
        type=SkillType.API,
        owner="solutions/sales",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        required_scopes=["crm.read"],
        runtime=SkillRuntime(sandbox="api", timeout_seconds=60),
        billing_meters=["tool_call_count"],
    )
    registry.register_for_tenant(
        tenant_id="tenant_acme",
        created_by_user_id="user_1",
        manifest=manifest,
    )
    registry.publish("tenant_acme", "sales.crm_lookup")

    installed = registry.install_for_workspace(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        skill_id="sales.crm_lookup",
        installed_by_user_id="user_1",
    )
    disabled = registry.disable_for_workspace(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        skill_id="sales.crm_lookup",
    )
    enabled = registry.enable_for_workspace(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        skill_id="sales.crm_lookup",
    )

    assert installed.status == "enabled"
    assert disabled.status == "disabled"
    assert enabled.status == "enabled"
    assert registry.list_for_workspace("tenant_acme", "workspace_sales") == [enabled]
    assert registry.list_for_workspace("tenant_acme", "workspace_support") == []


def test_memory_service_writes_and_lists_records_by_scope():
    service = InMemoryMemoryService()

    memory = service.write(
        MemoryWriteRequest(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            scope_type=MemoryScopeType.TEAM,
            scope_id="team_sales",
            source_run_id="run_123",
            content="Use concise prospect briefs.",
            created_by="user_1",
        )
    )

    assert memory.id.startswith("memory_")
    assert memory.scope_type == MemoryScopeType.TEAM
    assert service.list_by_scope("tenant_acme", MemoryScopeType.TEAM, "team_sales") == [memory]
    assert service.list_by_scope("tenant_other", MemoryScopeType.TEAM, "team_sales") == []
