from pathlib import Path

import yaml


CHART_DIR = Path("infra/helm/taroai")


def read_yaml(path: Path) -> dict:
    with path.open() as file:
        return yaml.safe_load(file)


def test_helm_chart_metadata_and_values_define_private_packaging_path():
    chart = read_yaml(CHART_DIR / "Chart.yaml")
    values = read_yaml(CHART_DIR / "values.yaml")

    assert chart["apiVersion"] == "v2"
    assert chart["name"] == "taroai"
    assert chart["type"] == "application"
    assert chart["version"]
    assert chart["appVersion"]

    for section in [
        "api",
        "workers",
        "migration",
        "config",
        "secrets",
        "ingress",
        "serviceAccount",
        "networkPolicy",
        "sandboxRuntimePolicy",
        "sandboxController",
        "browserController",
        "web",
        "autoscaling",
        "persistence",
        "nodeSelector",
        "tolerations",
        "affinity",
    ]:
        assert section in values

    assert values["image"]["repository"] == "ghcr.io/creao-ai/taroai-api"
    assert values["secrets"]["create"] is False
    assert values["secrets"]["existingSecret"] == "taroai-runtime-secrets"
    assert (
        values["secrets"]["secretKeys"]["sandboxControllerApiKey"]
        == "TAROAI_SANDBOX_CONTROLLER_API_KEY"
    )
    assert (
        values["secrets"]["secretKeys"]["browserControllerApiKey"]
        == "TAROAI_BROWSER_CONTROLLER_API_KEY"
    )
    assert values["serviceAccount"]["create"] is True
    assert values["networkPolicy"]["enabled"] is True
    assert values["sandboxRuntimePolicy"]["enabled"] is True
    assert values["sandboxRuntimePolicy"]["podSecurity"]["enforce"] == "restricted"
    assert values["sandboxRuntimePolicy"]["resourceQuota"]["pods"] == "50"
    assert values["sandboxRuntimePolicy"]["limitRange"]["default"]["memory"] == "1Gi"
    assert (
        values["sandboxRuntimePolicy"]["networkPolicy"]["name"]
        == "sandbox-runtime-default-deny"
    )
    assert values["sandboxRuntimePolicy"]["networkPolicy"]["podSelector"] == {
        "app.kubernetes.io/name": "taroai-sandbox-session"
    }
    assert values["ingress"]["enabled"] is False
    assert values["web"]["enabled"] is True
    assert values["web"]["image"]["repository"] == "ghcr.io/creao-ai/taroai-web"
    assert values["web"]["service"]["port"] == 80
    assert values["web"]["service"]["containerPort"] == 8080
    assert values["web"]["readinessProbe"]["path"] == "/"
    assert values["config"]["TAROAI_SANDBOX_DOCKER_USER"] == "65532:65532"
    assert values["config"]["TAROAI_SANDBOX_CONTROLLER_BASE_URL"]
    assert values["config"]["TAROAI_SANDBOX_CONTROLLER_PROVIDER"] == "kubernetes"
    assert values["config"]["TAROAI_SANDBOX_CONTROLLER_SESSION_TTL_SECONDS"] == "1800"
    assert (
        values["config"]["TAROAI_SANDBOX_CONTROLLER_KUBERNETES_RUNTIME_CLASS_NAME"]
        == "gvisor"
    )
    assert (
        values["config"]["TAROAI_SANDBOX_CONTROLLER_KUBERNETES_RUNTIME_CLASS_REQUIRED"]
        == "true"
    )
    assert (
        values["config"]["TAROAI_SANDBOX_CONTROLLER_KUBERNETES_ALLOWED_IMAGES"]
        == "[\"ghcr.io/customer/sandbox-runtime@sha256:*\"]"
    )
    assert (
        values["config"]["TAROAI_SANDBOX_CONTROLLER_KUBERNETES_ORPHAN_CLEANUP_ENABLED"]
        == "false"
    )
    assert values["config"]["TAROAI_BROWSER_CONTROLLER_BASE_URL"]
    assert values["config"]["TAROAI_BROWSER_CONTROLLER_TIMEOUT_SECONDS"] == "30"
    assert values["config"]["TAROAI_BROWSER_CONTROLLER_NAVIGATION_ALLOWED_HOSTS"] == "[]"
    assert values["migration"]["runMigrations"] == "false"
    assert values["migration"]["command"] == ["/app/entrypoint.sh"]
    assert values["migration"]["args"] == [
        "python",
        "-m",
        "taroai.db.migration_cli",
        "--database-url",
        "$(TAROAI_DATABASE_URL)",
        "--migrations-path",
        "/app/migrations",
        "--apply",
    ]

    sandbox_controller = values["sandboxController"]
    assert sandbox_controller["enabled"] is False
    assert sandbox_controller["image"]["repository"] == (
        "ghcr.io/creao-ai/taroai-sandbox-controller"
    )
    assert sandbox_controller["service"]["port"] == 8002
    assert sandbox_controller["readinessProbe"]["path"] == "/healthz"
    assert sandbox_controller["securityContext"]["allowPrivilegeEscalation"] is False
    assert sandbox_controller["securityContext"]["readOnlyRootFilesystem"] is True
    assert sandbox_controller["securityContext"]["capabilities"]["drop"] == ["ALL"]

    browser_controller = values["browserController"]
    assert browser_controller["enabled"] is False
    assert browser_controller["image"]["repository"] == (
        "ghcr.io/creao-ai/taroai-browser-controller"
    )
    assert browser_controller["service"]["port"] == 8001
    assert browser_controller["readinessProbe"]["path"] == "/healthz"
    assert browser_controller["securityContext"]["allowPrivilegeEscalation"] is False
    assert browser_controller["securityContext"]["readOnlyRootFilesystem"] is True
    assert browser_controller["securityContext"]["capabilities"]["drop"] == ["ALL"]

    worker_kinds = values["workers"]["kinds"]
    assert set(worker_kinds) == {
        "agent",
        "cleanup",
        "connectorSync",
        "restoreDrillDue",
        "restoreDrillExecution",
        "restoreDrillEvidence",
        "restoreDrillScheduler",
        "triggerDue",
        "triggerScheduler",
    }
    assert worker_kinds["agent"]["replicas"] == 2
    assert worker_kinds["restoreDrillDue"]["args"]["pollIntervalSeconds"] == 5
    assert worker_kinds["restoreDrillExecution"]["args"]["pollIntervalSeconds"] == 5
    assert worker_kinds["restoreDrillEvidence"]["args"]["pollIntervalSeconds"] == 5
    assert worker_kinds["restoreDrillScheduler"]["args"]["pollIntervalSeconds"] == 300
    assert worker_kinds["triggerScheduler"]["args"]["pollIntervalSeconds"] == 30

    rendered_values = (CHART_DIR / "values.yaml").read_text()
    forbidden_literals = [
        "replace-with",
        "local_cloud_poc",
        "taroai_minio_password",
        "change_me_in_production",
    ]
    for literal in forbidden_literals:
        assert literal not in rendered_values


