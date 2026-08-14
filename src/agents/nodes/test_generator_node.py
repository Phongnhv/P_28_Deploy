"""Test Generator Node — Biên dịch approved rules thành các câu lệnh SQL kiểm thử tối ưu.

Đặc điểm:
1. Deterministic template rendering (không dùng LLM ở bước sinh mã).
2. Query Batching: Gom tất cả row-level rules trên cùng 1 bảng thành 1 câu SQL duy nhất
   (dùng SUM(CASE WHEN ...)) để tránh quét bảng nhiều lần.
3. An toàn bảo mật:
   - Identifier (tên bảng, cột) được escape/quote.
   - Values (min, max, regex, accepted values) luôn dùng bind parameters (:param).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from src.agents.state import AgentState
from src.config import get_settings
from src.models.rule_schemas import RuleType
from src.services.rule_store import get_approved_rules, get_engine

logger = logging.getLogger(__name__)


def _quote_ident(ident: str, dialect_name: str = "sqlite") -> str:
    """Quote identifier an toàn theo dialect (SQLite dùng double quotes hoặc backticks, Postgres dùng double quotes)."""
    clean_ident = ident.replace('"', '""').strip()
    return f'"{clean_ident}"'


def _build_row_predicate(
    rule: dict,
    rule_idx: int,
    dialect_name: str,
) -> tuple[str, dict[str, Any]]:
    """Tạo biểu thức điều kiện vi phạm (predicate) và dictionary bind params cho 1 row-level rule.

    Trả về (predicate_sql, bind_params).
    Khi predicate = TRUE nghĩa là dòng đó VI PHẠM rule.
    """
    col = rule.get("column") or ""
    quoted_col = _quote_ident(col, dialect_name)
    rule_type = rule.get("rule_type", "")
    params = rule.get("effective_parameters") or rule.get("parameters") or {}
    binds: dict[str, Any] = {}

    if rule_type == RuleType.NOT_NULL.value or rule_type == "NOT_NULL":
        return f"{quoted_col} IS NULL", binds

    if rule_type == RuleType.RANGE.value or rule_type == "RANGE":
        conds = []
        if params.get("min") is not None:
            p_min = f"p_min_{rule_idx}"
            binds[p_min] = float(params["min"])
            conds.append(f"{quoted_col} < :{p_min}")
        if params.get("max") is not None:
            p_max = f"p_max_{rule_idx}"
            binds[p_max] = float(params["max"])
            conds.append(f"{quoted_col} > :{p_max}")
        if conds:
            return f"({quoted_col} IS NOT NULL AND ({' OR '.join(conds)}))", binds
        return "1=0", binds  # Không có param -> pass

    if rule_type == RuleType.ACCEPTED_VALUES.value or rule_type == "ACCEPTED_VALUES":
        accepted_vals = params.get("accepted_values") or []
        if not accepted_vals:
            return "1=0", binds
        val_placeholders = []
        for i, val in enumerate(accepted_vals):
            p_val = f"p_val_{rule_idx}_{i}"
            binds[p_val] = str(val)
            val_placeholders.append(f":{p_val}")
        in_list = ", ".join(val_placeholders)
        return f"({quoted_col} IS NOT NULL AND {quoted_col} NOT IN ({in_list}))", binds

    if rule_type == RuleType.REGEX_FORMAT.value or rule_type == "REGEX_FORMAT":
        regex_pattern = params.get("regex") or ""
        p_regex = f"p_regex_{rule_idx}"
        binds[p_regex] = regex_pattern
        if dialect_name == "postgresql":
            return f"({quoted_col} IS NOT NULL AND {quoted_col} !~ :{p_regex})", binds
        else:
            # SQLite (đã đăng ký custom function REGEXP)
            return f"({quoted_col} IS NOT NULL AND {quoted_col} NOT REGEXP :{p_regex})", binds

    if rule_type in (RuleType.CROSS_FIELD_COMPARISON.value, "CROSS_FIELD_COMPARISON"):
        target_col = params.get("target_column") or ""
        op = (params.get("operator") or "<=").strip()
        op_whitelist = {
            "<=": "<=",
            "<": "<",
            ">=": ">=",
            ">": ">",
            "=": "=",
            "==": "=",
            "!=": "!=",
            "<>": "!=",
        }
        safe_op = op_whitelist.get(op)
        if safe_op is None:
            logger.warning("Unknown operator %s in CROSS_FIELD_COMPARISON, falling back to <=", op)
            safe_op = "<="
        quoted_target = _quote_ident(target_col, dialect_name)
        if not col or not target_col:
            return "1=0", binds
        # Dòng vi phạm khi cả 2 cột đều không NULL và KHÔNG thỏa mãn điều kiện so sánh
        return f"({quoted_col} IS NOT NULL AND {quoted_target} IS NOT NULL AND NOT ({quoted_col} {safe_op} {quoted_target}))", binds

    return "1=0", binds


def generate_tests_for_table(
    table_name: str,
    rules: list[dict],
    dialect_name: str = "sqlite",
) -> list[dict]:
    """Tạo danh sách các test queries cho một bảng: gom row-level rules và tách group-level rules."""
    generated: list[dict] = []
    quoted_table = _quote_ident(table_name, dialect_name)

    row_level_types = {
        RuleType.NOT_NULL.value, "NOT_NULL",
        RuleType.RANGE.value, "RANGE",
        RuleType.ACCEPTED_VALUES.value, "ACCEPTED_VALUES",
        RuleType.REGEX_FORMAT.value, "REGEX_FORMAT",
        RuleType.NULL_RATE.value, "NULL_RATE",
        RuleType.CROSS_FIELD_COMPARISON.value, "CROSS_FIELD_COMPARISON",
    }

    row_rules = [r for r in rules if r.get("rule_type") in row_level_types]
    group_rules = [r for r in rules if r.get("rule_type") not in row_level_types]


    # 1. Gom các row-level rules thành 1 batch query
    if row_rules:
        select_clauses = ["COUNT(*) AS total_rows"]
        batch_binds: dict[str, Any] = {}
        rule_meta_list = []

        for idx, rule in enumerate(row_rules):
            alias = f"v_{idx}"
            r_type = rule.get("rule_type")
            if r_type in (RuleType.NULL_RATE.value, "NULL_RATE"):
                col = rule.get("column") or ""
                quoted_col = _quote_ident(col, dialect_name)
                pred = f"{quoted_col} IS NULL"
                rule_binds = {}
            else:
                pred, rule_binds = _build_row_predicate(rule, idx, dialect_name)

            select_clauses.append(f"SUM(CASE WHEN {pred} THEN 1 ELSE 0 END) AS {alias}")
            batch_binds.update(rule_binds)
            rule_meta_list.append({
                "rule": rule,
                "alias": alias,
                "predicate": pred,
            })

        batch_sql = f"SELECT {', '.join(select_clauses)} FROM {quoted_table}"
        generated.append({
            "test_id": f"batch_{table_name}",
            "table_name": table_name,
            "query_type": "batch_row",
            "sql_text": batch_sql,
            "bind_params": batch_binds,
            "rules_meta": rule_meta_list,
            "attempts": 0,
            "valid": False,
            "error": None,
        })

    # 2. Xử lý các group/table-level rules riêng biệt
    for idx, rule in enumerate(group_rules):
        r_type = rule.get("rule_type")
        col = rule.get("column") or ""
        quoted_col = _quote_ident(col, dialect_name)
        params = rule.get("effective_parameters") or rule.get("parameters") or {}

        if r_type in (RuleType.UNIQUE.value, "UNIQUE"):
            sql = (
                f"SELECT "
                f"(SELECT COUNT(*) FROM {quoted_table}) AS total_rows, "
                f"(SELECT COUNT(*) FROM {quoted_table} WHERE {quoted_col} IS NOT NULL) - "
                f"(SELECT COUNT(DISTINCT {quoted_col}) FROM {quoted_table} WHERE {quoted_col} IS NOT NULL) AS violation_count"
            )
            generated.append({
                "test_id": f"unique_{table_name}_{col}_{idx}",
                "table_name": table_name,
                "query_type": "unique",
                "sql_text": sql,
                "bind_params": {},
                "rules_meta": [{"rule": rule, "alias": "violation_count", "predicate": None}],
                "attempts": 0,
                "valid": False,
                "error": None,
            })

        elif r_type in (RuleType.ROW_COUNT.value, "ROW_COUNT"):
            sql = f"SELECT COUNT(*) AS total_rows FROM {quoted_table}"
            min_rows = int(params.get("min_row_count", 0))
            generated.append({
                "test_id": f"row_count_{table_name}_{idx}",
                "table_name": table_name,
                "query_type": "row_count",
                "sql_text": sql,
                "bind_params": {},
                "min_row_count": min_rows,
                "rules_meta": [{"rule": rule, "alias": "total_rows", "predicate": None}],
                "attempts": 0,
                "valid": False,
                "error": None,
            })

        elif r_type in (RuleType.FRESHNESS.value, "FRESHNESS"):
            sql = f"SELECT MAX({quoted_col}) AS max_timestamp FROM {quoted_table}"
            max_age_hours = float(params.get("max_age_hours", 24.0))
            generated.append({
                "test_id": f"freshness_{table_name}_{col}_{idx}",
                "table_name": table_name,
                "query_type": "freshness",
                "sql_text": sql,
                "bind_params": {},
                "max_age_hours": max_age_hours,
                "rules_meta": [{"rule": rule, "alias": "max_timestamp", "predicate": None}],
                "attempts": 0,
                "valid": False,
                "error": None,
            })

    return generated


import yaml


def generate_dbt_test_yaml(approved_rules: list[dict]) -> str:
    """Biên dịch danh sách approved rules thành định dạng tệp dbt test YAML chuẩn (generated_dq_tests.yml)."""
    tables_map: dict[str, dict[str, list[dict]]] = {}
    for r in approved_rules:
        t_name = r.get("table_name") or "profile_input"
        c_name = r.get("column") or "source_row_id"
        tables_map.setdefault(t_name, {}).setdefault(c_name, []).append(r)

    models_list = []
    for t_name, cols in tables_map.items():
        columns_list = []
        for c_name, rules in cols.items():
            tests_list = []
            for rule in rules:
                r_type = (rule.get("rule_type") or "").upper()
                params = rule.get("effective_parameters") or rule.get("parameters") or {}
                if r_type == "NOT_NULL":
                    tests_list.append("not_null")
                elif r_type == "UNIQUE":
                    tests_list.append("unique")
                elif r_type == "ACCEPTED_VALUES":
                    vals = params.get("accepted_values") or []
                    tests_list.append({"accepted_values": {"values": vals}})
                elif r_type == "RANGE":
                    conds = []
                    if params.get("min") is not None:
                        conds.append(f">= {params['min']}")
                    if params.get("max") is not None:
                        conds.append(f"<= {params['max']}")
                    expr = " AND ".join(conds) if conds else ">= 0"
                    tests_list.append({"dbt_utils.expression_is_true": {"expression": expr}})
                elif r_type == "CROSS_FIELD_COMPARISON":
                    target_col = params.get("target_column") or ""
                    op = params.get("operator") or "<="
                    expr = f"{op} {target_col}"
                    tests_list.append({"dbt_utils.expression_is_true": {"expression": expr}})
                else:
                    tests_list.append("not_null")
            columns_list.append({"name": c_name, "tests": tests_list})
        models_list.append({"name": t_name, "columns": columns_list})

    yaml_dict = {"version": 2, "models": models_list}
    return yaml.dump(yaml_dict, sort_keys=False, allow_unicode=True)


async def test_generator_node(state: AgentState) -> dict:
    """LangGraph Node: Sinh tệp dbt test YML từ approved rules, lưu DB, lưu vết local và chuẩn bị cho runner."""
    from src.services.rule_store import get_active_rules, save_generated_dbt_yaml

    run_id = state.get("rule_run_id") or state.get("test_run_id") or "test_run"
    approved_rules = state.get("approved_rules")

    # 1. Nếu chưa có trong state, load từ DB
    if approved_rules is None:
        if state.get("rule_run_id"):
            approved_rules = get_approved_rules(state["rule_run_id"])
        if not approved_rules:
            # Fallback: Đọc từ bảng Active Rules chính thức
            approved_rules = get_active_rules(state.get("dataset_id"))

    if not approved_rules:
        logger.warning("Không có approved/active rules nào để sinh test cho run_id=%s", run_id)
        return {
            "generated_tests": [],
            "approved_rules": [],
            "generated_dbt_yaml": "",
            "test_generation_errors": ["Không tìm thấy approved hoặc active rules nào."],
        }

    engine = get_engine()
    dialect_name = engine.dialect.name

    # Gom rules theo bảng
    by_table: dict[str, list[dict]] = {}
    for r in approved_rules:
        t = r.get("table_name", "")
        by_table.setdefault(t, []).append(r)

    all_generated: list[dict] = []
    for table_name, table_rules in by_table.items():
        tests = generate_tests_for_table(table_name, table_rules, dialect_name)
        all_generated.extend(tests)

    # 2. Sinh tệp dbt test YAML
    dbt_yaml_content = generate_dbt_test_yaml(approved_rules)

    # 3. Ghi tệp dbt test YML vào thư mục models của dbt_project
    root_dir = Path(__file__).resolve().parent.parent.parent.parent
    dbt_models_dir = root_dir / "dbt_project" / "models"
    dbt_models_dir.mkdir(parents=True, exist_ok=True)
    dbt_yml_file = dbt_models_dir / "generated_dq_tests.yml"
    try:
        dbt_yml_file.write_text(dbt_yaml_content, encoding="utf-8")
        logger.info("Đã tạo tệp dbt test YML tại: %s", dbt_yml_file)
    except Exception as exc:
        logger.warning("Không thể ghi tệp dbt_project/models/generated_dq_tests.yml: %s", exc)

    # 4. Xuất local trace file (output/test_generator/)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        settings = get_settings()
        base_dir = getattr(settings, "output_dir", None) or "./output"
        out_dir = Path(base_dir) / "test_generator"
        out_dir.mkdir(parents=True, exist_ok=True)
        dump_file = out_dir / f"debug_generated_dbt_tests_{timestamp}_{run_id}.yml"
        dump_file.write_text(dbt_yaml_content, encoding="utf-8")
        logger.info("Đã xuất trace generated dbt YML tests ra: %s", dump_file)
    except Exception as exc:
        logger.warning("Không thể ghi file trace generated dbt tests: %s", exc)

    # 5. Lưu tệp YML vào Database (Audit Log & Metadata)
    save_generated_dbt_yaml(run_id, dbt_yaml_content)

    return {
        "approved_rules": approved_rules,
        "generated_tests": all_generated,
        "generated_dbt_yaml": dbt_yaml_content,
        "dbt_yaml_file_path": str(dbt_yml_file),
        "test_generation_errors": [],
    }



# ---------------------------------------------------------------------------
# Standalone Test Harness (Chạy từ Database hoặc file output)
# ---------------------------------------------------------------------------

async def main():
    """Hàm chạy test độc lập cho test_generator_node từ database hoặc file output.

    Run: python -m src.agents.nodes.test_generator_node
    """
    import glob

    from sqlalchemy import text

    from src.services.rule_store import get_active_rules, get_engine, init_db

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    print("\n" + "=" * 75)
    print("🚀 CHẠY THỬ ĐỘC LẬP: test_generator_node (Sinh SQL từ Approved/Active Rules)")
    print("=" * 75)

    init_db()
    dataset_id = "yellow_tripdata"

    # 1. Ưu tiên 1: Đọc trực tiếp từ bảng active_rules trong Database
    db_rules = get_active_rules(dataset_id=dataset_id)
    source_desc = ""

    if db_rules:
        rules = db_rules
        source_desc = f"bảng active_rules trong Database ({len(rules)} rules đang ACTIVE)"
    else:
        # 2. Ưu tiên 2: Đọc các rule APPROVED từ bảng proposed_rules
        engine = get_engine()
        with engine.connect() as conn:
            query = text("""
                SELECT run_id, rule_id, dataset_id, table_name, column_name, rule_type, parameters, edited_parameters, severity, dimension, rule_description, status
                FROM proposed_rules
                WHERE status IN ('APPROVED', 'MERGED')
                ORDER BY created_at DESC;
            """)
            rows = conn.execute(query).fetchall()

        if rows:
            rules = []
            for r in rows:
                params_raw = r[7] if r[7] else r[6]
                params_dict = json.loads(params_raw) if params_raw else {}
                rules.append({
                    "rule_id": r[1],
                    "dataset_id": r[2],
                    "table_name": r[3],
                    "column": r[4],
                    "rule_type": r[5],
                    "parameters": params_dict,
                    "severity": r[8],
                    "dimension": r[9],
                    "rule_description": r[10],
                    "status": r[11],
                })
            source_desc = f"bảng proposed_rules trong Database ({len(rules)} rules đã APPROVED)"
        else:
            # 3. Fallback: Đọc từ file output local nếu DB chưa có rule nào được duyệt
            search_patterns = [
                "output/hitl/proposed_rules_*.json",
                "output/rule_proposer/debug_proposed_rules_*.json",
            ]
            files = []
            for pat in search_patterns:
                files.extend(glob.glob(pat))

            if not files:
                print("❌ Không tìm thấy approved rules trong DB và không có file trong output/.")
                print("💡 Hãy chạy rule_proposer_node trước: python -m src.agents.nodes.rule_proposer_node")
                return

            latest_file = sorted(files, key=os.path.getmtime)[-1]
            with open(latest_file, encoding="utf-8") as f:
                raw_data = json.load(f)

            rules = raw_data.get("proposed_rules", []) if isinstance(raw_data, dict) else raw_data
            source_desc = f"file local {latest_file} (tạm thời coi là Approved để test)"

    # Lọc bỏ bảng hệ thống
    system_tables = {"proposal_runs", "proposed_rules", "active_rules", "test_runs", "test_results"}
    filtered_rules = [
        r for r in rules if r.get("table_name", "").lower() not in system_tables
    ]

    print(f"📖 Nguồn nạp rules: {source_desc}")
    print(f"🎯 Tổng số rules nạp: {len(filtered_rules)} (đã lọc bảng hệ thống)")

    state: AgentState = {
        "dataset_id": dataset_id,
        "rule_run_id": "test_generator_standalone",
        "approved_rules": filtered_rules,
    }

    res = await test_generator_node(state)

    generated = res.get("generated_tests", [])
    print(f"\n📊 Tổng số SQL Test Queries đã sinh: {len(generated)}")
    for idx, t in enumerate(generated[:5], 1):
        print(f"\n[{idx}] Test ID     : {t['test_id']} (Type: {t['query_type']})")
        print(f"    Bảng        : {t['table_name']}")
        print(f"    SQL Query   : {t['sql_text']}")
        print(f"    Bind Params : {json.dumps(t.get('bind_params', {}), ensure_ascii=False)}")
        print(f"    Số rules gộp: {len(t.get('rules_meta', []))}")

    if len(generated) > 5:
        print(f"\n... và còn {len(generated) - 5} queries khác được lưu trong file trace.")

    print("\n" + "=" * 75 + "\n")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())





