"""API routes — Chat + DQ HITL endpoints."""

from __future__ import annotations

import asyncio
import uuid
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException

from src.agents.graph import agent
from src.models.schemas import (
    ApprovedRulesResponse,
    BulkReviewRequest,
    BulkReviewResponse,
    ChatRequest,
    ChatResponse,
    ProposeRequest,
    ProposeResponse,
    ReviewSummaryResponse,
    RuleReviewResponse,
    RuleUpdateRequest,
    RunStatusResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Existing chat endpoint
# ---------------------------------------------------------------------------

@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """Chat với AI agent."""
    try:
        result = await agent.ainvoke({"query": request.message})
        return ChatResponse(
            response=result.get("response", ""),
            analysis=result.get("analysis", ""),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def agent_status():
    """Kiểm tra trạng thái agent."""
    return {"status": "ready", "agent": "LangGraph Agent v1.0"}


# ---------------------------------------------------------------------------
# DQ endpoints
# ---------------------------------------------------------------------------

dq_router = APIRouter(prefix="/dq", tags=["DQ"])


async def _run_proposal_pipeline(
    run_id: str,
    dataset_id: str,
    connection_string: str | None,
    sampling_rate: float,
) -> None:
    """Background task: chạy Run 1 và cập nhật status vào DB."""
    from src.agents.graph import build_proposal_graph
    from src.services.rule_store import update_run_status

    try:
        update_run_status(run_id, "RUNNING")

        proposal_graph = build_proposal_graph()
        state = {
            "dataset_id": dataset_id,
            "rule_run_id": run_id,
            "metadata": {
                "connection_string": connection_string,
                "sampling_rate": sampling_rate,
            },
        }
        await proposal_graph.ainvoke(state)
        update_run_status(run_id, "DONE")
        logger.info("Run 1 hoàn thành: run_id=%s", run_id)

    except Exception as exc:
        logger.error("Run 1 thất bại run_id=%s: %s", run_id, exc, exc_info=True)
        update_run_status(run_id, "FAILED", error=str(exc))


@dq_router.post("/propose", response_model=ProposeResponse)
async def propose(
    request: ProposeRequest,
    background_tasks: BackgroundTasks,
) -> ProposeResponse:
    """Khởi động Run 1: profiler → digest → rule_proposer → hitl_gate.

    Trả về run_id ngay lập tức. Client poll GET /dq/runs/{run_id} để kiểm tra
    trạng thái hoàn thành. Sau khi DONE, rules sẵn sàng để Steward review.
    """
    from src.services.rule_store import create_run

    run_id = uuid.uuid4().hex
    create_run(run_id, request.dataset_id)

    background_tasks.add_task(
        _run_proposal_pipeline,
        run_id=run_id,
        dataset_id=request.dataset_id,
        connection_string=request.connection_string,
        sampling_rate=request.sampling_rate,
    )
    return ProposeResponse(run_id=run_id, status="QUEUED")


@dq_router.get("/runs/{run_id}", response_model=RunStatusResponse)
async def get_run_status(run_id: str) -> RunStatusResponse:
    """Poll trạng thái của Run 1."""
    from src.services.rule_store import get_run

    run = await asyncio.to_thread(get_run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"run_id={run_id!r} không tồn tại")
    return RunStatusResponse(**run)


# ---------------------------------------------------------------------------
# HITL Rule Review — nested dưới /runs/{run_id}
# Vì sao nested: rule_id chỉ unique trong 1 run, khớp PK ghép (run_id, rule_id)
# ---------------------------------------------------------------------------

@dq_router.get(
    "/runs/{run_id}/rules",
    response_model=list[RuleReviewResponse],
)
async def list_rules(
    run_id: str,
    status: str | None = None,
    table_name: str | None = None,
    dimension: str | None = None,
) -> list[RuleReviewResponse]:
    """Lấy danh sách rule của 1 run — feeds Screen 5 (Rule Review Table).

    Query params: status (PENDING/APPROVED/REJECTED), table_name, dimension.
    Trả [] nếu run đang RUNNING (chưa có rules) — không phải 404.
    """
    from src.services.rule_store import get_run, list_rules as store_list_rules

    run = await asyncio.to_thread(get_run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"run_id={run_id!r} không tồn tại")

    rows = await asyncio.to_thread(store_list_rules, run_id, status, table_name, dimension)
    return [RuleReviewResponse(**r) for r in rows]


@dq_router.post(
    "/runs/{run_id}/rules/bulk-review",
    response_model=BulkReviewResponse,
)
async def bulk_review(run_id: str, body: BulkReviewRequest) -> BulkReviewResponse:
    """Duyệt / từ chối nhiều rule cùng lúc (checkbox flow).

    Trả not_found cho các rule_id không tìm thấy trong run này.
    """
    from src.services.rule_store import bulk_review as store_bulk_review, get_run

    run = await asyncio.to_thread(get_run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"run_id={run_id!r} không tồn tại")

    decisions = [d.model_dump() for d in body.decisions]
    updated, not_found_ids = await asyncio.to_thread(store_bulk_review, run_id, decisions)
    return BulkReviewResponse(
        updated_count=len(updated),
        rules=[RuleReviewResponse(**r) for r in updated],
        not_found=not_found_ids,
    )


@dq_router.patch(
    "/runs/{run_id}/rules/{rule_id:path}",
    response_model=RuleReviewResponse,
)
async def update_rule(
    run_id: str,
    rule_id: str,
    body: RuleUpdateRequest,
) -> RuleReviewResponse:
    """Approve / reject / edit một rule (Steward action).

    Trường 'parameters' AI-proposed luôn được giữ nguyên (audit trail).
    Steward override được lưu vào 'edited_parameters'.
    review_note bắt buộc khi status=REJECTED.
    """
    from src.services.rule_store import get_run, review_rule

    run = await asyncio.to_thread(get_run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"run_id={run_id!r} không tồn tại")

    try:
        updated = await asyncio.to_thread(
            review_rule,
            run_id=run_id,
            rule_id=rule_id,
            status=body.status,
            edited_parameters=body.edited_parameters,
            severity=body.severity,
            reviewer=body.reviewer,
            review_note=body.review_note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if not updated:
        raise HTTPException(
            status_code=404,
            detail=f"rule_id={rule_id!r} không tồn tại trong run_id={run_id!r}",
        )
    return RuleReviewResponse(**updated)


@dq_router.get(
    "/runs/{run_id}/review-summary",
    response_model=ReviewSummaryResponse,
)
async def get_review_summary(run_id: str) -> ReviewSummaryResponse:
    """Tóm tắt tiến độ review — badge UI (tổng, PENDING, APPROVED, REJECTED, by_dimension)."""
    from src.services.rule_store import get_review_summary as store_summary, get_run

    run = await asyncio.to_thread(get_run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"run_id={run_id!r} không tồn tại")

    summary = await asyncio.to_thread(store_summary, run_id)
    return ReviewSummaryResponse(**summary)


@dq_router.get(
    "/runs/{run_id}/approved-rules",
    response_model=ApprovedRulesResponse,
)
async def get_approved_rules(run_id: str) -> ApprovedRulesResponse:
    """Lấy tất cả rule APPROVED — input contract cho Test Generator (Run 2)."""
    from src.services.rule_store import get_approved_rules as store_get_approved, get_run

    run = await asyncio.to_thread(get_run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"run_id={run_id!r} không tồn tại")

    rules = await asyncio.to_thread(store_get_approved, run_id)
    return ApprovedRulesResponse(
        run_id=run_id,
        count=len(rules),
        rules=[RuleReviewResponse(**r) for r in rules],
    )


@dq_router.post("/runs/{run_id}/generate-tests", status_code=501)
async def generate_tests(run_id: str):
    """Stub — Run 2 (Test Generator). Trả 501 cho đến khi milestone tiếp theo."""
    raise HTTPException(
        status_code=501,
        detail="generate-tests chưa được implement — milestone Test Generator.",
    )
