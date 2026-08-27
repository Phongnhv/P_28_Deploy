"""API request/response Pydantic DTO schemas for DataPulse REST endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from src.models.database import (
    AnomalyFeedbackEnum,
    TriggerTypeEnum,
)


class ExecutionRequest(BaseModel):
    execution_run_id: str = Field(..., description="Unique execution run ID")
    dataset_id: str = Field(..., description="Target dataset ID")
    dataset_version_id: str = Field(default="v1", description="Dataset version ID")
    ruleset_version_id: str | None = Field(None, description="Approved ruleset version ID")
    requested_by: str = Field(default="system", description="User or system triggering run")
    trigger_type: TriggerTypeEnum = Field(default=TriggerTypeEnum.MANUAL, description="Trigger source")


class PublishRulesetRequest(BaseModel):
    proposal_run_id: str = Field(..., description="Proposal run ID containing approved rules")
    dataset_id: str = Field(..., description="Dataset ID")
    created_by: str = Field(default="steward", description="Data Steward username")


class PublishRulesetResponse(BaseModel):
    ruleset_version_id: str
    ruleset_hash: str
    dataset_id: str
    status: str
    rule_count: int


class AnomalySignalDTO(BaseModel):
    signal_id: str
    family: str
    target_type: str
    target_id: str
    score: float
    reliability: float
    observed_value: str | None = None
    baseline: dict[str, Any] | None = None
    explanation_code: str
    evidence_refs: list[str] = Field(default_factory=list)


class AnomalyFeedbackRequest(BaseModel):
    feedback_label: AnomalyFeedbackEnum
    comment: str | None = None
    username: str = Field(default="steward", description="Steward submitting feedback")


class CombinedRunStatusResponse(BaseModel):
    execution_run_id: str
    dataset_id: str
    execution_status: str
    anomaly_status: str
    hypothesis_status: str
    execution_details: dict[str, Any] | None = None
    anomaly_decision: str | None = None
    anomaly_score: float | None = None
    signals_count: int = 0
    hypotheses_count: int = 0
