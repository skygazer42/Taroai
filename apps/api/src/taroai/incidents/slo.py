from datetime import datetime
from enum import Enum
from statistics import fmean

from pydantic import BaseModel, Field, model_validator


class SloMetric(str, Enum):
    API_AVAILABILITY = "api_availability"
    RUN_CREATION_LATENCY_MS = "run_creation_latency_ms"
    EVENT_STREAM_AVAILABILITY = "event_stream_availability"
    SANDBOX_STARTUP_MS = "sandbox_startup_ms"
    MODEL_GATEWAY_AVAILABILITY = "model_gateway_availability"
    CONNECTOR_SYNC_SUCCESS = "connector_sync_success"


class SloTier(str, Enum):
    POC = "poc"
    BUSINESS = "business"
    ENTERPRISE = "enterprise"


class SloDirection(str, Enum):
    HIGHER_IS_BETTER = "higher_is_better"
    LOWER_IS_BETTER = "lower_is_better"


class SloStatus(str, Enum):
    HEALTHY = "healthy"
    WARNING = "warning"
    BREACHED = "breached"


class SloTarget(BaseModel):
    metric: SloMetric
    tier: SloTier
    threshold: float = Field(gt=0)
    direction: SloDirection
    unit: str = Field(min_length=1)


class SloWindow(BaseModel):
    started_at: datetime
    ended_at: datetime

    @model_validator(mode="after")
    def validate_time_range(self) -> "SloWindow":
        if self.ended_at <= self.started_at:
            raise ValueError("SLO window ended_at must be after started_at")
        return self


class SloMeasurement(BaseModel):
    metric: SloMetric
    value: float = Field(ge=0)
    observed_at: datetime


class ErrorBudget(BaseModel):
    target: SloTarget
    window: SloWindow
    measured_value: float
    remaining_ratio: float = Field(ge=0, le=1)
    status: SloStatus


DEFAULT_TARGETS: dict[SloTier, dict[SloMetric, tuple[float, SloDirection, str]]] = {
    SloTier.POC: {
        SloMetric.API_AVAILABILITY: (0.99, SloDirection.HIGHER_IS_BETTER, "ratio"),
        SloMetric.RUN_CREATION_LATENCY_MS: (4000, SloDirection.LOWER_IS_BETTER, "ms"),
        SloMetric.EVENT_STREAM_AVAILABILITY: (0.99, SloDirection.HIGHER_IS_BETTER, "ratio"),
        SloMetric.SANDBOX_STARTUP_MS: (15000, SloDirection.LOWER_IS_BETTER, "ms"),
        SloMetric.MODEL_GATEWAY_AVAILABILITY: (0.99, SloDirection.HIGHER_IS_BETTER, "ratio"),
        SloMetric.CONNECTOR_SYNC_SUCCESS: (0.98, SloDirection.HIGHER_IS_BETTER, "ratio"),
    },
    SloTier.BUSINESS: {
        SloMetric.API_AVAILABILITY: (0.995, SloDirection.HIGHER_IS_BETTER, "ratio"),
        SloMetric.RUN_CREATION_LATENCY_MS: (3000, SloDirection.LOWER_IS_BETTER, "ms"),
        SloMetric.EVENT_STREAM_AVAILABILITY: (0.995, SloDirection.HIGHER_IS_BETTER, "ratio"),
        SloMetric.SANDBOX_STARTUP_MS: (12000, SloDirection.LOWER_IS_BETTER, "ms"),
        SloMetric.MODEL_GATEWAY_AVAILABILITY: (0.995, SloDirection.HIGHER_IS_BETTER, "ratio"),
        SloMetric.CONNECTOR_SYNC_SUCCESS: (0.99, SloDirection.HIGHER_IS_BETTER, "ratio"),
    },
    SloTier.ENTERPRISE: {
        SloMetric.API_AVAILABILITY: (0.999, SloDirection.HIGHER_IS_BETTER, "ratio"),
        SloMetric.RUN_CREATION_LATENCY_MS: (2000, SloDirection.LOWER_IS_BETTER, "ms"),
        SloMetric.EVENT_STREAM_AVAILABILITY: (0.999, SloDirection.HIGHER_IS_BETTER, "ratio"),
        SloMetric.SANDBOX_STARTUP_MS: (8000, SloDirection.LOWER_IS_BETTER, "ms"),
        SloMetric.MODEL_GATEWAY_AVAILABILITY: (0.999, SloDirection.HIGHER_IS_BETTER, "ratio"),
        SloMetric.CONNECTOR_SYNC_SUCCESS: (0.995, SloDirection.HIGHER_IS_BETTER, "ratio"),
    },
}


def default_slo_targets(tier: SloTier) -> list[SloTarget]:
    return [
        SloTarget(
            metric=metric,
            tier=tier,
            threshold=threshold,
            direction=direction,
            unit=unit,
        )
        for metric, (threshold, direction, unit) in DEFAULT_TARGETS[tier].items()
    ]


def build_error_budget(
    target: SloTarget,
    window: SloWindow,
    measurement_values: list[float],
) -> ErrorBudget:
    if not measurement_values:
        raise ValueError("SLO measurements are required")
    measured_value = fmean(measurement_values)
    remaining_ratio = calculate_remaining_ratio(target, measured_value)
    status = classify_slo_status(target, measured_value, remaining_ratio)
    return ErrorBudget(
        target=target,
        window=window,
        measured_value=measured_value,
        remaining_ratio=remaining_ratio,
        status=status,
    )


def calculate_remaining_ratio(target: SloTarget, measured_value: float) -> float:
    if target.direction == SloDirection.HIGHER_IS_BETTER:
        if measured_value < target.threshold:
            return 0
        allowed_error = max(1 - target.threshold, 0)
        if allowed_error == 0:
            return 1
        measured_error = max(1 - measured_value, 0)
        return clamp((allowed_error - measured_error) / allowed_error)
    if measured_value > target.threshold:
        return 0
    return clamp((target.threshold - measured_value) / target.threshold)


def classify_slo_status(
    target: SloTarget,
    measured_value: float,
    remaining_ratio: float,
) -> SloStatus:
    if target.direction == SloDirection.HIGHER_IS_BETTER:
        if measured_value < target.threshold:
            return SloStatus.BREACHED
    elif measured_value > target.threshold:
        return SloStatus.BREACHED
    if remaining_ratio < 0.25:
        return SloStatus.WARNING
    return SloStatus.HEALTHY


def clamp(value: float) -> float:
    return min(max(value, 0), 1)
