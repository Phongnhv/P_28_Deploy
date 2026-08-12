"""API routes — Chat + DQ HITL endpoints."""

from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException

from src.agents.graph import agent
from src.models.schemas import (
    ActiveRuleResponse,
    ActiveRulesListResponse,
    ApprovedRulesResponse,
    BulkReviewRequest,
    BulkReviewResponse,
    ChatRequest,
    ChatResponse,
    ExecuteActiveTestsRequest,
    ExecuteTestsResponse,
    ProposeRequest,
    ProposeResponse,
    PublishRulesResponse,
    ReviewSummaryResponse,
    RuleReviewResponse,
    RuleUpdateRequest,
    RunStatusResponse,
    TestResultResponse,
    TestResultsListResponse,
    TestRunStatusResponse,
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
    from src.services.rule_store import get_run
    from src.services.rule_store import list_rules as store_list_rules

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
    from src.services.rule_store import bulk_review as store_bulk_review
    from src.services.rule_store import get_run

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
    from src.services.rule_store import get_review_summary as store_summary
    from src.services.rule_store import get_run

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
    from src.services.rule_store import get_approved_rules as store_get_approved
    from src.services.rule_store import get_run

    run = await asyncio.to_thread(get_run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"run_id={run_id!r} không tồn tại")

    rules = await asyncio.to_thread(store_get_approved, run_id)
    return ApprovedRulesResponse(
        run_id=run_id,
        count=len(rules),
        rules=[RuleReviewResponse(**r) for r in rules],
    )


async def _run_execution_pipeline(
    test_run_id: str,
    proposal_run_id: str,
    dataset_id: str,
) -> None:
    """Background task: chạy Run 2 (Test Execution Graph) và cập nhật status vào DB."""
    from src.agents.graph import build_execution_graph
    from src.services.rule_store import get_approved_rules as store_get_approved
    from src.services.rule_store import update_test_run_status

    try:
        update_test_run_status(test_run_id, "RUNNING")
        approved_rules = store_get_approved(proposal_run_id)

        execution_graph = build_execution_graph()
        state = {
            "test_run_id": test_run_id,
            "rule_run_id": proposal_run_id,
            "dataset_id": dataset_id,
            "approved_rules": approved_rules,
        }
        await execution_graph.ainvoke(state)
        logger.info("Run 2 hoàn thành: test_run_id=%s", test_run_id)

    except Exception as exc:
        logger.error("Run 2 thất bại test_run_id=%s: %s", test_run_id, exc, exc_info=True)
        update_test_run_status(test_run_id, "FAILED", error=str(exc))


@dq_router.post(
    "/runs/{run_id}/generate-tests",
    response_model=ExecuteTestsResponse,
)
@dq_router.post(
    "/runs/{run_id}/execute-tests",
    response_model=ExecuteTestsResponse,
)
async def execute_tests(
    run_id: str,
    background_tasks: BackgroundTasks,
) -> ExecuteTestsResponse:
    """Kích hoạt Run 2: load approved rules → test_generator → validate → repair → run → anomaly.

    Trả về test_run_id ngay lập tức. Client poll GET /dq/test-runs/{test_run_id}
    để kiểm tra trạng thái và kết quả.
    """
    from src.services.rule_store import create_test_run, get_run

    proposal_run = await asyncio.to_thread(get_run, run_id)
    if not proposal_run:
        raise HTTPException(status_code=404, detail=f"proposal run_id={run_id!r} không tồn tại")

    dataset_id = proposal_run.get("dataset_id", "unknown")
    test_run_id = uuid.uuid4().hex
    create_test_run(test_run_id, dataset_id)

    background_tasks.add_task(
        _run_execution_pipeline,
        test_run_id=test_run_id,
        proposal_run_id=run_id,
        dataset_id=dataset_id,
    )
    return ExecuteTestsResponse(test_run_id=test_run_id, status="QUEUED")


@dq_router.get(
    "/test-runs/{test_run_id}",
    response_model=TestRunStatusResponse,
)
async def get_test_run_status(test_run_id: str) -> TestRunStatusResponse:
    """Poll trạng thái của một test run."""
    from src.services.rule_store import get_test_run as store_get_test_run

    run = await asyncio.to_thread(store_get_test_run, test_run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"test_run_id={test_run_id!r} không tồn tại")
    return TestRunStatusResponse(**run)


@dq_router.get(
    "/test-runs/{test_run_id}/results",
    response_model=TestResultsListResponse,
)
async def get_test_run_results(
    test_run_id: str,
    status: str | None = None,
) -> TestResultsListResponse:
    """Lấy danh sách kết quả kiểm thử của từng rule trong test run."""
    from src.services.rule_store import (
        get_test_results as store_get_results,
        get_test_run as store_get_test_run,
    )

    run = await asyncio.to_thread(store_get_test_run, test_run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"test_run_id={test_run_id!r} không tồn tại")

    rows = await asyncio.to_thread(store_get_results, test_run_id, status)
    return TestResultsListResponse(
        test_run_id=test_run_id,
        count=len(rows),
        results=[TestResultResponse(**r) for r in rows],
    )


# ---------------------------------------------------------------------------
# Publish & Active Rules Registry Endpoints
# ---------------------------------------------------------------------------

@dq_router.post(
    "/runs/{run_id}/publish",
    response_model=PublishRulesResponse,
)
async def publish_run_rules(run_id: str) -> PublishRulesResponse:
    """Xuất bản (Publish/Merge) các rules đã APPROVED từ proposal run vào Active Ruleset chính thức."""
    from src.services.rule_store import get_run, publish_approved_rules

    proposal_run = await asyncio.to_thread(get_run, run_id)
    if not proposal_run:
        raise HTTPException(status_code=404, detail=f"proposal run_id={run_id!r} không tồn tại")

    count = await asyncio.to_thread(publish_approved_rules, run_id)
    return PublishRulesResponse(
        run_id=run_id,
        published_count=count,
        message=f"Đã xuất bản thành công {count} rules vào Active Ruleset.",
    )


@dq_router.get(
    "/active-rules",
    response_model=ActiveRulesListResponse,
)
async def list_active_rules(
    dataset_id: str | None = None,
    table_name: str | None = None,
) -> ActiveRulesListResponse:
    """Lấy danh sách các rules đang hoạt động (Active Ruleset)."""
    from src.services.rule_store import get_active_rules as store_get_active_rules

    rules = await asyncio.to_thread(store_get_active_rules, dataset_id, table_name)
    return ActiveRulesListResponse(
        total_rules=len(rules),
        rules=[ActiveRuleResponse(**r) for r in rules],
    )


@dq_router.patch(
    "/active-rules/{rule_id}/deactivate",
)
async def deactivate_active_rule(rule_id: str) -> dict:
    """Vô hiệu hoá một active rule."""
    from src.services.rule_store import deactivate_rule as store_deactivate_rule

    success = await asyncio.to_thread(store_deactivate_rule, rule_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"rule_id={rule_id!r} không tồn tại hoặc đã bị vô hiệu hóa")
    return {"message": f"Rule {rule_id} đã được chuyển sang INACTIVE.", "status": "INACTIVE"}


@dq_router.post(
    "/execute-active-tests",
    response_model=ExecuteTestsResponse,
)
async def execute_active_tests(
    request: ExecuteActiveTestsRequest,
    background_tasks: BackgroundTasks,
) -> ExecuteTestsResponse:
    """Kích hoạt chạy test trên bộ Active Ruleset chính thức."""
    from src.services.rule_store import create_test_run, get_active_rules

    test_run_id = uuid.uuid4().hex
    dataset_id = request.dataset_id or "all"
    create_test_run(test_run_id, dataset_id)

    async def _run_active_execution(test_run_id: str, dataset_id: str, table_name: str | None) -> None:
        from src.agents.graph import build_execution_graph
        from src.services.rule_store import update_test_run_status

        try:
            update_test_run_status(test_run_id, "RUNNING")
            active_rules = get_active_rules(
                dataset_id=None if dataset_id == "all" else dataset_id,
                table_name=table_name,
            )

            execution_graph = build_execution_graph()
            state = {
                "test_run_id": test_run_id,
                "dataset_id": dataset_id,
                "approved_rules": active_rules,
            }
            await execution_graph.ainvoke(state)
            logger.info("Chạy test trên Active Ruleset hoàn thành: test_run_id=%s", test_run_id)
        except Exception as exc:
            logger.error("Chạy test trên Active Ruleset thất bại test_run_id=%s: %s", test_run_id, exc, exc_info=True)
            update_test_run_status(test_run_id, "FAILED", error=str(exc))

    background_tasks.add_task(
        _run_active_execution,
        test_run_id=test_run_id,
        dataset_id=dataset_id,
        table_name=request.table_name,
    )
    return ExecuteTestsResponse(test_run_id=test_run_id, status="QUEUED")


