import asyncio
import logging
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

# Add project root to path
sys.path.append(str(Path(__file__).resolve().parents[1]))

import src.services.rule_store as rule_store_module
from src.agents.graph import build_proposal_graph
from src.services.rule_store import init_db, list_rules

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("run_ecommerce_proposal_test")


async def run_test():
    logger.info("Initializing SQLite test database for E-commerce dataset...")
    db_file = Path("output/test_ecommerce.db")
    if db_file.exists():
        try:
            db_file.unlink()
        except Exception:
            pass

    db_url = f"sqlite:///{db_file.as_posix()}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})

    # Mock the rule_store engine
    rule_store_module._engine = engine
    init_db()

    # Create an E-commerce schema and insert test rows
    with engine.connect() as conn:
        conn.execute(text("DROP TABLE IF EXISTS orders;"))
        conn.execute(
            text("""
            CREATE TABLE orders (
                order_id TEXT PRIMARY KEY,
                customer_id TEXT,
                order_total REAL,
                order_status TEXT,
                created_at TEXT
            );
        """)
        )
        conn.execute(
            text("""
            INSERT INTO orders VALUES
            ('ORD001', 'CUST_A', 150.0, 'DELIVERED', '2026-08-18 10:00:00'),
            ('ORD002', 'CUST_B', 85.50, 'SHIPPED', '2026-08-18 10:30:00'),
            ('ORD003', 'CUST_C', 200.0, 'DELIVERED', '2026-08-18 11:00:00'),
            ('ORD004', 'CUST_A', 45.00, 'DELIVERED', '2026-08-18 12:00:00'),
            ('ORD005', 'CUST_D', 120.0, 'PROCESSING', '2026-08-18 12:15:00');
        """)
        )
        conn.commit()

    logger.info("Database loaded. Mocking LLM outputs for structured nodes...")

    from src.models.rule_schemas import (
        DataQualityDimension,
        ProposalBasis,
        ProposedRule,
        RuleConfidence,
        RuleType,
        Severity,
        TableRuleProposal,
    )
    from src.models.semantic_contract import SemanticColumn, TableSemanticContract

    mock_semantic_contract = TableSemanticContract(
        table_name="orders",
        domain="e-commerce",
        table_purpose="Bảng lưu thông tin đơn hàng",
        columns=[
            SemanticColumn(
                name="order_id",
                semantic_type="identifier",
                business_role="primary_key",
                nullable_expected=False,
                confidence=1.0,
                description="Mã đơn hàng",
            ),
            SemanticColumn(
                name="customer_id",
                semantic_type="identifier",
                business_role="customer_id",
                nullable_expected=False,
                confidence=1.0,
                description="Mã khách hàng",
            ),
            SemanticColumn(
                name="order_total",
                semantic_type="currency",
                business_role="transaction_amount",
                nullable_expected=False,
                confidence=1.0,
                description="Tổng số tiền thanh toán",
            ),
            SemanticColumn(
                name="order_status",
                semantic_type="category",
                business_role="order_status",
                nullable_expected=False,
                confidence=1.0,
                description="Trạng thái đơn hàng",
            ),
            SemanticColumn(
                name="created_at",
                semantic_type="timestamp",
                business_role="created_at",
                nullable_expected=False,
                confidence=1.0,
                description="Thời gian tạo đơn hàng",
            ),
        ],
        relationships=[],
        business_assumptions=["Mỗi đơn hàng có một mã duy nhất và tổng tiền thanh toán không được phép âm."],
    )

    mock_proposal = TableRuleProposal(
        table="orders",
        rules=[
            ProposedRule(
                candidate_id="orders.order_id.NOT_NULL",
                column="order_id",
                rule_type=RuleType.NOT_NULL,
                parameters={},
                rule_name="Yêu cầu bắt buộc nhập mã đơn hàng",
                business_rationale="Mã đơn hàng là định danh bắt buộc để theo dõi trạng thái giao dịch và đối soát.",
                proposal_basis=ProposalBasis.DATA_PROFILE,
                selected_evidence_refs=["profile:order_id:no_nulls"],
                confidence=RuleConfidence(
                    overall=1.0,
                    evidence_strength=1.0,
                    business_support=1.0,
                    sample_representativeness=1.0,
                    explanation="Không có giá trị null nào được phát hiện.",
                ),
                severity=Severity.CRITICAL,
                dimension=DataQualityDimension.COMPLETENESS,
                rule_description="Mã đơn hàng (order_id) không được mang giá trị rỗng.",
                ai_reasoning="Profile thực tế ghi nhận 0.0% dòng trống trên tổng số 5 đơn hàng.",
            ),
            ProposedRule(
                candidate_id="orders.order_total.RANGE",
                column="order_total",
                rule_type=RuleType.RANGE,
                parameters={"min": 0.0},
                rule_name="Tổng số tiền thanh toán không được âm",
                business_rationale="Số tiền thanh toán cho đơn hàng phải luôn lớn hơn hoặc bằng 0.",
                proposal_basis=ProposalBasis.DATA_PROFILE,
                selected_evidence_refs=["profile:order_total:range"],
                confidence=RuleConfidence(
                    overall=0.9,
                    evidence_strength=0.9,
                    business_support=0.9,
                    sample_representativeness=0.9,
                    explanation="Giá trị quan sát tối thiểu là 45.0, phù hợp quy chuẩn.",
                ),
                severity=Severity.HIGH,
                dimension=DataQualityDimension.VALIDITY,
                rule_description="Tổng tiền thanh toán (order_total) phải lớn hơn hoặc bằng 0.0.",
                ai_reasoning="Dải giá trị thực tế quan sát được là từ 45.0 đến 200.0.",
            ),
        ],
    )

    class MockStructuredLLM:
        def __init__(self, schema):
            self.schema = schema

        async def ainvoke(self, messages):
            if self.schema == TableSemanticContract:
                return mock_semantic_contract
            elif self.schema == TableRuleProposal:
                return mock_proposal
            raise ValueError(f"Unknown schema: {self.schema}")

    class MockLLMResponse:
        def __init__(self, content):
            self.content = content

    class MockLLM:
        def with_structured_output(self, schema):
            return MockStructuredLLM(schema)

        async def ainvoke(self, messages):
            return MockLLMResponse("Đây là system prompt chuyên biệt đã được custom cho bảng orders.")

    # Inject mock into nodes
    import src.agents.nodes.dataset_understanding_node as du_node
    import src.agents.nodes.prompt_customizer_node as pc_node
    import src.agents.nodes.rule_proposer_node as rp_node

    du_node.get_llm = lambda provider, temperature: MockLLM()
    rp_node.get_llm = lambda provider, temperature: MockLLM()
    pc_node.get_llm = lambda provider, temperature: MockLLM()

    logger.info("Running generalized proposal graph end-to-end...")

    proposal_graph = build_proposal_graph()
    initial_state = {
        "dataset_id": "ecommerce_orders",
        "target_tables": ["orders"],
        "metadata": {
            "connection_string": db_url,
            "sampling_rate": 1.0,
            "auto_confirm_semantic": True,
            "domain_hint": "E-commerce transactional database with orders, customers, and order amounts.",
        },
    }

    final_state = await proposal_graph.ainvoke(initial_state)

    if "error" in final_state and final_state["error"]:
        logger.error(f"Graph execution failed with error: {final_state['error']}")
        sys.exit(1)

    run_id = final_state.get("rule_run_id")
    proposed_rules = list_rules(run_id)

    logger.info("=" * 75)
    logger.info("E-COMMERCE PROPOSAL TEST COMPLETED SUCCESSFULLY!")
    logger.info(f"Run ID: {run_id}")
    logger.info(f"Total Rules Proposed: {len(proposed_rules)}")
    logger.info("=" * 75)

    for i, r in enumerate(proposed_rules, 1):
        logger.info(f"[{i}] Rule: {r.get('rule_id')} ({r.get('dimension')})")
        logger.info(f"    Name: {r.get('rule_name')}")
        logger.info(f"    Description: {r.get('rule_description')}")
        logger.info(f"    Business Rationale: {r.get('business_rationale')}")
        logger.info(f"    AI Reasoning: {r.get('ai_reasoning')}")

    assert len(proposed_rules) > 0, "No rules proposed!"
    logger.info("Validation tests passed!")


if __name__ == "__main__":
    asyncio.run(run_test())