def test_helm_templates_cover_runtime_components_without_secret_literals():
    expected_templates = {
        "templates/README.md",
        "templates/_helpers.tpl",
        "templates/serviceaccount.yaml",
        "templates/configmap.yaml",
        "templates/api.yaml",
        "templates/worker.yaml",
        "templates/sandbox-controller.yaml",
        "templates/browser-controller.yaml",
        "templates/web.yaml",
        "templates/migration-job.yaml",
        "templates/network-policy.yaml",
        "templates/sandbox-runtime-policy.yaml",
        "templates/ingress.yaml",
        "templates/hpa.yaml",
    }
    assert {
        path.relative_to(CHART_DIR).as_posix()
        for path in CHART_DIR.glob("templates/*")
        if path.is_file()
    } == expected_templates

    template_text = "\n".join(
        path.read_text()
        for path in sorted((CHART_DIR / "templates").glob("*"))
        if path.is_file()
    )
    sandbox_template_text = (CHART_DIR / "templates/sandbox-controller.yaml").read_text()
    browser_template_text = (CHART_DIR / "templates/browser-controller.yaml").read_text()
    web_template_text = (CHART_DIR / "templates/web.yaml").read_text()
    for fragment in [
        "kind: Deployment",
        "kind: Job",
        "kind: Service",
        "kind: ServiceAccount",
        "kind: ConfigMap",
        "kind: NetworkPolicy",
        "kind: ResourceQuota",
        "kind: LimitRange",
        "sandboxRuntimePolicy.networkPolicy",
        "kind: Ingress",
        "kind: HorizontalPodAutoscaler",
        "taroai.sandbox.playwright_service:app",
        "taroai.sandbox.controller_service:app",
        ".Values.sandboxController.enabled",
        ".Values.browserController.enabled",
        ".Values.web.enabled",
        ".Values.sandboxRuntimePolicy.enabled",
        "pod-security.kubernetes.io/enforce",
        "TAROAI_SANDBOX_CONTROLLER_BASE_URL",
        "TAROAI_BROWSER_CONTROLLER_BASE_URL",
        "include \"taroai.fullname\" $root",
        ".Values.secrets.existingSecret",
        "TAROAI_RUN_MIGRATIONS",
    ]:
        assert fragment in template_text

    forbidden_literals = [
        "replace-with",
        "local_cloud_poc",
        "taroai_minio_password",
        "change_me_in_production",
        "stringData:",
    ]
    for literal in forbidden_literals:
        assert literal not in template_text

    assert "envFrom:" not in sandbox_template_text
    assert ".Values.secrets.secretKeys.sandboxControllerApiKey" in sandbox_template_text
    assert "configMapKeyRef:" in sandbox_template_text
    assert "TAROAI_SANDBOX_CONTROLLER_SESSION_TTL_SECONDS" in sandbox_template_text
    assert "TAROAI_SANDBOX_CONTROLLER_MAX_SESSIONS_PER_TENANT" in sandbox_template_text
    assert "TAROAI_SANDBOX_CONTROLLER_DOCKER_MEMORY_LIMIT" in sandbox_template_text
    assert "TAROAI_SANDBOX_CONTROLLER_KUBERNETES_RUNTIME_CLASS_REQUIRED" in sandbox_template_text
    assert "TAROAI_SANDBOX_CONTROLLER_KUBERNETES_ALLOWED_IMAGES" in sandbox_template_text
    assert "TAROAI_SANDBOX_CONTROLLER_KUBERNETES_ORPHAN_CLEANUP_ENABLED" in sandbox_template_text
    assert "envFrom:" not in browser_template_text
    assert ".Values.secrets.secretKeys.browserControllerApiKey" in browser_template_text
    assert "configMapKeyRef:" in browser_template_text
    assert "TAROAI_BROWSER_CONTROLLER_SESSION_TTL_SECONDS" in browser_template_text
    assert "TAROAI_BROWSER_CONTROLLER_MAX_SESSIONS_PER_TENANT" in browser_template_text
    for api_only_env in [
        "TAROAI_ACCESS_TOKEN_SECRET",
        "TAROAI_PASSWORD_HASH_SALT",
        "TAROAI_TENANT_BOOTSTRAP_TOKEN",
        "TAROAI_MODEL_GATEWAY_API_KEY",
        "TAROAI_OBJECT_STORAGE_SECRET_ACCESS_KEY",
    ]:
        assert api_only_env not in sandbox_template_text
        assert api_only_env not in browser_template_text
        assert api_only_env not in web_template_text
    assert "envFrom:" not in web_template_text
    assert ".Values.secrets.existingSecret" not in web_template_text
    assert "ghcr.io/creao-ai/taroai-web" in (CHART_DIR / "values.yaml").read_text()


def test_helm_template_readme_documents_web_workspace_packaging():
    readme = (CHART_DIR / "templates/README.md").read_text()

    assert "Web Workspace" in readme
    assert "Sandbox Controller" in readme
    assert "taroai-sandbox-controller" in readme
    assert "sandboxController.enabled" in readme
    assert "taroai-web" in readme
    assert "web.enabled" in readme
    assert "Frontend templates are intentionally absent" not in readme
