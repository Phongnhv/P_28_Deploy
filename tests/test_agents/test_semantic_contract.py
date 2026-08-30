import pytest

from src.agents.nodes.dataset_understanding_node import dataset_understanding_node
from src.agents.nodes.hitl_semantic_gate_node import hitl_semantic_gate_node
from src.agents.nodes.rule_candidate_builder_node import rule_candidate_builder_node
from src.agents.state import AgentState
from src.models.semantic_contract import TableSemanticContract


def test_prompt_customizer_context_merge_preserves_legacy_missing_tables():
    from src.agents.nodes.prompt_customizer_node import _merge_table_business_contexts

    state: AgentState = {
        "specialized_system_prompts": {
            "orders": "legacy orders",
            "customers": "legacy customers",
        },
        "table_business_contexts": {
            "orders": "current orders",
        },
    }

    assert _merge_table_business_contexts(state) == {
        "orders": "current orders",
        "customers": "legacy customers",
    }


@pytest.mark.asyncio
async def test_dataset_understanding_node(monkeypatch):
    """Kiểm tra xem dataset_understanding_node có phân tích và trả về Semantic Contract đúng định dạng."""
    mock_contract = TableSemanticContract(
        table_name="orders",
        domain="e-commerce",
        table_purpose="Lưu trữ thông tin đơn hàng",
        columns=[
            {
                "name": "order_id",
                "semantic_type": "identifier",
                "business_role": "primary_key",
                "nullable_expected": False,
                "confidence": 0.95,
                "description": "Mã đơn hàng duy nhất",
            },
            {
                "name": "order_total",
                "semantic_type": "currency",
                "business_role": "total_amount",
                "nullable_expected": False,
                "confidence": 0.9,
                "description": "Tổng số tiền thanh toán",
            },
        ],
        relationships=[],
        business_assumptions=[],
    )

    class MockStructuredLLM:
        async def ainvoke(self, messages):
            return mock_contract

    class MockLLM:
        def with_structured_output(self, schema):
            return MockStructuredLLM()

    monkeypatch.setattr("src.agents.nodes.dataset_understanding_node.get_llm", lambda provider, temperature: MockLLM())

    state: AgentState = {
        "dataset_id": "test_dataset",
        "dataset_profile_digest": {
            "orders": {
                "rows": 100,
                "columns": [
                    {"name": "order_id", "type": "TEXT", "null_pct": 0.0},
                    {"name": "order_total", "type": "REAL", "null_pct": 0.0},
                ],
            }
        },
        "metadata": {},
    }

    result = await dataset_understanding_node(state)
    assert "semantic_contract" in result
    contract = result["semantic_contract"]
    assert contract["dataset_id"] == "test_dataset"
    assert "orders" in contract["tables"]
    assert contract["tables"]["orders"]["table_name"] == "orders"
    assert contract["status"] == "draft"


@pytest.mark.asyncio
async def test_hitl_semantic_gate_node(tmp_path):
    """Kiểm tra cổng hitl_semantic_gate tạm dừng khi contract là nháp và đi tiếp khi đã confirmed."""
    draft_contract = {"dataset_id": "test_dataset", "tables": {}, "status": "draft"}

    state_draft: AgentState = {
        "rule_run_id": "test_run_123",
        "semantic_contract": draft_contract,
        "metadata": {"auto_confirm_semantic": False},
    }

    # Trường hợp draft -> Tạm dừng có chủ đích: báo qua `pause_reason`, KHÔNG phải `error`.
    # Dùng chung một trường cho "lỗi" và "chờ người duyệt" khiến runner không phân biệt được
    # hai tình huống và ghi đè trạng thái AWAITING_SEMANTIC_REVIEW thành DONE.
    res_draft = await hitl_semantic_gate_node(state_draft)
    assert res_draft.get("pause_reason") == "AWAITING_SEMANTIC_REVIEW"
    assert res_draft.get("error") is None
    assert res_draft.get("progress_state") == "WAITING_FOR_SEMANTIC_REVIEW"

    # Trường hợp confirmed -> Cho phép đi tiếp
    confirmed_contract = draft_contract.copy()
    confirmed_contract["status"] = "confirmed"
    state_confirmed: AgentState = {
        "rule_run_id": "test_run_123",
        "semantic_contract": confirmed_contract,
        "metadata": {},
    }

    res_confirmed = await hitl_semantic_gate_node(state_confirmed)
    assert "error" not in res_confirmed or res_confirmed["error"] != "AWAITING_SEMANTIC_REVIEW"
    assert res_confirmed.get("progress_state") == "PROPOSING_RULES"


