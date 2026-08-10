"""ChromaDB RAG tool — placeholder until ChromaDB được seed với lịch sử rule.

Giữ nguyên signature để wiring ChromaDB thật sau này không cần sửa node.
"""


def query_historical_rules(
    table_name: str,
    columns: list[str],
    top_k: int = 3,
) -> list[dict]:
    """RAG placeholder. Trả về [] cho tới khi ChromaDB được seed.

    Args:
        table_name: Tên bảng cần tra cứu lịch sử rule.
        columns: Danh sách tên cột của bảng.
        top_k: Số lượng rule lịch sử tối đa trả về.

    Returns:
        Danh sách dict rule lịch sử (rỗng trong giai đoạn stub).
    """
    return []
