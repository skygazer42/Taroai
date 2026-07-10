from pathlib import Path

import yaml


K8S_DIR = Path("infra/k8s")
RUNTIME_CONFIG = "taroai-runtime-config"
RUNTIME_SECRETS = "taroai-runtime-secrets"


def load_yaml_documents(path: str) -> list[dict]:
    with Path(path).open() as file:
        return [document for document in yaml.safe_load_all(file) if document]


def documents_by_kind_and_name(path: str) -> dict[tuple[str, str], dict]:
    return {
        (document["kind"], document["metadata"]["name"]): document
        for document in load_yaml_documents(path)
    }


def container_env(container: dict) -> dict[str, dict | str]:
    return {entry["name"]: entry for entry in container.get("env", [])}


def test_kubernetes_kustomization_lists_all_platform_manifests():
    with Path("infra/k8s/kustomization.yaml").open() as file:
        kustomization = yaml.safe_load(file)

    assert kustomization["apiVersion"] == "kustomize.config.k8s.io/v1beta1"
    assert kustomization["kind"] == "Kustomization"
    assert kustomization["namespace"] == "taroai"
    assert kustomization["resources"] == [
        "sandbox-runtime-policy.yaml",
        "configmap.yaml",
        "secrets.example.yaml",
        "postgres.yaml",
        "redis.yaml",
        "minio.yaml",
        "api.yaml",
        "sandbox-controller.yaml",
        "browser-controller.yaml",
        "web.yaml",
        "worker.yaml",
        "network-policy.yaml",
    ]


def test_kubernetes_sandbox_runtime_policy_defines_namespace_guards():
    resources = documents_by_kind_and_name("infra/k8s/sandbox-runtime-policy.yaml")

    namespace = resources[("Namespace", "taroai")]
    quota = resources[("ResourceQuota", "taroai-sandbox-runtime-quota")]
    limit_range = resources[("LimitRange", "taroai-sandbox-runtime-limits")]
    default_deny = resources[("NetworkPolicy", "taroai-sandbox-runtime-default-deny")]

    labels = namespace["metadata"]["labels"]
    assert labels["pod-security.kubernetes.io/enforce"] == "restricted"
    assert labels["pod-security.kubernetes.io/audit"] == "restricted"
    assert labels["pod-security.kubernetes.io/warn"] == "restricted"
    assert labels["pod-security.kubernetes.io/enforce-version"] == "latest"

    assert quota["metadata"]["namespace"] == "taroai"
    assert quota["spec"]["hard"] == {
        "pods": "50",
        "requests.cpu": "20",
        "requests.memory": "40Gi",
        "limits.cpu": "40",
        "limits.memory": "80Gi",
        "requests.ephemeral-storage": "100Gi",
        "limits.ephemeral-storage": "200Gi",
    }

    container_limits = limit_range["spec"]["limits"][0]
    assert limit_range["metadata"]["namespace"] == "taroai"
    assert container_limits["type"] == "Container"
    assert container_limits["default"] == {
        "cpu": "1000m",
        "memory": "1Gi",
        "ephemeral-storage": "2Gi",
    }
    assert container_limits["defaultRequest"] == {
        "cpu": "500m",
        "memory": "512Mi",
        "ephemeral-storage": "1Gi",
    }
    assert container_limits["max"]["memory"] == "4Gi"
    assert container_limits["max"]["ephemeral-storage"] == "8Gi"

    assert default_deny["apiVersion"] == "networking.k8s.io/v1"
    assert default_deny["metadata"]["namespace"] == "taroai"
    assert default_deny["spec"]["podSelector"]["matchLabels"] == {
        "app.kubernetes.io/name": "taroai-sandbox-session"
    }
    assert default_deny["spec"]["policyTypes"] == ["Ingress", "Egress"]
    assert "ingress" not in default_deny["spec"]
    assert "egress" not in default_deny["spec"]


