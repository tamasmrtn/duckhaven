import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from api.services.dashboards.loader import MAX_BYTES
from api.services.dashboards.spec import MAX_WIDGETS


class DashboardIn(BaseModel):
    """A dashboard file, as JSON in `spec` or as YAML in `source_text`. Exactly one.

    YAML is kept as written, comments included, and is what `GET .../source`
    returns. A `spec` is stored as JSON and exported as canonical YAML."""

    spec: dict | None = Field(default=None, description="The file, already parsed.")
    source_text: str | None = Field(
        default=None, max_length=MAX_BYTES, description="The file as YAML text."
    )

    @model_validator(mode="after")
    def _one_source(self):
        if (self.spec is None) == (self.source_text is None):
            raise ValueError("Send the file as exactly one of spec or source_text.")
        return self


class DashboardCreate(DashboardIn):
    pass


class DashboardUpdate(DashboardIn):
    """Replace the draft with this file."""

    base_version: int = Field(ge=1, description="The `version` this edit was made against.")


class DashboardPublish(BaseModel):
    base_version: int = Field(ge=1, description="The `version` of the draft being published.")
    message: str | None = Field(default=None, max_length=500, description="What changed.")


class DashboardRunIn(BaseModel):
    """One view of a dashboard: the parameter values to run it with."""

    model_config = ConfigDict(extra="forbid")

    params: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Values by parameter name; one left out takes its default. A `time_range` "
            "takes a preset name such as `last_30_days` or a window; a `dimension` takes "
            "one value, or null for every value."
        ),
    )
    draft: bool = Field(default=False, description="Run the draft. Needs the writer role.")
    widgets: list[str] | None = Field(
        default=None,
        max_length=MAX_WIDGETS,
        description="Run only these widgets, by id; every data widget when left out.",
    )
    use_cache: bool = Field(default=True, description="Answer from the result cache when it can.")


class DiagnosticOut(BaseModel):
    path: str = Field(description="Where in the file, such as `rows[0].widgets[1].query`.")
    line: int | None = Field(description="1-based; null when the file was sent as JSON.")
    col: int | None
    message: str
    hint: str | None = None


class RefOut(BaseModel):
    kind: Literal["model", "metric", "dimension", "table"]
    model: str | None = Field(description="The model a metric or dimension belongs to.")
    name: str


class DashboardSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    title: str
    description: str | None
    labels: list[str]
    status: Literal["draft", "published"] = Field(description="`draft` until the first publish.")
    has_draft: bool = Field(description="Whether there are unpublished changes.")
    source: Literal["ui", "git"]
    source_id: str | None
    managed: bool = Field(description="Managed in git: changed only by applying its file.")
    version: int
    created_at: datetime
    updated_at: datetime
    updated_by: uuid.UUID | None
    updated_via: Literal["ui", "rest", "cli", "assistant", "mcp"]
    published_at: datetime | None


class DashboardOut(DashboardSummary):
    draft: bool = Field(description="Whether `spec` is the draft rather than the published file.")
    spec: dict
    source_text: str | None = Field(description="The YAML as written, when it was sent as YAML.")
    spec_hash: str = Field(description="sha256 of `spec` as canonical JSON.")
    spec_version: int
    source_ref: str | None
    client: str | None = Field(description="The client of the last change, from its User-Agent.")
    created_by: uuid.UUID | None
    published_by: uuid.UUID | None
    refs: list[RefOut] = Field(description="The models, metrics, dimensions and tables it reads.")
    warnings: list[DiagnosticOut]


class DashboardRevisionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    number: int
    spec_hash: str
    message: str | None
    published_by: uuid.UUID | None
    published_at: datetime


class DashboardRevisionOut(DashboardRevisionSummary):
    spec: dict
    source_text: str | None


class DashboardValidationOut(BaseModel):
    valid: bool = Field(description="Whether it can be published as it stands.")
    errors: list[DiagnosticOut]
    warnings: list[DiagnosticOut]
    refs: list[RefOut]
