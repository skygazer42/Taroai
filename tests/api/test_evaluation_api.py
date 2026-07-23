from fastapi.testclient import TestClient

from taroai.agent import AgentRuntime
from taroai.agent.models import AgentDecision
from taroai.app import create_app
from taroai.model_gateway import ModelGateway
from taroai.store import InMemoryControlPlaneStore


class ExactResponseGateway(ModelGateway):
    def create_plan(self, request):
        raise NotImplementedError

    def decide_next_action(self, request):
        return AgentDecision(
            kind="respond",
            response_text="EVAL_OK",
            verification_required=False,
        )


def test_agent_version_must_pass_its_pinned_evaluation_before_publish():
    store = InMemoryControlPlaneStore()
    client = TestClient(
        create_app(
            store=store,
            runtime=AgentRuntime(store=store, model_gateway=ExactResponseGateway()),
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": "user_1"}
    suite = {
        "id": "core-answer",
        "version": "1.0.0",
        "target_kind": "agent",
        "cases": [
            {
                "id": "direct-answer",
                "version": "1",
                "input": {"request": "Return EVAL_OK"},
                "expected": {"scorer": "exact", "exact_value": "EVAL_OK"},
                "critical": True,
            }
        ],
        "gate": {
            "minimum_score": 1,
            "minimum_success_rate": 1,
            "maximum_tool_error_rate": 0,
            "maximum_human_intervention_rate": 0,
        },
    }

    registered = client.post("/api/evaluations/suites", headers=headers, json=suite)
    created = client.post(
        "/api/agents",
        headers=headers,
        json={
            "workspace_id": "workspace_sales",
            "name": "Evaluated agent",
            "version": {
                "instructions": "Return EVAL_OK",
                "runtime_snapshot": {
                    "evaluation_suite_id": suite["id"],
                    "evaluation_suite_version": suite["version"],
                },
            },
        },
    )
    agent_id = created.json()["agent"]["id"]
    publish_url = f"/api/agents/{agent_id}/versions/1/publish"

    blocked = client.post(publish_url, headers=headers)
    evaluation = client.post(
        f"/api/evaluations/agents/{agent_id}/versions/1/run",
        headers=headers,
        json={"suite_id": suite["id"], "suite_version": suite["version"]},
    )
    evaluation_body = evaluation.json()
    evidence = client.get(
        f"/api/evaluations/runs/{evaluation_body['id']}/evidence",
        headers=headers,
    )
    baseline = client.post(
        f"/api/evaluations/runs/{evaluation_body['id']}/baseline",
        headers=headers,
    )
    published = client.post(publish_url, headers=headers)

    assert registered.status_code == 201
    assert created.status_code == 201
    assert blocked.status_code == 409
    assert "must pass" in blocked.json()["message"]
    assert evaluation.status_code == 200
    assert evaluation_body["status"] == "passed"
    assert evaluation_body["promotion_gate"] == {"allowed": True, "reasons": []}
    assert evidence.status_code == 200
    assert evidence.json()["evidence_digest"] == evaluation_body["evidence_digest"]
    assert baseline.status_code == 200
    assert baseline.json()["run_id"] == evaluation_body["id"]
    assert published.status_code == 200
    assert published.json()["version"]["status"] == "published"
