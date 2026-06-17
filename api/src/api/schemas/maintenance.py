"""Response/request shapes for the maintenance advisor API."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class PolicyOut(BaseModel):
    scan_enabled: bool
    scan_frequency: str
    preset: str
    thresholds: dict
    max_tables_per_cycle: int
    last_scan_at: datetime | None = None
    last_deep_scan_at: datetime | None = None


class PolicyUpdate(BaseModel):
    scan_enabled: bool | None = None
    scan_frequency: str | None = None
    preset: str | None = None
    # Advanced override of individual threshold values; merged onto the preset.
    thresholds: dict | None = None
    max_tables_per_cycle: int | None = None


class ScanResult(BaseModel):
    status: str
    dispatched: int = 0
    candidates: int = 0
    stale: int = 0
    deep: bool = False


class HealthSummary(BaseModel):
    """A rolled-up score for any scope (namespace, workspace, deployment)."""

    score: int | None
    band: str
    table_count: int
    attention_count: int
    total_data_bytes: int


class TableHealthOut(BaseModel):
    schema_name: str
    table_name: str
    score: int | None
    band: str
    scanned_at: datetime | None = None
    snapshot_count: int | None = None
    data_file_count: int | None = None
    manifest_count: int | None = None
    total_data_bytes: int | None = None
    avg_file_bytes: int | None = None
    small_file_ratio: float | None = None
    orphan_bytes: int | None = None
    factors: dict | None = None


class NamespaceHealthOut(BaseModel):
    schema_name: str
    summary: HealthSummary


class WorkspaceHealthOut(BaseModel):
    workspace_id: uuid.UUID
    slug: str
    summary: HealthSummary


class DeploymentHealthOut(BaseModel):
    summary: HealthSummary
    workspaces: list[WorkspaceHealthOut]


class WorkspaceHealthDetailOut(BaseModel):
    summary: HealthSummary
    namespaces: list[NamespaceHealthOut]
    tables: list[TableHealthOut]


class HealthHistoryPoint(BaseModel):
    scanned_at: datetime
    score: int | None = None
    total_data_bytes: int | None = None


class RecommendationOut(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    schema_name: str
    table_name: str
    kind: str
    severity: str
    confidence: str
    rationale: str
    estimated_impact: dict | None = None
    remediation: dict | None = None
    status: str
    created_at: datetime
    resolved_at: datetime | None = None


class TableHealthDetailOut(BaseModel):
    table: TableHealthOut
    history: list[HealthHistoryPoint]
    recommendations: list[RecommendationOut]