def test_rule_candidate_builder_node():
    """Kiểm tra xem rule_candidate_builder_node có sinh ra các candidates chuẩn xác từ Semantic Contract."""
    contract = {
        "dataset_id": "test_dataset",
        "status": "confirmed",
        "tables": {
            "orders": {
                "table_name": "orders",
                "domain": "e-commerce",
                "table_purpose": "orders table",
                "columns": [
                    {
                        "name": "order_id",
                        "semantic_type": "identifier",
                        "business_role": "primary_key",
                        "nullable_expected": False,
                    },
                    {
                        "name": "order_total",
                        "semantic_type": "currency",
                        "business_role": "total_amount",
                        "nullable_expected": False,
                    },
                ],
                "relationships": [],
            }
        },
    }

    state: AgentState = {
        "semantic_contract": contract,
        "dataset_profile_digest": {
            "orders": {
                "rows": 100,
                "columns": [
                    {"name": "order_id", "role": "id", "signals": ["has_pk_constraint", "no_nulls"]},
                    {"name": "order_total", "role": "numeric", "signals": [], "quantiles": {"p5": 10.0, "p95": 100.0}},
                ],
            }
        },
    }

    res = rule_candidate_builder_node(state)
    candidates = res.get("rule_candidates", [])
    assert len(candidates) > 0

    # Kiểm tra xem có sinh NOT_NULL cho order_id không
    not_null_order_id = [c for c in candidates if c["column"] == "order_id" and c["rule_type"] == "NOT_NULL"]
    assert len(not_null_order_id) == 1

    # Kiểm tra xem có sinh UNIQUE cho order_id không
    unique_order_id = [c for c in candidates if c["column"] == "order_id" and c["rule_type"] == "UNIQUE"]
    assert len(unique_order_id) == 1

    # Kiểm tra xem có sinh RANGE cho order_total không
    range_total = [c for c in candidates if c["column"] == "order_total" and c["rule_type"] == "RANGE"]
    assert len(range_total) == 1
    # p5=10, p95=100 -> span=90 -> suggested_min = 10 - 9 = 1.0; suggested_max = 100 + 9 = 109.0
    assert range_total[0]["parameters"]["min"] == 1.0
    assert range_total[0]["parameters"]["max"] == 109.0


@pytest.mark.asyncio
async def test_prompt_customizer_node(monkeypatch):
    """Kiểm tra xem prompt_customizer_node có tạo ra table business context đúng như thiết kế."""
    from src.agents.nodes.prompt_customizer_node import prompt_customizer_node

    class MockLLMResponse:
        def __init__(self, content):
            self.content = content

    class MockLLM:
        async def ainvoke(self, messages):
            return MockLLMResponse("Đây là ngữ cảnh nghiệp vụ chi tiết cho bảng orders.")

    monkeypatch.setattr("src.agents.nodes.prompt_customizer_node.get_llm", lambda provider, temperature: MockLLM())

    contract = {
        "dataset_id": "test_dataset",
        "status": "confirmed",
        "tables": {"orders": {"table_name": "orders", "domain": "e-commerce", "table_purpose": "orders table"}},
    }

    state: AgentState = {"semantic_contract": contract, "table_business_contexts": {}, "specialized_system_prompts": {}}

    res = await prompt_customizer_node(state)
    contexts = res.get("table_business_contexts", {})
    prompts = res.get("specialized_system_prompts", {})
    assert "orders" in contexts
    assert contexts["orders"] == "Đây là ngữ cảnh nghiệp vụ chi tiết cho bảng orders."
    assert "orders" in prompts
    assert prompts["orders"] == "Đây là ngữ cảnh nghiệp vụ chi tiết cho bảng orders."
