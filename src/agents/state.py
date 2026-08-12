from __future__ import annotations

from typing import TypedDict


class AgentState(TypedDict, total=False):
    """State schema cho LangGraph agent.

    Mỗi node đọc và ghi vào state này.
    total=False cho phép tất cả fields là optional.
    """

    query: str
    context: str
    analysis: str
    response: str
    error: str
    metadata: dict
    profiler_result: dict
    dataset_profile_digest: dict

    # RidePulse DQ specific state fields
    dataset_id: str
    proposed_rules: list
    approved_rules: list
    execution_results: dict
    anomalies: list
    rule_proposal_errors: list   # bảng bị lỗi trong fan-out rule proposer
    rule_run_id: str             # batch key dùng để lọc Rule Review Screen
    dataset_profile: dict        # raw profile từ raw_profiler_node

    # Run 2: Test Execution state fields
    test_run_id: str             # ID của phiên chạy test (Run 2)
    generated_tests: list        # Danh sách test queries được render và validated
    test_results: list           # Danh sách kết quả thực thi từng rule
    test_generation_errors: list # Danh sách lỗi phát sinh trong quá trình sinh/sửa/chạy test


