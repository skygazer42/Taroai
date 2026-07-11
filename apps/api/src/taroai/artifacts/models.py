from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class KpiWidget(BaseModel):
    type: Literal["kpi"] = "kpi"
    title: str = Field(min_length=1, max_length=120)
    value: str | float | int
    delta: str | None = None
    tone: Literal["neutral", "positive", "warning", "critical"] = "neutral"


class ChartSeries(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    values: list[float]


class ChartWidget(BaseModel):
    type: Literal["chart"] = "chart"
    title: str = Field(min_length=1, max_length=120)
    chart_type: Literal["line", "bar", "area", "pie"]
    labels: list[str] = Field(default_factory=list)
    series: list[ChartSeries] = Field(min_length=1)

    @model_validator(mode="after")
    def series_match_labels(self):
        if self.labels and any(len(series.values) != len(self.labels) for series in self.series):
            raise ValueError("dashboard chart series must match label count")
        return self


class TableWidget(BaseModel):
    type: Literal["table"] = "table"
    title: str = Field(min_length=1, max_length=120)
    columns: list[str] = Field(min_length=1)
    rows: list[list[str | float | int | bool | None]] = Field(default_factory=list)

    @model_validator(mode="after")
    def rows_match_columns(self):
        if any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError("dashboard table rows must match the column count")
        return self


class AlertWidget(BaseModel):
    type: Literal["alert"] = "alert"
    title: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1, max_length=2000)
    severity: Literal["info", "success", "warning", "critical"] = "info"


class ProgressWidget(BaseModel):
    type: Literal["progress"] = "progress"
    title: str = Field(min_length=1, max_length=120)
    value: float = Field(ge=0, le=100)
    label: str | None = None


DashboardWidget = KpiWidget | ChartWidget | TableWidget | AlertWidget | ProgressWidget


class DashboardSpec(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=1000)
    widgets: list[DashboardWidget] = Field(default_factory=list, max_length=100)

    model_config = ConfigDict(extra="forbid")


class ArtifactRenderPolicy(BaseModel):
    allow_scripts: Literal[False] = False
    iframe_sandbox: str = "allow-forms"
    content_security_policy: str = (
        "default-src 'none'; img-src data: blob:; style-src 'unsafe-inline'; "
        "font-src data:; media-src data: blob:; form-action 'none'; base-uri 'none'"
    )
    max_preview_bytes: int = Field(default=512_000, ge=1024, le=2_000_000)

    model_config = ConfigDict(extra="forbid")


class RichArtifactCreate(BaseModel):
    run_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=255)
    artifact_type: Literal[
        "document", "image", "pdf", "html", "dashboard", "data", "code", "archive"
    ]
    storage_object_id: str | None = None
    thread_id: str | None = None
    message_id: str | None = None
    content_type: str | None = None
    preview_payload: dict[str, Any] = Field(default_factory=dict)
    dashboard: DashboardSpec | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def dashboard_matches_type(self):
        if self.artifact_type == "dashboard" and self.dashboard is None:
            raise ValueError("dashboard artifacts require a typed dashboard payload")
        if self.artifact_type != "dashboard" and self.dashboard is not None:
            raise ValueError("dashboard payload is only valid for dashboard artifacts")
        return self


class ArtifactPreview(BaseModel):
    artifact_id: str
    mode: Literal["text", "image", "pdf", "iframe", "dashboard", "download"]
    content_type: str
    text: str | None = None
    srcdoc: str | None = None
    dashboard: DashboardSpec | None = None
    download_url: str
    truncated: bool = False
    iframe_sandbox: str | None = None
    content_security_policy: str | None = None
