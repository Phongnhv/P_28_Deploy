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
    generated_dbt_yaml: str      # YAML content retained in state for observability only
    dbt_artifact_ref: dict       # Run-scoped object-storage reference for the dbt YAML
    dbt_trace_file_path: str     # Exact local/test fallback path for this run
    dbt_validation_valid: bool   # Whether the generated dbt project passed the quality gate
    dbt_validation_error: str | None
    dbt_validation_attempts: int
    dbt_validation_trace_path: str
    dbt_repair_history: list
    test_results: list           # Danh sách kết quả thực thi từng rule
    test_generation_errors: list # Danh sách lỗi phát sinh trong quá trình sinh/sửa/chạy test

    # Run 2: Steward Insights & DQ Score fields
    dq_score: float              # Điểm chất lượng tổng thể (0.0 - 100.0)
    dq_grade: str                # Xếp hạng chất lượng dữ liệu (A / B / C / D)
    dq_dimensions: dict          # Điểm theo từng chiều DQ (COMPLETENESS, VALIDITY, etc.)
    steward_summary: str         # Báo cáo tổng kết Markdown cho Data Steward
    remediation_actions: list    # Danh sách hành động khuyến nghị dạng structured

    # Generalised scope expansion fields
    target_tables: list[str]                  # Bảng mục tiêu cần chạy phân tích và đề xuất
    normalized_data_dictionary: dict           # User-supplied or LLM-inferred dictionary
    data_dictionary_source: str               # supplied | inferred
    semantic_contract: dict                   # Hợp đồng ngữ nghĩa (Semantic Contract) được đề xuất hoặc đã duyệt
    rule_candidates: list[dict]               # Danh sách các candidates deterministic sinh từ Semantic Contract
    specialized_system_prompts: dict[str, str] # System prompt tĩnh đã được custom hóa ngữ cảnh theo từng bảng
    progress_state: str                       # Trạng thái tiến trình của Agent (PROFILING, etc.)
