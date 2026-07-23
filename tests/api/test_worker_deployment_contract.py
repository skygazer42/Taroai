from pathlib import Path

import yaml

from taroai.config import DURABLE_DEPLOYMENT_BACKENDS
from taroai.workers.runner import parse_worker_process_args


def load_yaml_documents(path: str) -> list[dict]:
    with Path(path).open() as file:
        return [document for document in yaml.safe_load_all(file) if document]


def test_worker_runner_cli_parses_agent_and_cleanup_worker_modes():
    agent_config = parse_worker_process_args(
        [
            "--worker-kind",
            "agent",
            "--loop-forever",
            "--poll-interval-seconds",
            "0.5",
            "--stop-after-empty-polls",
            "3",
            "--max-jobs",
            "7",
        ]
    )
    cleanup_config = parse_worker_process_args(["--worker-kind", "cleanup"])
    trigger_due_config = parse_worker_process_args(["--worker-kind", "trigger_due"])
    trigger_scheduler_config = parse_worker_process_args(["--worker-kind", "trigger_scheduler"])
    connector_sync_config = parse_worker_process_args(["--worker-kind", "connector_sync"])
    restore_drill_due_config = parse_worker_process_args(["--worker-kind", "restore_drill_due"])
    restore_drill_execution_config = parse_worker_process_args(
        ["--worker-kind", "restore_drill_execution"]
    )
    restore_drill_evidence_config = parse_worker_process_args(
        ["--worker-kind", "restore_drill_evidence"]
    )
    restore_drill_scheduler_config = parse_worker_process_args(
        ["--worker-kind", "restore_drill_scheduler"]
    )

    assert agent_config.worker_kind == "agent"
    assert agent_config.loop_forever is True
    assert agent_config.poll_interval_seconds == 0.5
    assert agent_config.stop_after_empty_polls == 3
    assert agent_config.max_jobs == 7
    assert cleanup_config.worker_kind == "cleanup"
    assert trigger_due_config.worker_kind == "trigger_due"
    assert trigger_scheduler_config.worker_kind == "trigger_scheduler"
    assert connector_sync_config.worker_kind == "connector_sync"
    assert restore_drill_due_config.worker_kind == "restore_drill_due"
    assert restore_drill_execution_config.worker_kind == "restore_drill_execution"
    assert restore_drill_evidence_config.worker_kind == "restore_drill_evidence"
    assert restore_drill_scheduler_config.worker_kind == "restore_drill_scheduler"


def test_compose_runs_the_agent_and_trigger_workers_continuously():
    assert "TAROAI_RUN_EXECUTION_DISPATCH_MODE=queue" in Path(
        ".env.example"
    ).read_text()
    deployments = {
        "infra/docker-compose.yml": {
            "agent-worker": "agent",
            "trigger-due-worker": "trigger_due",
            "trigger-scheduler": "trigger_scheduler",
        },
        "infra/release/compose/docker-compose.release.yml": {
            "worker": "agent",
            "trigger-due-worker": "trigger_due",
            "trigger-scheduler": "trigger_scheduler",
        },
    }

    for path, expected_workers in deployments.items():
        services = load_yaml_documents(path)[0]["services"]
        agent_service = "agent-worker" if path == "infra/docker-compose.yml" else "worker"
        assert services[agent_service]["deploy"]["replicas"] == 2
        for service_name, worker_kind in expected_workers.items():
            command = services[service_name]["command"]
            assert command[command.index("--worker-kind") + 1] == worker_kind
            assert "--loop-forever" in command
            assert services[service_name]["restart"] == "unless-stopped"
            assert services[service_name]["environment"]["TAROAI_RUN_MIGRATIONS"] == "false"
            if worker_kind != "agent":
                assert services[service_name]["deploy"]["replicas"] == 1


def test_local_compose_loads_runtime_overrides_last():
    services = load_yaml_documents("infra/docker-compose.yml")[0]["services"]

    for service_name in ("api", "agent-worker"):
        env_files = services[service_name]["env_file"]
        assert env_files[-1] == {"path": "../.env.runtime", "required": False}

def test_release_compose_uses_cloud_tools_without_local_controllers_by_default():
    compose = load_yaml_documents(
        "infra/release/compose/docker-compose.release.yml"
    )[0]
    environment = compose["x-runtime-environment"]
    services = compose["services"]

    assert environment["TAROAI_SANDBOX_PROVIDER"] == "${TAROAI_SANDBOX_PROVIDER:-e2b}"
    assert environment["TAROAI_BROWSER_PROVIDER"] == "${TAROAI_BROWSER_PROVIDER:-disabled}"
    assert environment["TAROAI_SECRET_SERVICE_BACKEND"] == (
        "${TAROAI_SECRET_SERVICE_BACKEND:-aws_secrets_manager}"
    )
    assert environment["TAROAI_DEV_REQUEST_HEADERS_ENABLED"] == "false"
    for setting_name, backend in DURABLE_DEPLOYMENT_BACKENDS.items():
        assert environment[f"TAROAI_{setting_name.upper()}"] == backend

    api_dependencies = services["api"]["depends_on"]
    assert "sandbox-controller" not in api_dependencies
    assert "browser-controller" not in api_dependencies
    assert services["sandbox-controller"]["profiles"] == ["self-hosted-sandbox"]
    assert services["browser-controller"]["profiles"] == ["browser"]


