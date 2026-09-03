"""Unit tests cho report_writer_node — LangGraph node viết báo cáo tiếng Việt bằng LLM."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.nodes.report_writer_node import (
    _build_data_context,
    _strip_code_fences,
    report_writer_node,
)
from src.services.report_renderer import render_steward_report_vi

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

EXECUTION_RUN_ID = "exec-writer-test-001"
DATASET_ID = "test_dataset"


def test_report_context_includes_rule_meaning_and_derives_missing_rate():
    result = {"rule_id": "opaque-rule", "rule_title": "rating must not be null",
              "status": "FAIL", "checked_count": 8807, "failed_count": 4,
              "violation_rate": None}
    context = _build_data_context("netflix-run", "netflix", {}, None, [result])
    assert "rating must not be null" in context
    assert "0.05%" in context
    assert result["violation_rate"] is None


def test_report_context_does_not_invent_rate_for_empty_execution():
    result = {"rule_id": "empty-rule", "status": "ERROR", "checked_count": 0,
              "failed_count": 0, "violation_rate": None}
    context = _build_data_context("empty-run", "netflix", {}, None, [result])
    assert "(N/A)" in context


SAMPLE_DQ_RUN = {
    "id": EXECUTION_RUN_ID,
    "dataset_id": DATASET_ID,
    "status": "SUCCEEDED",
    "total_failed": 2,
    "total_checked": 50000,
    "created_at": "2026-08-20 10:00:00",
    "completed_at": "2026-08-20 10:01:00",
    "error_message": None,
    "rule_ids": ["rule_a", "rule_b"],
}

SAMPLE_RESULTS = [
    {
        "id": 1,
        "rule_id": "source_rows.passenger_count.NULL_RATE",
        "rule_title": "NULL_RATE",
        "status": "FAIL",
        "checked_count": 50000,
        "failed_count": 7672,
        "violation_rate": 0.15344,
        "error_message": None,
    },
    {
        "id": 2,
        "rule_id": "source_rows.trip_distance.RANGE",
        "rule_title": "RANGE",
        "status": "PASS",
        "checked_count": 50000,
        "failed_count": 0,
        "violation_rate": 0.0,
        "error_message": None,
    },
]

SAMPLE_STATE = {
    "execution_run_id": EXECUTION_RUN_ID,
    "anomaly_run_id": "anom-writer-001",
    "dataset_id": DATASET_ID,
    "anomaly_decision": {
        "decision": "ANOMALY",
        "score": 0.85,
        "confidence": 0.80,
        "severity": "HIGH",
        "override_reason": "",
    },
    "signal_observations": [
        {
            "signal_id": "sig-001",
            "family": "NULL_RATE",
            "target_type": "COLUMN",
            "target_id": "source_rows.passenger_count",
            "score": 0.9,
            "reliability": 0.8,
            "explanation_code": "THRESHOLD_EXCEEDED",
        }
    ],
    "hypotheses": [
        {
            "hypothesis_type": "DATA_QUALITY_VIOLATION",
            "summary": "Tỷ lệ NULL cao bất thường ở cột passenger_count.",
            "confidence": 0.95,
            "supporting_signal_ids": ["sig-001"],
            "contradicting_signal_ids": [],
            "evidence_refs": ["source_rows.passenger_count.NULL_RATE"],
            "recommended_checks": ["Kiểm tra pipeline upstream.", "Xem log ingestion."],
            "missing_evidence": None,
            "limitations": None,
        }
    ],
    "hypothesis_status": "SUCCEEDED",
    "metadata": {},
}

NORMAL_STATE = {
    "execution_run_id": EXECUTION_RUN_ID,
    "anomaly_run_id": "anom-writer-002",
    "dataset_id": DATASET_ID,
    "anomaly_decision": {
        "decision": "NORMAL",
        "score": 0.05,
        "confidence": 0.95,
        "severity": "LOW",
        "override_reason": "",
    },
    "signal_observations": [],
    "hypotheses": [],
    "hypothesis_status": "NOT_REQUIRED",
    "metadata": {},
}

INSUFFICIENT_HISTORY_STATE = {
    **NORMAL_STATE,
    "anomaly_run_id": "anom-writer-003",
    "anomaly_decision": {
        "decision": "INSUFFICIENT_HISTORY",
        "score": 0.4,
        "confidence": 0.4,
        "severity": "LOW",
        "override_reason": "",
    },
}


def _mock_db(run=SAMPLE_DQ_RUN, results=SAMPLE_RESULTS):
    return patch(
        "src.agents.nodes.report_writer_node.report_writer_node.__wrapped__"
        if hasattr(report_writer_node, "__wrapped__")
        else "src.services.report_renderer._load_execution_data",
        return_value=(run, results),
    )


def _mock_load(run=SAMPLE_DQ_RUN, results=SAMPLE_RESULTS):
    return patch(
        "src.agents.nodes.report_writer_node._build_data_context.__globals__"
        if False
        else "src.services.report_renderer._load_execution_data",
        return_value=(run, results),
    )


# ---------------------------------------------------------------------------
# _strip_code_fences
# ---------------------------------------------------------------------------


def test_strip_code_fences_with_markdown():
    raw = "```markdown\n# Báo Cáo Data Steward\nNội dung\n```"
    result = _strip_code_fences(raw)
    assert result.startswith("# Báo Cáo Data Steward")
    assert "```" not in result


def test_strip_code_fences_with_md():
    raw = "```md\n# Báo Cáo Data Steward\n```"
    result = _strip_code_fences(raw)
    assert "# Báo Cáo Data Steward" in result


def test_strip_code_fences_plain_text():
    text = "# Báo Cáo Data Steward\nNội dung không có fences"
    assert _strip_code_fences(text) == text


def test_report_excludes_reasoning_and_tool_blocks():
    blocks = [
        {"type": "reasoning", "encrypted_content": "opaque-provider-payload", "text": "not report text"},
        {"type": "text", "text": "# Report\n"},
        {"type": "tool_use", "input": {"ignored": True}},
        {"type": "output_text", "text": "12 rows; 36 row checks."},
    ]
    assert _strip_code_fences(blocks) == "# Report\n12 rows; 36 row checks."


def test_strip_code_fences_empty_fences():
    raw = "```\n# Báo Cáo Data Steward\n```"
    result = _strip_code_fences(raw)
    assert "# Báo Cáo Data Steward" in result
    assert "```" not in result


# ---------------------------------------------------------------------------
# _build_data_context
# ---------------------------------------------------------------------------


def test_build_data_context_contains_key_info():
    ctx = _build_data_context(EXECUTION_RUN_ID, DATASET_ID, SAMPLE_STATE, SAMPLE_DQ_RUN, SAMPLE_RESULTS)
    assert EXECUTION_RUN_ID in ctx
    assert "ANOMALY" in ctx
    assert "passenger_count" in ctx  # rule_id in failed results
    assert "NULL_RATE" in ctx
    assert "15.34" in ctx  # violation rate


def test_build_data_context_normal_decision():
    ctx = _build_data_context(EXECUTION_RUN_ID, DATASET_ID, NORMAL_STATE, SAMPLE_DQ_RUN, [])
    assert "NORMAL" in ctx


def test_build_data_context_insufficient_history_does_not_call_it_normal():
    ctx = _build_data_context(
        EXECUTION_RUN_ID, DATASET_ID, INSUFFICIENT_HISTORY_STATE,
        SAMPLE_DQ_RUN, [],
    )
    assert "INSUFFICIENT_HISTORY" in ctx
    assert "kết luận là NORMAL" not in ctx


# ---------------------------------------------------------------------------
# report_writer_node — LLM success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_writer_llm_success(tmp_path):
    """LLM thành công → output tiếng Việt được dùng, file được tạo."""
    llm_response_text = (
        "# Báo Cáo Data Steward\n\n"
        "## 1. Tóm Tắt Điều Hành\n\n"
        f"Phiên thực thi `{EXECUTION_RUN_ID}` phát hiện bất thường.\n\n"
        "## 2. Thông Tin Phiên Chạy\n\nNội dung.\n"
    )

    mock_response = MagicMock()
    mock_response.content = llm_response_text

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)

    with (
        patch("src.services.report_renderer._load_execution_data", return_value=(SAMPLE_DQ_RUN, SAMPLE_RESULTS)),
        patch("src.agents.nodes.report_writer_node.get_llm", return_value=mock_llm),
        patch(
            "src.agents.nodes.report_writer_node._write_report_file",
            return_value=str(tmp_path / "steward_report_test.md"),
        ) as mock_write,
    ):
        output = await report_writer_node(SAMPLE_STATE)

    assert output["steward_report_path"] != ""
    assert output["metadata"]["steward_report_llm_used"] is True
    mock_write.assert_called_once()
    # Verify content passed to writer contains the LLM output
    written_content = mock_write.call_args[0][1]
    assert "# Báo Cáo Data Steward" in written_content


@pytest.mark.asyncio
async def test_report_writer_llm_missing_title_triggers_fallback(tmp_path):
    """LLM output thiếu tiêu đề bắt buộc → fallback template được dùng."""
    mock_response = MagicMock()
    mock_response.content = "Đây là output không có tiêu đề đúng."

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)

    written_contents = []

    def capture_write(exec_id, content, output_dir=None):
        written_contents.append(content)
        return str(tmp_path / f"steward_report_{exec_id}.md")

    with (
        patch("src.services.report_renderer._load_execution_data", return_value=(SAMPLE_DQ_RUN, SAMPLE_RESULTS)),
        patch("src.agents.nodes.report_writer_node.get_llm", return_value=mock_llm),
        patch("src.agents.nodes.report_writer_node._write_report_file", side_effect=capture_write),
    ):
        output = await report_writer_node(SAMPLE_STATE)

    assert output["metadata"]["steward_report_llm_used"] is False
    # Fallback content should have Vietnamese template marker
    assert written_contents
    assert "Báo Cáo Data Steward" in written_contents[0]
    assert "template" in written_contents[0].lower() or "tự động" in written_contents[0]


@pytest.mark.asyncio
async def test_report_writer_llm_exception_triggers_fallback(tmp_path):
    """LLM raise exception → fallback template được dùng."""
    written_contents = []

    def capture_write(exec_id, content, output_dir=None):
        written_contents.append(content)
        return str(tmp_path / f"steward_report_{exec_id}.md")

    with (
        patch("src.services.report_renderer._load_execution_data", return_value=(SAMPLE_DQ_RUN, SAMPLE_RESULTS)),
        patch("src.agents.nodes.report_writer_node.get_llm", side_effect=RuntimeError("API timeout")),
        patch("src.agents.nodes.report_writer_node._write_report_file", side_effect=capture_write),
    ):
        output = await report_writer_node(SAMPLE_STATE)

    assert output["metadata"]["steward_report_llm_used"] is False
    assert written_contents
    assert "Báo Cáo Data Steward" in written_contents[0]


@pytest.mark.asyncio
async def test_report_writer_state_fields_populated(tmp_path):
    """report_writer_node phải điền steward_report_path và metadata."""
    mock_response = MagicMock()
    mock_response.content = "# Báo Cáo Data Steward\n\nNội dung báo cáo."
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)

    with (
        patch("src.services.report_renderer._load_execution_data", return_value=(SAMPLE_DQ_RUN, SAMPLE_RESULTS)),
        patch("src.agents.nodes.report_writer_node.get_llm", return_value=mock_llm),
        patch("src.agents.nodes.report_writer_node._write_report_file", return_value="/some/path/report.md"),
    ):
        output = await report_writer_node(SAMPLE_STATE)

    assert "steward_report_path" in output
    assert "metadata" in output
    assert "steward_report_path" in output["metadata"]
    assert "steward_report_llm_used" in output["metadata"]


@pytest.mark.asyncio
async def test_report_writer_code_fences_stripped(tmp_path):
    """LLM trả về output bọc trong code fences → fences phải bị bỏ."""
    fenced_output = "```markdown\n# Báo Cáo Data Steward\n\nNội dung.\n```"
    mock_response = MagicMock()
    mock_response.content = fenced_output
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)

    written_contents = []

    def capture_write(exec_id, content, output_dir=None):
        written_contents.append(content)
        return str(tmp_path / f"steward_report_{exec_id}.md")

    with (
        patch("src.services.report_renderer._load_execution_data", return_value=(SAMPLE_DQ_RUN, SAMPLE_RESULTS)),
        patch("src.agents.nodes.report_writer_node.get_llm", return_value=mock_llm),
        patch("src.agents.nodes.report_writer_node._write_report_file", side_effect=capture_write),
    ):
        await report_writer_node(SAMPLE_STATE)

    assert written_contents
    assert "```" not in written_contents[0]
    assert "# Báo Cáo Data Steward" in written_contents[0]


# ---------------------------------------------------------------------------
# render_steward_report_vi — Vietnamese fallback template
# ---------------------------------------------------------------------------


def test_vi_fallback_contains_vietnamese_headings():
    with patch("src.services.report_renderer._load_execution_data", return_value=(SAMPLE_DQ_RUN, SAMPLE_RESULTS)):
        md = render_steward_report_vi(EXECUTION_RUN_ID, DATASET_ID, SAMPLE_STATE)

    assert "# Báo Cáo Data Steward" in md
    assert "Thông Tin Phiên Chạy" in md
    assert "Chi Tiết Rules Thất Bại" in md  # Section 3 heading
    assert "Kết Luận Phát Hiện Bất Thường" in md
    assert "Giả Thuyết Nguyên Nhân" in md


def test_vi_fallback_contains_execution_id():
    with patch("src.services.report_renderer._load_execution_data", return_value=(SAMPLE_DQ_RUN, SAMPLE_RESULTS)):
        md = render_steward_report_vi(EXECUTION_RUN_ID, DATASET_ID, SAMPLE_STATE)
    assert EXECUTION_RUN_ID in md


def test_vi_fallback_failed_rules_in_vietnamese():
    with patch("src.services.report_renderer._load_execution_data", return_value=(SAMPLE_DQ_RUN, SAMPLE_RESULTS)):
        md = render_steward_report_vi(EXECUTION_RUN_ID, DATASET_ID, SAMPLE_STATE)
    assert "passenger_count" in md
    assert "15.34%" in md


def test_vi_fallback_normal_decision():
    with patch("src.services.report_renderer._load_execution_data", return_value=(SAMPLE_DQ_RUN, [])):
        md = render_steward_report_vi(EXECUTION_RUN_ID, DATASET_ID, NORMAL_STATE)
    assert "Bình thường" in md or "NORMAL" in md
    assert "Phân tích giả thuyết không cần thiết" in md


def test_vi_fallback_insufficient_history_is_not_normal():
    with patch("src.services.report_renderer._load_execution_data", return_value=(SAMPLE_DQ_RUN, [])):
        md = render_steward_report_vi(EXECUTION_RUN_ID, DATASET_ID, INSUFFICIENT_HISTORY_STATE)
    assert "Chưa đủ lịch sử" in md
    assert "chưa đủ lịch sử dữ liệu" in md
    assert "Bình thường" not in md
    assert "NORMAL" not in md


def test_vi_fallback_has_fallback_note():
    """Template fallback phải luôn ghi chú rõ đây là template."""
    with patch("src.services.report_renderer._load_execution_data", return_value=(SAMPLE_DQ_RUN, SAMPLE_RESULTS)):
        md = render_steward_report_vi(EXECUTION_RUN_ID, DATASET_ID, SAMPLE_STATE)
    assert "template" in md.lower() or "tự động" in md.lower()