def test_kubernetes_api_manifest_exposes_control_plane_with_migration_job():
    resources = documents_by_kind_and_name("infra/k8s/api.yaml")

    deployment = resources[("Deployment", "taroai-api")]
    service = resources[("Service", "taroai-api")]
    migration_job = resources[("Job", "taroai-db-migrate")]

    assert deployment["apiVersion"] == "apps/v1"
    assert deployment["spec"]["replicas"] == 2
    assert deployment["spec"]["selector"]["matchLabels"]["app.kubernetes.io/name"] == "taroai-api"

    pod_spec = deployment["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]
    env = container_env(container)

    assert pod_spec["securityContext"]["runAsNonRoot"] is True
    assert container["image"] == "ghcr.io/creao-ai/taroai-api:latest"
    assert container["ports"] == [{"name": "http", "containerPort": 8000}]
    assert container["envFrom"] == [
        {"configMapRef": {"name": RUNTIME_CONFIG}},
        {"secretRef": {"name": RUNTIME_SECRETS}},
    ]
    assert env["TAROAI_RUN_MIGRATIONS"]["value"] == "false"
    assert container["readinessProbe"]["httpGet"] == {"path": "/readyz", "port": "http"}
    assert container["livenessProbe"]["httpGet"] == {"path": "/healthz", "port": "http"}
    assert {mount["mountPath"] for mount in container["volumeMounts"]} == {
        "/tmp",
        "/data/taroai",
    }
    assert {volume["name"] for volume in pod_spec["volumes"]} == {
        "tmp",
        "taroai-data",
    }
    assert container["resources"]["requests"]["cpu"]
    assert container["resources"]["limits"]["memory"]
    assert container["securityContext"]["readOnlyRootFilesystem"] is True

    assert service["spec"]["type"] == "ClusterIP"
    assert service["spec"]["selector"]["app.kubernetes.io/name"] == "taroai-api"
    assert service["spec"]["ports"] == [
        {"name": "http", "port": 8000, "targetPort": "http"}
    ]

    job_spec = migration_job["spec"]
    migration_pod_spec = job_spec["template"]["spec"]
    migration_container = migration_pod_spec["containers"][0]
    migration_env = container_env(migration_container)

    assert migration_job["apiVersion"] == "batch/v1"
    assert job_spec["backoffLimit"] == 3
    assert migration_pod_spec["restartPolicy"] == "Never"
    assert migration_container["image"] == container["image"]
    assert migration_container["envFrom"] == container["envFrom"]
    assert migration_env["TAROAI_RUN_MIGRATIONS"]["value"] == "false"
    assert migration_container["command"] == ["/app/entrypoint.sh"]
    assert migration_container["args"] == [
        "python",
        "-m",
        "taroai.db.migration_cli",
        "--database-url",
        "$(TAROAI_DATABASE_URL)",
        "--migrations-path",
        "/app/migrations",
        "--apply",
    ]


def test_kubernetes_postgres_manifest_defines_stateful_database_with_init_contract():
    resources = documents_by_kind_and_name("infra/k8s/postgres.yaml")

    init_config = resources[("ConfigMap", "taroai-postgres-init")]
    service = resources[("Service", "postgres")]
    statefulset = resources[("StatefulSet", "postgres")]

    assert "001_taroai_app.sh" in init_config["data"]
    assert "TAROAI_APP_DATABASE_PASSWORD" in init_config["data"]["001_taroai_app.sh"]
    assert service["spec"]["ports"] == [
        {"name": "postgres", "port": 5432, "targetPort": "postgres"}
    ]
    assert statefulset["apiVersion"] == "apps/v1"
    assert statefulset["spec"]["serviceName"] == "postgres"
    assert statefulset["spec"]["replicas"] == 1

    pod_spec = statefulset["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]
    env = container_env(container)

    assert container["image"] == "postgres:16-alpine"
    assert container["ports"] == [{"name": "postgres", "containerPort": 5432}]
    assert env["POSTGRES_DB"]["value"] == "taroai"
    assert env["POSTGRES_USER"]["value"] == "taroai_admin"
    assert env["POSTGRES_PASSWORD"]["valueFrom"]["secretKeyRef"] == {
        "name": RUNTIME_SECRETS,
        "key": "POSTGRES_PASSWORD",
    }
    assert env["TAROAI_APP_DATABASE_PASSWORD"]["valueFrom"]["secretKeyRef"] == {
        "name": RUNTIME_SECRETS,
        "key": "TAROAI_APP_DATABASE_PASSWORD",
    }
    assert container["readinessProbe"]["exec"]["command"] == [
        "pg_isready",
        "-U",
        "taroai_admin",
        "-d",
        "taroai",
    ]
    assert "data" in [claim["metadata"]["name"] for claim in statefulset["spec"]["volumeClaimTemplates"]]


def test_kubernetes_redis_manifest_defines_passworded_stateful_cache():
    resources = documents_by_kind_and_name("infra/k8s/redis.yaml")

    service = resources[("Service", "redis")]
    statefulset = resources[("StatefulSet", "redis")]

    assert service["spec"]["ports"] == [
        {"name": "redis", "port": 6379, "targetPort": "redis"}
    ]

    pod_spec = statefulset["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]
    env = container_env(container)

    assert statefulset["spec"]["serviceName"] == "redis"
    assert container["image"] == "redis:7-alpine"
    assert container["ports"] == [{"name": "redis", "containerPort": 6379}]
    assert "--appendonly" in container["args"]
    assert "--requirepass" in container["args"]
    assert env["REDIS_PASSWORD"]["valueFrom"]["secretKeyRef"] == {
        "name": RUNTIME_SECRETS,
        "key": "REDIS_PASSWORD",
    }
    assert container["readinessProbe"]["exec"]["command"] == [
        "sh",
        "-c",
        "redis-cli -a \"$REDIS_PASSWORD\" ping",
    ]
    assert "data" in [claim["metadata"]["name"] for claim in statefulset["spec"]["volumeClaimTemplates"]]


def test_kubernetes_minio_manifest_defines_object_storage_and_bucket_job():
    resources = documents_by_kind_and_name("infra/k8s/minio.yaml")

    service = resources[("Service", "minio")]
    statefulset = resources[("StatefulSet", "minio")]
    bucket_job = resources[("Job", "taroai-minio-init")]

    assert service["spec"]["ports"] == [
        {"name": "api", "port": 9000, "targetPort": "api"},
        {"name": "console", "port": 9001, "targetPort": "console"},
    ]

    pod_spec = statefulset["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]
    env = container_env(container)

    assert statefulset["spec"]["serviceName"] == "minio"
    assert container["image"] == "minio/minio:RELEASE.2025-06-13T11-33-47Z"
    assert container["args"] == ["server", "/data", "--console-address", ":9001"]
    assert container["ports"] == [
        {"name": "api", "containerPort": 9000},
        {"name": "console", "containerPort": 9001},
    ]
    assert env["MINIO_ROOT_USER"]["valueFrom"]["secretKeyRef"] == {
        "name": RUNTIME_SECRETS,
        "key": "MINIO_ROOT_USER",
    }
    assert env["MINIO_ROOT_PASSWORD"]["valueFrom"]["secretKeyRef"] == {
        "name": RUNTIME_SECRETS,
        "key": "MINIO_ROOT_PASSWORD",
    }
    assert container["readinessProbe"]["httpGet"] == {"path": "/minio/health/ready", "port": "api"}
    assert "data" in [claim["metadata"]["name"] for claim in statefulset["spec"]["volumeClaimTemplates"]]

    job_container = bucket_job["spec"]["template"]["spec"]["containers"][0]
    assert bucket_job["apiVersion"] == "batch/v1"
    assert bucket_job["spec"]["template"]["spec"]["restartPolicy"] == "Never"
    assert job_container["image"] == "minio/mc:latest"
    assert "mc mb --ignore-existing" in job_container["command"][2]
    assert "mc anonymous set none" in job_container["command"][2]


def test_kubernetes_runtime_config_and_secret_cover_api_workers_and_backing_services():
    configmap = load_yaml_documents("infra/k8s/configmap.yaml")[0]
    secret = load_yaml_documents("infra/k8s/secrets.example.yaml")[0]

    config_data = configmap["data"]
    secret_data = secret["stringData"]

    assert configmap["metadata"]["name"] == RUNTIME_CONFIG
    assert secret["metadata"]["name"] == RUNTIME_SECRETS
    assert config_data["TAROAI_OBJECT_STORAGE_ENDPOINT"] == "http://minio:9000"
    assert config_data["TAROAI_JOB_QUEUE_BACKEND"] == "redis"
    assert config_data["TAROAI_RUN_EXECUTION_DISPATCH_MODE"] == "queue"
    assert config_data["TAROAI_CONNECTOR_REGISTRY_BACKEND"] == "sql"
    assert config_data["TAROAI_TRIGGER_OPERATIONS_STUCK_AFTER_SECONDS"] == "900"
    assert config_data["TAROAI_SANDBOX_DOCKER_USER"] == "65532:65532"
    assert config_data["TAROAI_SANDBOX_CONTROLLER_BASE_URL"] == (
        "http://sandbox-controller:8002"
    )
    assert config_data["TAROAI_SANDBOX_CONTROLLER_TIMEOUT_SECONDS"] == "30"
    assert config_data["TAROAI_SANDBOX_CONTROLLER_PROVIDER"] == "kubernetes"
    assert config_data["TAROAI_SANDBOX_CONTROLLER_SESSION_TTL_SECONDS"] == "1800"
    assert config_data["TAROAI_SANDBOX_CONTROLLER_MAX_SESSIONS_PER_TENANT"] == "20"
    assert config_data["TAROAI_SANDBOX_CONTROLLER_KUBERNETES_RUNTIME_CLASS_NAME"] == "gvisor"
    assert config_data["TAROAI_SANDBOX_CONTROLLER_KUBERNETES_RUNTIME_CLASS_REQUIRED"] == "true"
    assert config_data["TAROAI_SANDBOX_CONTROLLER_KUBERNETES_ALLOWED_IMAGES"] == (
        "[\"ghcr.io/customer/sandbox-runtime@sha256:*\"]"
    )
    assert config_data["TAROAI_SANDBOX_CONTROLLER_KUBERNETES_ORPHAN_CLEANUP_ENABLED"] == "false"
    assert config_data["TAROAI_BROWSER_PROVIDER"] == "playwright"
    assert config_data["TAROAI_BROWSER_CONTROLLER_BASE_URL"] == (
        "http://browser-controller:8001"
    )
    assert config_data["TAROAI_BROWSER_CONTROLLER_TIMEOUT_SECONDS"] == "30"
    assert config_data["TAROAI_BROWSER_CONTROLLER_SESSION_TTL_SECONDS"] == "1800"
    assert config_data["TAROAI_BROWSER_CONTROLLER_MAX_SESSIONS_PER_TENANT"] == "20"
    assert config_data["TAROAI_BROWSER_CONTROLLER_NAVIGATION_ALLOWED_HOSTS"] == "[]"
    assert not [
        key
        for key, value in config_data.items()
        if key.endswith("_BACKEND") and value == "memory"
    ]

    sensitive_config_keys = {
        "TAROAI_DATABASE_URL",
        "TAROAI_REDIS_URL",
        "TAROAI_OBJECT_STORAGE_SECRET_ACCESS_KEY",
        "TAROAI_MODEL_GATEWAY_API_KEY",
        "TAROAI_SANDBOX_CONTROLLER_API_KEY",
        "TAROAI_SANDBOX_SECRET_RESOLVER_TOKEN",
        "TAROAI_BROWSER_CONTROLLER_API_KEY",
        "TAROAI_TRIGGER_WEBHOOK_SIGNING_SECRETS",
        "POSTGRES_PASSWORD",
        "MINIO_ROOT_PASSWORD",
        "REDIS_PASSWORD",
    }
    assert sensitive_config_keys.isdisjoint(config_data)

    required_secret_keys = {
        "TAROAI_DATABASE_URL",
        "TAROAI_APP_DATABASE_PASSWORD",
        "TAROAI_REDIS_URL",
        "REDIS_PASSWORD",
        "TAROAI_OBJECT_STORAGE_ACCESS_KEY_ID",
        "TAROAI_OBJECT_STORAGE_SECRET_ACCESS_KEY",
        "MINIO_ROOT_USER",
        "MINIO_ROOT_PASSWORD",
        "TAROAI_MODEL_GATEWAY_API_KEY",
        "TAROAI_SANDBOX_CONTROLLER_API_KEY",
        "TAROAI_SANDBOX_SECRET_RESOLVER_TOKEN",
        "TAROAI_BROWSER_CONTROLLER_API_KEY",
        "TAROAI_ACCESS_TOKEN_SECRET",
        "TAROAI_PASSWORD_HASH_SALT",
        "TAROAI_TENANT_BOOTSTRAP_TOKEN",
        "TAROAI_TRIGGER_WEBHOOK_SIGNING_SECRETS",
        "POSTGRES_PASSWORD",
    }
    assert required_secret_keys.issubset(secret_data)


def test_kubernetes_browser_controller_manifest_runs_isolated_browser_service():
    resources = documents_by_kind_and_name("infra/k8s/browser-controller.yaml")

    deployment = resources[("Deployment", "browser-controller")]
    service = resources[("Service", "browser-controller")]

    assert deployment["apiVersion"] == "apps/v1"
    assert deployment["spec"]["replicas"] == 1
    assert deployment["spec"]["selector"]["matchLabels"]["app.kubernetes.io/name"] == (
        "browser-controller"
    )

    pod_spec = deployment["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]

    assert pod_spec["securityContext"]["runAsNonRoot"] is True
    assert container["image"] == "ghcr.io/creao-ai/taroai-browser-controller:latest"
    assert container["command"] == ["uvicorn"]
    assert container["args"] == [
        "taroai.sandbox.playwright_service:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8001",
    ]
    assert container["ports"] == [{"name": "http", "containerPort": 8001}]
    assert "envFrom" not in container
    env = container_env(container)
    assert env["TAROAI_BROWSER_CONTROLLER_API_KEY"]["valueFrom"][
        "secretKeyRef"
    ] == {
        "name": RUNTIME_SECRETS,
        "key": "TAROAI_BROWSER_CONTROLLER_API_KEY",
    }
    for env_name in [
        "TAROAI_BROWSER_CONTROLLER_SESSION_TTL_SECONDS",
        "TAROAI_BROWSER_CONTROLLER_MAX_SESSIONS",
        "TAROAI_BROWSER_CONTROLLER_MAX_SESSIONS_PER_TENANT",
        "TAROAI_BROWSER_CONTROLLER_MAX_SESSIONS_PER_RUN",
        "TAROAI_BROWSER_CONTROLLER_NAVIGATION_ALLOWED_HOSTS",
    ]:
        assert env[env_name]["valueFrom"]["configMapKeyRef"] == {
            "name": RUNTIME_CONFIG,
            "key": env_name,
        }
    for api_only_env in [
        "TAROAI_ACCESS_TOKEN_SECRET",
        "TAROAI_PASSWORD_HASH_SALT",
        "TAROAI_TENANT_BOOTSTRAP_TOKEN",
        "TAROAI_MODEL_GATEWAY_API_KEY",
        "TAROAI_OBJECT_STORAGE_SECRET_ACCESS_KEY",
    ]:
        assert api_only_env not in env
    assert container["readinessProbe"]["httpGet"] == {"path": "/healthz", "port": "http"}
    assert container["livenessProbe"]["httpGet"] == {"path": "/healthz", "port": "http"}
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]
    assert {mount["mountPath"] for mount in container["volumeMounts"]} == {
        "/tmp",
        "/home/taroai",
    }
    assert {volume["name"] for volume in pod_spec["volumes"]} == {
        "tmp",
        "browser-profile",
    }

    assert service["spec"]["type"] == "ClusterIP"
    assert service["spec"]["selector"]["app.kubernetes.io/name"] == "browser-controller"
    assert service["spec"]["ports"] == [
        {"name": "http", "port": 8001, "targetPort": "http"}
    ]


def test_kubernetes_sandbox_controller_manifest_runs_isolated_controller_service():
    resources = documents_by_kind_and_name("infra/k8s/sandbox-controller.yaml")

    deployment = resources[("Deployment", "sandbox-controller")]
    service = resources[("Service", "sandbox-controller")]

    assert deployment["apiVersion"] == "apps/v1"
    assert deployment["spec"]["replicas"] == 1
    assert deployment["spec"]["selector"]["matchLabels"]["app.kubernetes.io/name"] == (
        "sandbox-controller"
    )

    pod_spec = deployment["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]
    env = container_env(container)

    assert pod_spec["securityContext"]["runAsNonRoot"] is True
    assert container["image"] == "ghcr.io/creao-ai/taroai-sandbox-controller:latest"
    assert container["command"] == ["uvicorn"]
    assert container["args"] == [
        "taroai.sandbox.controller_service:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8002",
    ]
    assert container["ports"] == [{"name": "http", "containerPort": 8002}]
    assert "envFrom" not in container
    assert env["TAROAI_SANDBOX_CONTROLLER_API_KEY"]["valueFrom"][
        "secretKeyRef"
    ] == {
        "name": RUNTIME_SECRETS,
        "key": "TAROAI_SANDBOX_CONTROLLER_API_KEY",
    }
    for env_name in [
        "TAROAI_SANDBOX_CONTROLLER_SESSION_TTL_SECONDS",
        "TAROAI_SANDBOX_CONTROLLER_MAX_SESSIONS",
        "TAROAI_SANDBOX_CONTROLLER_MAX_SESSIONS_PER_TENANT",
        "TAROAI_SANDBOX_CONTROLLER_MAX_SESSIONS_PER_RUN",
        "TAROAI_SANDBOX_CONTROLLER_PROVIDER",
        "TAROAI_SANDBOX_CONTROLLER_DOCKER_MEMORY_LIMIT",
        "TAROAI_SANDBOX_CONTROLLER_DOCKER_CPUS",
        "TAROAI_SANDBOX_CONTROLLER_DOCKER_PIDS_LIMIT",
        "TAROAI_SANDBOX_CONTROLLER_DOCKER_USER",
        "TAROAI_SANDBOX_CONTROLLER_DOCKER_READ_ONLY_ROOTFS",
        "TAROAI_SANDBOX_CONTROLLER_DOCKER_DROP_ALL_CAPABILITIES",
        "TAROAI_SANDBOX_CONTROLLER_DOCKER_SECURITY_OPTS",
        "TAROAI_SANDBOX_CONTROLLER_DOCKER_TMPFS_MOUNTS",
        "TAROAI_SANDBOX_CONTROLLER_KUBERNETES_NAMESPACE",
        "TAROAI_SANDBOX_CONTROLLER_KUBERNETES_KUBECTL_BINARY",
        "TAROAI_SANDBOX_CONTROLLER_KUBERNETES_SERVICE_ACCOUNT_NAME",
        "TAROAI_SANDBOX_CONTROLLER_KUBERNETES_RUNTIME_CLASS_NAME",
        "TAROAI_SANDBOX_CONTROLLER_KUBERNETES_RUNTIME_CLASS_REQUIRED",
        "TAROAI_SANDBOX_CONTROLLER_KUBERNETES_ALLOWED_IMAGES",
        "TAROAI_SANDBOX_CONTROLLER_KUBERNETES_ORPHAN_CLEANUP_ENABLED",
        "TAROAI_SANDBOX_CONTROLLER_KUBERNETES_IMAGE_PULL_POLICY",
        "TAROAI_SANDBOX_CONTROLLER_KUBERNETES_POD_READY_TIMEOUT_SECONDS",
        "TAROAI_SANDBOX_CONTROLLER_KUBERNETES_MEMORY_LIMIT",
        "TAROAI_SANDBOX_CONTROLLER_KUBERNETES_CPU_LIMIT",
        "TAROAI_SANDBOX_CONTROLLER_KUBERNETES_EPHEMERAL_STORAGE_LIMIT",
        "TAROAI_SANDBOX_CONTROLLER_KUBERNETES_RUN_AS_USER",
        "TAROAI_SANDBOX_CONTROLLER_KUBERNETES_RUN_AS_GROUP",
    ]:
        assert env[env_name]["valueFrom"]["configMapKeyRef"] == {
            "name": RUNTIME_CONFIG,
            "key": env_name,
        }
    for api_only_env in [
        "TAROAI_ACCESS_TOKEN_SECRET",
        "TAROAI_PASSWORD_HASH_SALT",
        "TAROAI_TENANT_BOOTSTRAP_TOKEN",
        "TAROAI_MODEL_GATEWAY_API_KEY",
        "TAROAI_OBJECT_STORAGE_SECRET_ACCESS_KEY",
    ]:
        assert api_only_env not in env
    assert container["readinessProbe"]["httpGet"] == {"path": "/healthz", "port": "http"}
    assert container["livenessProbe"]["httpGet"] == {"path": "/healthz", "port": "http"}
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]
    assert {mount["mountPath"] for mount in container["volumeMounts"]} == {
        "/tmp",
        "/data/taroai/sandboxes",
    }

    assert service["spec"]["type"] == "ClusterIP"
    assert service["spec"]["selector"]["app.kubernetes.io/name"] == "sandbox-controller"
    assert service["spec"]["ports"] == [
        {"name": "http", "port": 8002, "targetPort": "http"}
    ]


def test_kubernetes_web_manifest_serves_static_workspace_without_runtime_secrets():
    resources = documents_by_kind_and_name("infra/k8s/web.yaml")

    deployment = resources[("Deployment", "taroai-web")]
    service = resources[("Service", "taroai-web")]

    assert deployment["apiVersion"] == "apps/v1"
    assert deployment["spec"]["replicas"] == 2
    assert deployment["spec"]["selector"]["matchLabels"]["app.kubernetes.io/name"] == (
        "taroai-web"
    )

    pod_spec = deployment["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]

    assert pod_spec["securityContext"]["runAsNonRoot"] is True
    assert container["image"] == "ghcr.io/creao-ai/taroai-web:latest"
    assert container["ports"] == [{"name": "http", "containerPort": 8080}]
    assert "envFrom" not in container
    assert "env" not in container
    assert container["readinessProbe"]["httpGet"] == {"path": "/", "port": "http"}
    assert container["livenessProbe"]["httpGet"] == {"path": "/", "port": "http"}
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]
    assert {mount["mountPath"] for mount in container["volumeMounts"]} == {
        "/var/cache/nginx",
        "/var/run",
    }

    assert service["spec"]["type"] == "ClusterIP"
    assert service["spec"]["selector"]["app.kubernetes.io/name"] == "taroai-web"
    assert service["spec"]["ports"] == [
        {"name": "http", "port": 80, "targetPort": "http"}
    ]


def test_kubernetes_network_policies_constrain_runtime_traffic():
    resources = documents_by_kind_and_name("infra/k8s/network-policy.yaml")

    expected_names = {
        "taroai-default-deny",
        "taroai-allow-dns-egress",
        "taroai-api-ingress",
        "taroai-api-egress",
        "taroai-worker-egress",
        "taroai-sandbox-controller-ingress",
        "taroai-sandbox-controller-egress",
        "taroai-browser-controller-ingress",
        "taroai-browser-controller-egress",
        "taroai-web-ingress",
        "taroai-migration-egress",
        "taroai-minio-init-egress",
        "taroai-postgres-ingress",
        "taroai-redis-ingress",
        "taroai-minio-ingress",
    }
    assert {name for kind, name in resources} == expected_names

    default_deny = resources[("NetworkPolicy", "taroai-default-deny")]
    assert default_deny["apiVersion"] == "networking.k8s.io/v1"
    assert default_deny["spec"] == {
        "podSelector": {},
        "policyTypes": ["Ingress", "Egress"],
    }

    dns = resources[("NetworkPolicy", "taroai-allow-dns-egress")]
    assert dns["spec"]["podSelector"] == {}
    assert dns["spec"]["policyTypes"] == ["Egress"]
    dns_ports = dns["spec"]["egress"][0]["ports"]
    assert {"protocol": "UDP", "port": 53} in dns_ports
    assert {"protocol": "TCP", "port": 53} in dns_ports

    api_ingress = resources[("NetworkPolicy", "taroai-api-ingress")]
    assert api_ingress["spec"]["podSelector"]["matchLabels"] == {
        "app.kubernetes.io/name": "taroai-api",
    }
    assert api_ingress["spec"]["ingress"][0]["ports"] == [{"protocol": "TCP", "port": 8000}]
    assert api_ingress["spec"]["ingress"][0]["from"] == [
        {"podSelector": {"matchLabels": {"app.kubernetes.io/part-of": "taroai"}}}
    ]

    assert_allows_backend_egress(resources[("NetworkPolicy", "taroai-api-egress")], "api")
    assert_allows_backend_egress(resources[("NetworkPolicy", "taroai-worker-egress")], "worker")
    assert_contains_service_egress(
        resources[("NetworkPolicy", "taroai-api-egress")],
        "api",
        "browser-controller",
        8001,
    )
    assert_contains_service_egress(
        resources[("NetworkPolicy", "taroai-api-egress")],
        "api",
        "sandbox-controller",
        8002,
    )
    assert_contains_service_egress(
        resources[("NetworkPolicy", "taroai-worker-egress")],
        "worker",
        "browser-controller",
        8001,
    )
    assert_contains_service_egress(
        resources[("NetworkPolicy", "taroai-worker-egress")],
        "worker",
        "sandbox-controller",
        8002,
    )
    assert_ingress_sources(
        resources[("NetworkPolicy", "taroai-sandbox-controller-ingress")],
        "sandbox-controller",
        8002,
        ["api", "worker"],
    )
    assert_ingress_sources(
        resources[("NetworkPolicy", "taroai-browser-controller-ingress")],
        "browser-controller",
        8001,
        ["api", "worker"],
    )
    assert_allows_browser_controller_egress(
        resources[("NetworkPolicy", "taroai-browser-controller-egress")]
    )
    assert_allows_sandbox_controller_egress(
        resources[("NetworkPolicy", "taroai-sandbox-controller-egress")]
    )
    assert_ingress_sources(
        resources[("NetworkPolicy", "taroai-web-ingress")],
        "taroai-web",
        8080,
        ["api"],
    )
    assert_service_egress(
        resources[("NetworkPolicy", "taroai-migration-egress")],
        "migration",
        "postgres",
        5432,
    )
    assert_service_egress(
        resources[("NetworkPolicy", "taroai-minio-init-egress")],
        "object-storage-init",
        "minio",
        9000,
    )
    assert_ingress_sources(
        resources[("NetworkPolicy", "taroai-postgres-ingress")],
        "postgres",
        5432,
        ["api", "worker", "migration"],
    )
    assert_ingress_sources(
        resources[("NetworkPolicy", "taroai-redis-ingress")],
        "redis",
        6379,
        ["api", "worker"],
    )
    assert_ingress_sources(
        resources[("NetworkPolicy", "taroai-minio-ingress")],
        "minio",
        9000,
        ["api", "worker", "object-storage-init"],
    )


def assert_allows_backend_egress(policy: dict, component: str) -> None:
    assert policy["spec"]["podSelector"]["matchLabels"] == {
        "app.kubernetes.io/component": component,
    }
    assert policy["spec"]["policyTypes"] == ["Egress"]
    destinations = [
        (
            rule["to"][0]["podSelector"]["matchLabels"]["app.kubernetes.io/name"],
            rule["ports"][0]["port"],
        )
        for rule in policy["spec"]["egress"]
        if "podSelector" in rule["to"][0]
    ]
    assert ("postgres", 5432) in destinations
    assert ("redis", 6379) in destinations
    assert ("minio", 9000) in destinations
    external_https_rules = [
        rule
        for rule in policy["spec"]["egress"]
        if "ipBlock" in rule["to"][0] and rule["ports"][0]["port"] == 443
    ]
    assert external_https_rules


def assert_allows_browser_controller_egress(policy: dict) -> None:
    assert policy["spec"]["podSelector"]["matchLabels"] == {
        "app.kubernetes.io/component": "browser-controller",
    }
    assert policy["spec"]["policyTypes"] == ["Egress"]
    external_https_rules = [
        rule
        for rule in policy["spec"]["egress"]
        if "ipBlock" in rule["to"][0] and rule["ports"][0]["port"] == 443
    ]
    assert external_https_rules


def assert_allows_sandbox_controller_egress(policy: dict) -> None:
    assert policy["spec"]["podSelector"]["matchLabels"] == {
        "app.kubernetes.io/component": "sandbox-controller",
    }
    assert policy["spec"]["policyTypes"] == ["Egress"]
    external_https_rules = [
        rule
        for rule in policy["spec"]["egress"]
        if "ipBlock" in rule["to"][0] and rule["ports"][0]["port"] == 443
    ]
    assert external_https_rules


def assert_service_egress(
    policy: dict,
    component: str,
    destination_name: str,
    port: int,
) -> None:
    assert policy["spec"]["podSelector"]["matchLabels"] == {
        "app.kubernetes.io/component": component,
    }
    assert policy["spec"]["policyTypes"] == ["Egress"]
    assert policy["spec"]["egress"] == [
        {
            "to": [
                {
                    "podSelector": {
                        "matchLabels": {"app.kubernetes.io/name": destination_name}
                    }
                }
            ],
            "ports": [{"protocol": "TCP", "port": port}],
        }
    ]


def assert_contains_service_egress(
    policy: dict,
    component: str,
    destination_name: str,
    port: int,
) -> None:
    assert policy["spec"]["podSelector"]["matchLabels"] == {
        "app.kubernetes.io/component": component,
    }
    assert policy["spec"]["policyTypes"] == ["Egress"]
    assert {
        "to": [
            {
                "podSelector": {
                    "matchLabels": {"app.kubernetes.io/name": destination_name}
                }
            }
        ],
        "ports": [{"protocol": "TCP", "port": port}],
    } in policy["spec"]["egress"]


def assert_ingress_sources(
    policy: dict,
    app_name: str,
    port: int,
    components: list[str],
) -> None:
    assert policy["spec"]["podSelector"]["matchLabels"] == {
        "app.kubernetes.io/name": app_name,
    }
    assert policy["spec"]["policyTypes"] == ["Ingress"]
    ingress_rule = policy["spec"]["ingress"][0]
    assert ingress_rule["ports"] == [{"protocol": "TCP", "port": port}]
    source_components = [
        peer["podSelector"]["matchLabels"]["app.kubernetes.io/component"]
        for peer in ingress_rule["from"]
    ]
    assert source_components == components
