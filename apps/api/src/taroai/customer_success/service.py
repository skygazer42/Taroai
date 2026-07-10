from collections import Counter
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from taroai.domain import RunStatus
from taroai.customer_success.models import (
    AdoptionMetrics,
    SolutionPackOutcomeMetrics,
    SuccessHealthBand,
    TenantSuccessHealth,
    TenantSuccessSummary,
)


class InMemoryCustomerSuccessService(BaseModel):
    store: Any = Field(exclude=True, repr=False)
    solution_pack_registry: Any | None = Field(default=None, exclude=True, repr=False)
    skill_registry: Any | None = Field(default=None, exclude=True, repr=False)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def build_tenant_summary(self, tenant_id: str) -> TenantSuccessSummary:
        adoption = self.build_adoption_metrics(tenant_id)
        solution_pack_outcomes = self.build_solution_pack_outcomes(
            tenant_id,
            adoption,
        )
        return TenantSuccessSummary(
            tenant_id=tenant_id,
            adoption=adoption,
            solution_pack_outcomes=solution_pack_outcomes,
            health=self.build_success_health(
                tenant_id,
                adoption,
                solution_pack_outcomes,
            ),
        )

    def build_adoption_metrics(self, tenant_id: str) -> AdoptionMetrics:
        runs = self.store.list_runs(tenant_id)
        billing_meters = self.store.list_billing_meters(tenant_id)
        audit_events = self.store.list_audit_events(tenant_id)
        skill_ids = {
            meter.skill_id
            for meter in billing_meters
            if meter.meter_type == "skill_call_count" and meter.skill_id is not None
        }
        workflow_counts = Counter(
            self._workflow_key(run)
            for run in runs
            if self._workflow_key(run) is not None
        )
        return AdoptionMetrics(
            active_users=len({run.user_id for run in runs}),
            active_workspaces=len({run.workspace_id for run in runs}),
            runs_created=len(runs),
            runs_completed=sum(1 for run in runs if run.status == RunStatus.SUCCEEDED),
            artifact_downloads=sum(
                1 for event in audit_events if event.event_type == "storage.downloaded"
            ),
            skills_used=len(skill_ids),
            approvals_resolved=sum(
                1 for event in audit_events if event.event_type == "approval.resolved"
            ),
            feedback_submitted=sum(
                1
                for event in audit_events
                if event.event_type == "customer.feedback.submitted"
            ),
            repeated_workflows=sum(1 for count in workflow_counts.values() if count >= 2),
        )

    def build_solution_pack_outcomes(
        self,
        tenant_id: str,
        adoption: AdoptionMetrics,
    ) -> list[SolutionPackOutcomeMetrics]:
        if self.solution_pack_registry is None:
            return []
        outcomes: list[SolutionPackOutcomeMetrics] = []
        for installation in self.solution_pack_registry.list_installations(tenant_id):
            try:
                entry = self.solution_pack_registry.get_for_tenant(
                    tenant_id,
                    installation.pack_id,
                )
            except Exception:
                continue
            metric_values = {
                metric: self._solution_pack_metric_value(
                    metric,
                    adoption,
                    installation,
                )
                for metric in entry.manifest.success_metrics
            }
            outcomes.append(
                SolutionPackOutcomeMetrics(
                    pack_id=installation.pack_id,
                    version=installation.version,
                    workspace_count=len(installation.workspace_ids),
                    installed_skill_count=len(installation.installed_skill_ids),
                    metric_values=metric_values,
                )
            )
        return sorted(outcomes, key=lambda item: item.pack_id)

    def build_success_health(
        self,
        tenant_id: str,
        adoption: AdoptionMetrics,
        solution_pack_outcomes: list[SolutionPackOutcomeMetrics],
    ) -> TenantSuccessHealth:
        onboarding_score = self._score(
            adoption.active_users
            + adoption.active_workspaces
            + len(solution_pack_outcomes),
            target=5,
        )
        adoption_score = self._score(
            adoption.runs_created
            + adoption.skills_used
            + adoption.repeated_workflows,
            target=8,
        )
        reliability_score = self._completion_score(adoption)
        value_score = self._score(
            adoption.runs_completed
            + adoption.artifact_downloads
            + adoption.approvals_resolved,
            target=6,
        )
        risk_score = self._risk_score(adoption)
        average = (
            onboarding_score
            + adoption_score
            + reliability_score
            + value_score
            + (100 - risk_score)
        ) / 5
        if average >= 70 and risk_score < 50:
            band = SuccessHealthBand.HEALTHY
        elif average >= 45 and risk_score < 75:
            band = SuccessHealthBand.WATCH
        else:
            band = SuccessHealthBand.AT_RISK
        return TenantSuccessHealth(
            tenant_id=tenant_id,
            onboarding_score=onboarding_score,
            adoption_score=adoption_score,
            reliability_score=reliability_score,
            value_score=value_score,
            risk_score=risk_score,
            band=band,
        )

    def _workflow_key(self, run) -> str | None:
        if run.agent_id:
            return f"agent:{run.agent_id}"
        if run.mode:
            return f"mode:{run.mode.value}"
        return None

    def _solution_pack_metric_value(
        self,
        metric: str,
        adoption: AdoptionMetrics,
        installation,
    ) -> int:
        if metric == "active_workspaces":
            return adoption.active_workspaces
        if metric == "skills_installed":
            return len(installation.installed_skill_ids)
        if hasattr(adoption, metric):
            return int(getattr(adoption, metric))
        return 0

    def _score(self, value: int, target: int) -> int:
        if target <= 0:
            return 100
        return min(100, int(round((value / target) * 100)))

    def _completion_score(self, adoption: AdoptionMetrics) -> int:
        if adoption.runs_created == 0:
            return 0
        return min(
            100,
            int(round((adoption.runs_completed / adoption.runs_created) * 100)),
        )

    def _risk_score(self, adoption: AdoptionMetrics) -> int:
        if adoption.runs_created == 0:
            return 80
        incomplete_runs = max(0, adoption.runs_created - adoption.runs_completed)
        return min(
            100,
            int(round((incomplete_runs / adoption.runs_created) * 100))
            + min(20, adoption.feedback_submitted * 5),
        )
