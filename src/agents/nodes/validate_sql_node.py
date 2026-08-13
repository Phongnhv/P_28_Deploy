"""Validate SQL Node — Kiểm tra cú pháp và tính hợp lệ của câu lệnh SQL bằng EXPLAIN.

Không execute query thực sự, chỉ parse và build query plan qua DB engine.
Bắt được lỗi:
- Sai cú pháp SQL
- Tên bảng / tên cột không tồn tại
- Type mismatch
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import text

from src.agents.state import AgentState
from src.services.rule_store import get_engine

logger = logging.getLogger(__name__)


def validate_single_sql(sql: str, params: dict, dialect_name: str = "sqlite") -> tuple[bool, str | None]:
    """Chạy EXPLAIN để kiểm tra câu lệnh SQL mà không thực thi."""
    explain_prefix = "EXPLAIN QUERY PLAN " if dialect_name == "sqlite" else "EXPLAIN "
    explain_sql = f"{explain_prefix}{sql}"

    engine = get_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text(explain_sql), params)
        return True, None
    except Exception as exc:
        error_msg = str(exc)
        logger.warning("SQL validation failed: %s | Query: %s", error_msg, sql)
        return False, error_msg


async def validate_sql_node(state: AgentState) -> dict:
    """LangGraph Node: Kiểm tra cú pháp toàn bộ test queries trong state."""
    tests = state.get("generated_tests", [])
    engine = get_engine()
    dialect_name = engine.dialect.name

    updated_tests = []
    has_invalid_with_retries = False

    for test in tests:
        # Nếu đã valid hoặc đã hết lượt retry -> giữ nguyên
        if test.get("valid") is True:
            updated_tests.append(test)
            continue

        sql = test.get("sql_text", "")
        params = test.get("bind_params", {})
        attempts = test.get("attempts", 0)

        is_valid, error_msg = validate_single_sql(sql, params, dialect_name)
        test_copy = dict(test)
        test_copy["valid"] = is_valid
        test_copy["error"] = error_msg

        if not is_valid and attempts < 3:
            has_invalid_with_retries = True

        updated_tests.append(test_copy)

    logger.info(
        "Kết quả validate SQL: %d/%d queries valid (còn cần sửa: %s)",
        sum(1 for t in updated_tests if t.get("valid")),
        len(updated_tests),
        has_invalid_with_retries,
    )

    run_id = state.get("rule_run_id") or state.get("test_run_id") or "test_run"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Xuất trace file
    try:
        import json
        from pathlib import Path

        from src.config import get_settings
        settings = get_settings()
        base_dir = getattr(settings, "output_dir", None) or "./output"
        out_dir = Path(base_dir) / "validate_sql"
        out_dir.mkdir(parents=True, exist_ok=True)
        dump_file = out_dir / f"debug_validated_tests_{timestamp}_{run_id}.json"
        dump_file.write_text(
            json.dumps(updated_tests, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("Đã xuất trace validated tests ra: %s", dump_file)
    except Exception as exc:
        logger.warning("Không thể ghi file trace validated tests: %s", exc)

    return {
        "generated_tests": updated_tests,
    }


# ---------------------------------------------------------------------------
# Standalone Test Harness (Chạy từ file output trước đó)
# ---------------------------------------------------------------------------

async def main():
    """Hàm chạy test độc lập cho validate_sql_node từ file output thực tế.

    Run: python -m src.agents.nodes.validate_sql_node
    """
    import glob
    import json
    import os

    from src.services.rule_store import init_db

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    print("\n" + "=" * 75)
    print("🚀 CHẠY THỬ ĐỘC LẬP: validate_sql_node (Dry-run EXPLAIN trên DB thật)")
    print("=" * 75)

    init_db()

    # Tìm file output từ test_generator_node
    search_pattern = "output/test_generator/debug_generated_tests_*.json"
    files = glob.glob(search_pattern)

    if not files:
        print("❌ Không tìm thấy file trong output/test_generator/.")
        print("💡 Hãy chạy test_generator_node trước: python -m src.agents.nodes.test_generator_node")
        return

    latest_file = sorted(files, key=os.path.getmtime)[-1]
    print(f"📖 Đọc generated tests từ: {latest_file}")

    with open(latest_file, encoding="utf-8") as f:
        tests = json.load(f)

    print(f"🎯 Tổng số queries nạp để validate: {len(tests)}")

    state: AgentState = {
        "dataset_id": "yellow_tripdata",
        "generated_tests": tests,
    }

    res = await validate_sql_node(state)

    tests_res = res.get("generated_tests", [])
    valid_count = sum(1 for t in tests_res if t.get("valid"))
    invalid_count = len(tests_res) - valid_count

    print(f"\n📊 Kết quả validate: {valid_count} VALID | {invalid_count} INVALID")
    for idx, t in enumerate(tests_res, 1):
        status_icon = "✅ VALID" if t["valid"] else "❌ INVALID"
        print(f"\n[{idx}] Test ID : {t['test_id']} ➔ {status_icon}")
        print(f"    SQL     : {t['sql_text']}")
        if not t["valid"]:
            print(f"    Lỗi     : {t.get('error')}")

    print("\n" + "=" * 75 + "\n")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())


