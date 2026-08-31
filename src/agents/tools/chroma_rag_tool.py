"""Rule history tool — queries approved rules from PostgreSQL."""

from src.agents.tools.rule_proposer_tools import query_historical_approved_rules


def query_historical_rules(
    table_name: str,
    columns: list[str],
    top_k: int = 3,
) -> list[dict]:
    """Tra cứu các rule lịch sử đã được phê duyệt từ PostgreSQL.

    Args:
        table_name: Tên bảng cần tra cứu lịch sử rule.
        columns: Danh sách tên cột của bảng.
        top_k: Số lượng rule lịch sử tối đa trả về.

    Returns:
        Danh sách dict rule lịch sử.
    """
    try:
        res = query_historical_approved_rules.invoke(
            {"table_name": table_name, "limit": top_k}
        )
        return res.get("approved_rules", []) if isinstance(res, dict) else []
    except Exception:
        return []

