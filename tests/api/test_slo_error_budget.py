from datetime import timedelta

from taroai.domain import utc_now
from taroai.incidents.slo import (
    SloMetric,
    SloStatus,
    SloTier,
    SloWindow,
    build_error_budget,
    default_slo_targets,
)


def test_default_slo_targets_make_enterprise_availability_stricter_than_poc():
    poc_targets = {
        target.metric: target for target in default_slo_targets(SloTier.POC)
    }
    business_targets = {
        target.metric: target for target in default_slo_targets(SloTier.BUSINESS)
    }
    enterprise_targets = {
        target.metric: target for target in default_slo_targets(SloTier.ENTERPRISE)
    }

    assert set(enterprise_targets) == set(SloMetric)
    assert (
        enterprise_targets[SloMetric.API_AVAILABILITY].threshold
        > business_targets[SloMetric.API_AVAILABILITY].threshold
        > poc_targets[SloMetric.API_AVAILABILITY].threshold
    )
    assert (
        enterprise_targets[SloMetric.RUN_CREATION_LATENCY_MS].threshold
        < business_targets[SloMetric.RUN_CREATION_LATENCY_MS].threshold
        < poc_targets[SloMetric.RUN_CREATION_LATENCY_MS].threshold
    )


def test_error_budget_reports_healthy_when_measurements_have_headroom():
    target = {
        item.metric: item for item in default_slo_targets(SloTier.ENTERPRISE)
    }[SloMetric.API_AVAILABILITY]
    window = SloWindow(
        started_at=utc_now() - timedelta(hours=1),
        ended_at=utc_now(),
    )

    budget = build_error_budget(
        target=target,
        window=window,
        measurement_values=[0.9995, 0.9998, 0.9993],
    )

    assert budget.status == SloStatus.HEALTHY
    assert budget.measured_value > target.threshold
    assert budget.remaining_ratio > 0.25


def test_error_budget_reports_warning_when_budget_is_nearly_exhausted():
    target = {
        item.metric: item for item in default_slo_targets(SloTier.BUSINESS)
    }[SloMetric.RUN_CREATION_LATENCY_MS]
    window = SloWindow(
        started_at=utc_now() - timedelta(hours=1),
        ended_at=utc_now(),
    )

    budget = build_error_budget(
        target=target,
        window=window,
        measurement_values=[
            target.threshold * 0.92,
            target.threshold * 0.94,
            target.threshold * 0.96,
        ],
    )

    assert budget.status == SloStatus.WARNING
    assert 0 <= budget.remaining_ratio < 0.25


def test_error_budget_reports_breach_when_measurements_miss_target():
    target = {
        item.metric: item for item in default_slo_targets(SloTier.ENTERPRISE)
    }[SloMetric.SANDBOX_STARTUP_MS]
    window = SloWindow(
        started_at=utc_now() - timedelta(hours=1),
        ended_at=utc_now(),
    )

    budget = build_error_budget(
        target=target,
        window=window,
        measurement_values=[target.threshold * 1.1, target.threshold * 1.2],
    )

    assert budget.status == SloStatus.BREACHED
    assert budget.remaining_ratio == 0