def test_kubernetes_worker_manifest_runs_workers_independently():
    deployments = load_yaml_documents("infra/k8s/worker.yaml")
    by_name = {deployment["metadata"]["name"]: deployment for deployment in deployments}

    assert set(by_name) == {
        "taroai-agent-worker",
        "taroai-cleanup-worker",
        "taroai-connector-sync-worker",
        "taroai-restore-drill-due-worker",
        "taroai-restore-drill-execution-worker",
        "taroai-restore-drill-evidence-worker",
        "taroai-restore-drill-scheduler",
        "taroai-trigger-due-worker",
        "taroai-trigger-scheduler",
    }

    agent = by_name["taroai-agent-worker"]
    cleanup = by_name["taroai-cleanup-worker"]
    connector_sync = by_name["taroai-connector-sync-worker"]
    restore_drill_due = by_name["taroai-restore-drill-due-worker"]
    restore_drill_execution = by_name["taroai-restore-drill-execution-worker"]
    restore_drill_evidence = by_name["taroai-restore-drill-evidence-worker"]
    restore_drill_scheduler = by_name["taroai-restore-drill-scheduler"]
    trigger_due = by_name["taroai-trigger-due-worker"]
    trigger_scheduler = by_name["taroai-trigger-scheduler"]

    assert agent["spec"]["replicas"] == 2
    assert cleanup["spec"]["replicas"] == 1
    assert connector_sync["spec"]["replicas"] == 1
    assert restore_drill_due["spec"]["replicas"] == 1
    assert restore_drill_execution["spec"]["replicas"] == 1
    assert restore_drill_evidence["spec"]["replicas"] == 1
    assert restore_drill_scheduler["spec"]["replicas"] == 1
    assert trigger_due["spec"]["replicas"] == 1
    assert trigger_scheduler["spec"]["replicas"] == 1

    for name, deployment, worker_kind in [
        ("taroai-agent-worker", agent, "agent"),
        ("taroai-cleanup-worker", cleanup, "cleanup"),
        ("taroai-connector-sync-worker", connector_sync, "connector_sync"),
        ("taroai-restore-drill-due-worker", restore_drill_due, "restore_drill_due"),
        (
            "taroai-restore-drill-execution-worker",
            restore_drill_execution,
            "restore_drill_execution",
        ),
        (
            "taroai-restore-drill-evidence-worker",
            restore_drill_evidence,
            "restore_drill_evidence",
        ),
        (
            "taroai-restore-drill-scheduler",
            restore_drill_scheduler,
            "restore_drill_scheduler",
        ),
        ("taroai-trigger-due-worker", trigger_due, "trigger_due"),
        ("taroai-trigger-scheduler", trigger_scheduler, "trigger_scheduler"),
    ]:
        pod_spec = deployment["spec"]["template"]["spec"]
        container = pod_spec["containers"][0]
        args = container["args"]

        assert deployment["kind"] == "Deployment"
        assert deployment["apiVersion"] == "apps/v1"
        assert deployment["spec"]["selector"]["matchLabels"]["app.kubernetes.io/name"] == name
        assert pod_spec["securityContext"]["runAsNonRoot"] is True
        assert container["command"] == ["python", "-m", "taroai.workers.runner"]
        assert args[args.index("--worker-kind") + 1] == worker_kind
        assert "--loop-forever" in args
        assert container["envFrom"] == [
            {"configMapRef": {"name": "taroai-runtime-config"}},
            {"secretRef": {"name": "taroai-runtime-secrets"}},
        ]
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


def test_kubernetes_worker_config_separates_non_secret_and_secret_values():
    configmap = load_yaml_documents("infra/k8s/configmap.yaml")[0]
    secret = load_yaml_documents("infra/k8s/secrets.example.yaml")[0]

    config_data = configmap["data"]
    secret_data = secret["stringData"]

    assert configmap["metadata"]["name"] == "taroai-runtime-config"
    assert secret["metadata"]["name"] == "taroai-runtime-secrets"
    assert config_data["TAROAI_JOB_QUEUE_BACKEND"] == "redis"
    assert config_data["TAROAI_RUN_EXECUTION_DISPATCH_MODE"] == "queue"
    assert config_data["TAROAI_CONTROL_PLANE_STORE_BACKEND"] == "sql"
    assert config_data["TAROAI_CONNECTOR_REGISTRY_BACKEND"] == "sql"
    assert config_data["TAROAI_STORAGE_CATALOG_BACKEND"] == "sql"
    assert config_data["TAROAI_MODEL_GATEWAY_POLICY_STORE_BACKEND"] == "sql"
    assert config_data["TAROAI_TRIGGER_OPERATIONS_STUCK_AFTER_SECONDS"] == "900"
    assert not [
        key
        for key, value in config_data.items()
        if key.endswith("_BACKEND") and value == "memory"
    ]
    assert "TAROAI_DATABASE_URL" not in config_data
    assert "TAROAI_REDIS_URL" not in config_data
    assert "TAROAI_OBJECT_STORAGE_SECRET_ACCESS_KEY" not in config_data
    assert "TAROAI_SANDBOX_SECRET_RESOLVER_TOKEN" not in config_data
    assert "TAROAI_TRIGGER_WEBHOOK_SIGNING_SECRETS" not in config_data
    assert secret_data["TAROAI_DATABASE_URL"].startswith("postgresql://")
    assert secret_data["TAROAI_REDIS_URL"].startswith("redis://")
    assert secret_data["TAROAI_MODEL_GATEWAY_API_KEY"] == "replace-with-model-gateway-key"
    assert secret_data["TAROAI_SANDBOX_SECRET_RESOLVER_TOKEN"] == "replace-with-sandbox-resolver-token"
    assert secret_data["TAROAI_TRIGGER_WEBHOOK_SIGNING_SECRETS"]
