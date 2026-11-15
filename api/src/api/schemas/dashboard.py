import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from api.services.dashboards.loader import MAX_BYTES
from api.services.dashboards.proposals import MAX_OPERATIONS, Operation
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


class DashboardPreviewRunIn(BaseModel):
    """One view of a preview: the parameter values to run it with."""

    model_config = ConfigDict(extra="forbid")

    params: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Values by parameter name; one left out takes its default. A `time_range` "
            "takes a preset name such as `last_30_days` or a window; a `dimension` takes "
            "one value, or null for every value."
        ),
    )
    widgets: list[str] | None = Field(
        default=None,
        max_length=MAX_WIDGETS,
        description="Run only these widgets, by id; every data widget when left out.",
    )
    use_cache: bool = Field(default=True, description="Answer from the result cache when it can.")
    compare: Literal["previous_period", "previous_year"] | None = Field(
        default=None,
        description=(
            "The comparison every `kpi` widget with a time window makes, in place of "
            "the one its file sets. Other widgets are unchanged."
        ),
    )


class DashboardRunIn(DashboardPreviewRunIn):
    """One view of a dashboard: the parameter values to run it with."""

    draft: bool = Field(default=False, description="Run the draft. Needs the writer role.")


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
    source_ref: str | None = Field(description="The commit the applied file came from.")
    source_hash: str | None = Field(
        description="sha256 of the file's bytes as last applied from git, as `sha256sum` prints it."
    )
    client: str | None = Field(
        description="The client of the last change, from its User-Agent. Over MCP, the agent's."
    )
    updated_by_name: str | None = Field(
        default=None, description="The name of the person who made the last change."
    )
    created_by: uuid.UUID | None
    published_by: uuid.UUID | None
    refs: list[RefOut] = Field(description="The models, metrics, dimensions and tables it reads.")
    warnings: list[DiagnosticOut]


class DashboardChangeOut(BaseModel):
    path: str = Field(
        description=(
            "What changes: a top-level key such as `title`, `parameters.<name>`, "
            "`widgets.<id>`, `layout` when widgets move within or between rows, or "
            "`source_text` when only the YAML's text changes, such as a comment."
        )
    )
    change: Literal["added", "removed", "changed"]


class DashboardApplyOut(BaseModel):
    """What applying a file from git does, or would do on a dry run."""

    dry_run: bool
    action: Literal["create", "update", "unchanged"]
    name: str = Field(description="The dashboard's name once the file is applied.")
    renamed_from: str | None = Field(description="Its old name, when this apply renames it.")
    adopted: bool = Field(description="A dashboard made in DuckHaven becomes managed in git.")
    published: bool = Field(description="The file is published, so readers see it.")
    current_hash: str | None = Field(
        description=(
            "`spec_hash` of the newest file before the apply: the draft, or the published "
            "file when there is no draft. Null when the dashboard is created."
        )
    )
    spec_hash: str = Field(description="`spec_hash` of the applied file.")
    changes: list[DashboardChangeOut] = Field(
        description="Against the published file when publishing, otherwise the newest file."
    )
    warnings: list[DiagnosticOut]
    dashboard: DashboardOut | None = Field(
        description="The dashboard once applied; null on a dry run."
    )


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


class DashboardPreviewOut(BaseModel):
    id: uuid.UUID
    spec: dict
    source_text: str | None = Field(description="The YAML as written, when it was sent as YAML.")
    spec_hash: str = Field(
        description="sha256 of `spec` as canonical JSON; it changes with the file."
    )
    created_at: datetime
    updated_at: datetime
    expires_at: datetime = Field(description="Two hours after the last change.")
    warnings: list[DiagnosticOut]


class DashboardProposalIn(BaseModel):
    """Changes to propose to a dashboard, as operations on its newest file."""

    dashboard: str = Field(min_length=1, max_length=64, description="The dashboard's name.")
    summary: str = Field(
        min_length=1, max_length=500, description="What the changes do, in a sentence."
    )
    operations: list[Operation] = Field(
        min_length=1,
        max_length=MAX_OPERATIONS,
        description="Applied in order. A row index counts rows after the operations before it.",
    )


class DashboardProposalOut(BaseModel):
    id: uuid.UUID
    dashboard: str = Field(description="The dashboard's name.")
    title: str = Field(description="The dashboard's title, as the proposal leaves it.")
    status: Literal["pending", "accepted", "discarded", "stale", "expired"] = Field(
        description=(
            "`stale` when the dashboard changed after the proposal was made, and "
            "`expired` a day after it was made; neither can be accepted."
        )
    )
    summary: str
    operations: list[dict]
    changes: list[DashboardChangeOut] = Field(description="What accepting it changes.")
    base_version: int = Field(description="The dashboard `version` it was made against.")
    base_hash: str = Field(description="The `spec_hash` of the file it was made against.")
    result_spec: dict = Field(description="The file accepting it saves as the draft.")
    result_source: str = Field(description="That file, as canonical YAML.")
    result_hash: str
    managed: bool = Field(
        description="Whether the dashboard is managed in git, so the proposal cannot be "
        "accepted here: copy its file into the repository instead."
    )
    warnings: list[DiagnosticOut]
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None
