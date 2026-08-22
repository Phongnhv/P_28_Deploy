"""API Request/Response Pydantic DTO schemas for RidePulse DQ REST Endpoints."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from src.models.database import (
    AnomalyFeedbackEnum,
    DqResultStatusEnum,
    ExecutionRunStatusEnum,
    TriggerTypeEnum,
)


class ExecutionRequest(BaseModel):
    execution_run_id: str = Field(..., description="Unique execution run ID")
    dataset_id: str = Field(..., description="Target dataset ID")
    dataset_version_id: str = Field(default="v1", description="Dataset version ID")
    ruleset_version_id: Optional[str] = Field(None, description="Approved ruleset version ID")
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
    observed_value: Optional[str] = None
    baseline: Optional[Dict[str, Any]] = None
    explanation_code: str
    evidence_refs: List[str] = Field(default_factory=list)


class AnomalyFeedbackRequest(BaseModel):
    feedback_label: AnomalyFeedbackEnum
    comment: Optional[str] = None
    username: str = Field(default="steward", description="Steward submitting feedback")


class CombinedRunStatusResponse(BaseModel):
    execution_run_id: str
    dataset_id: str
    execution_status: str
    anomaly_status: str
    hypothesis_status: str
    execution_details: Optional[Dict[str, Any]] = None
    anomaly_decision: Optional[str] = None
    anomaly_score: Optional[float] = None
    signals_count: int = 0
    hypotheses_count: int = 0
