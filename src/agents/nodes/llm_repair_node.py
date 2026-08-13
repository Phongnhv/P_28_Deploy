"""LLM Repair Node — Tự động sửa lỗi cú pháp / schema của SQL test query thông qua LLM.

Agentic Loop:
1. Đọc query bị lỗi và DB traceback.
2. Lấy schema thực tế của bảng qua inspector.
3. Gửi cho LLM sửa SQL.
4. Kiểm tra an toàn (chỉ chấp nhận SELECT).
5. Tăng `attempts += 1` và chuyển lại cho `validate_sql_node`.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime

from sqlalchemy import inspect

from src.agents.nodes.templates import sql_repair_prompt
from src.agents.state import AgentState
from src.config import get_settings
from src.services.llm import get_llm
from src.services.rule_store import get_engine

logger = logging.getLogger(__name__)


def _extract_sql(llm_output: str) -> str:
    """Trích xuất câu SQL từ khối markdown ```sql ... ``` hoặc raw string."""
    match = re.search(r"```(?:sql)?\s*(SELECT[\s\S]*?)```", llm_output, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    # Nếu không có code block, kiểm tra xem có bắt đầu bằng SELECT không
    stripped = llm_output.strip()
    if stripped.upper().startswith("SELECT"):
        return stripped
    return llm_output.strip()


def _is_safe_select(sql: str) -> bool:
    """Kiểm tra an toàn: chỉ cho phép SELECT, cấm DDL/DML."""
    upper = sql.upper().strip()
    if not upper.startswith("SELECT"):
        return False
    dangerous_keywords = ["DROP ", "DELETE ", "UPDATE ", "INSERT ", "ALTER ", "TRUNCATE ", "EXEC ", "CREATE "]
    for kw in dangerous_keywords:
        if kw in upper:
            return False
    return True


def _get_table_schema_info(table_name: str) -> list[dict]:
    """Lấy danh sách các cột và kiểu dữ liệu của bảng từ DB inspector."""
    engine = get_engine()
    inspector = inspect(engine)
    try:
        columns = inspector.get_columns(table_name)
        return [{"name": c["name"], "type": str(c["type"])} for c in columns]
    except Exception as exc:
        logger.warning("Không thể lấy schema cho bảng %s: %s", table_name, exc)
        return []


async def llm_repair_node(state: AgentState) -> dict:
    """LangGraph Node: Duyệt qua các query lỗi và gọi LLM sửa."""
    tests = state.get("generated_tests", [])
    settings = get_settings()
    llm = get_llm(settings.llm_provider, temperature=0.0)

    updated_tests = []
    errors = list(state.get("test_generation_errors", []))

    for test in tests:
        if test.get("valid") is True or test.get("attempts", 0) >= 3:
            updated_tests.append(test)
            continue

        test_copy = dict(test)
        table_name = test_copy.get("table_name", "")
        schema_info = _get_table_schema_info(table_name)
        rules_meta = test_copy.get("rules_meta", [])
        rules_data = [m.get("rule", {}) for m in rules_meta]

        prompt_messages = sql_repair_prompt.format_messages(
            table_name=table_name,
            schema_info=json.dumps(schema_info, ensure_ascii=False, indent=2),
            rules_json=json.dumps(rules_data, ensure_ascii=False, indent=2),
            error_sql=test_copy.get("sql_text", ""),
            db_error=test_copy.get("error", "Unknown error"),
        )

        test_copy["attempts"] = test_copy.get("attempts", 0) + 1
        logger.info(
            "Đang gọi LLM sửa SQL cho test %s (lần thử %d/3)...",
            test_copy.get("test_id"),
            test_copy["attempts"],
        )

        try:
            response = await llm.ainvoke(prompt_messages)
            repaired_sql = _extract_sql(response.content)

            if _is_safe_select(repaired_sql):
                test_copy["sql_text"] = repaired_sql
                logger.info("LLM đã sinh câu SQL mới cho test %s", test_copy.get("test_id"))
            else:
                msg = f"LLM trả về câu SQL không an toàn (không phải SELECT): {repaired_sql}"
                logger.warning(msg)
                test_copy["error"] = msg
        except Exception as exc:
            msg = f"Lỗi khi gọi LLM repair cho test {test_copy.get('test_id')}: {exc}"
            logger.error(msg)
            test_copy["error"] = msg
            errors.append(msg)

        updated_tests.append(test_copy)

    run_id = state.get("rule_run_id") or state.get("test_run_id") or "test_run"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Xuất trace file
    try:
        from pathlib import Path
        settings = get_settings()
        base_dir = getattr(settings, "output_dir", None) or "./output"
        out_dir = Path(base_dir) / "llm_repair"
        out_dir.mkdir(parents=True, exist_ok=True)
        dump_file = out_dir / f"debug_repaired_tests_{timestamp}_{run_id}.json"
        dump_file.write_text(
            json.dumps(updated_tests, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("Đã xuất trace repaired tests ra: %s", dump_file)
    except Exception as exc:
        logger.warning("Không thể ghi file trace repaired tests: %s", exc)

    return {
        "generated_tests": updated_tests,
        "test_generation_errors": errors,
    }


# ---------------------------------------------------------------------------
# Standalone Test Harness (Chạy từ file output hoặc demo)
# ---------------------------------------------------------------------------

async def main():
    """Hàm chạy test độc lập cho llm_repair_node (Agentic Repair Loop).

    Run: python -m src.agents.nodes.llm_repair_node
    """
    import glob
    import os

    from src.services.rule_store import init_db

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    print("\n" + "=" * 75)
    print("🚀 CHẠY THỬ ĐỘC LẬP: llm_repair_node (Agentic Repair Loop)")
    print("=" * 75)

    init_db()

    # Kiểm tra xem có file invalid tests từ validate_sql không
    files = glob.glob("output/validate_sql/debug_validated_tests_*.json")
    bad_tests = []
    if files:
        latest_file = sorted(files, key=os.path.getmtime)[-1]
        with open(latest_file, encoding="utf-8") as f:
            all_tests = json.load(f)
        bad_tests = [t for t in all_tests if not t.get("valid") and t.get("attempts", 0) < 3]

    if bad_tests:
        print(f"📖 Tìm thấy {len(bad_tests)} queries bị lỗi từ file: {latest_file}")
        state: AgentState = {
            "dataset_id": "yellow_tripdata",
            "generated_tests": bad_tests,
        }
    else:
        print("💡 Không có query lỗi thực tế nào trong output/validate_sql/.")
        print("🛠️ Đang tạo kịch bản demo: 1 câu SQL gõ nhầm tên cột trên bảng yellow_tripdata...")
        demo_bad_test = {
            "test_id": "test_typo_demo",
            "table_name": "yellow_tripdata",
            "sql_text": 'SELECT COUNT(*) AS total_rows, SUM(CASE WHEN "fare_amout_typo" < 0 THEN 1 ELSE 0 END) AS v_0 FROM "yellow_tripdata"',
            "bind_params": {},
            "rules_meta": [
                {
                    "rule": {
                        "rule_id": "yellow_tripdata.fare_amount.RANGE",
                        "table_name": "yellow_tripdata",
                        "column": "fare_amount",
                        "rule_type": "RANGE",
                        "parameters": {"min": 0.0},
                    },
                    "alias": "v_0",
                }
            ],
            "attempts": 0,
            "valid": False,
            "error": 'no such column: "fare_amout_typo"',
        }
        state: AgentState = {
            "dataset_id": "yellow_tripdata",
            "generated_tests": [demo_bad_test],
        }

    print("🤖 Đang gọi LLM tự động sửa SQL...")
    res = await llm_repair_node(state)

    repaired_list = res.get("generated_tests", [])
    for idx, t in enumerate(repaired_list, 1):
        print(f"\n[{idx}] Test ID: {t['test_id']} (Lần thử {t.get('attempts')}/3)")
        print(f"    SQL sau khi sửa: {t['sql_text']}")

    print("\n" + "=" * 75 + "\n")



if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

