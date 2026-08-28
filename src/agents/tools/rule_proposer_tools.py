"""Tools for the Graph 1 Rule Proposer DeepAgent.

These tools provide the agent with empirical data-investigation capabilities:
1. Querying previously approved rules from PostgreSQL.
2. Dry-running candidate rules on live/sampled data to evaluate pass/violation rates.
3. Safe sampling and inspection of anomalous data slices.
4. Deep statistical distribution checks on specific columns.
5. Inspecting semantic contract and dictionary definitions.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.tools import tool
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.models.database import (
    ColumnProfileModel,
    DatasetModel,
    RuleProposalModel,
    SemanticContractModel,
)
from src.services.rule_store import ActiveRuleModel, get_engine

logger = logging.getLogger(__name__)


def _sanitize_identifier(name: str | None, default: str = "") -> str:
    """Ensure identifier contains only safe alphanumeric and underscore characters."""
    if not name:
        return default
    cleaned = str(name).strip()
    if not re.match(r"^[a-zA-Z0-9_]+$", cleaned):
        raise ValueError(f"Invalid or unsafe SQL identifier: {name}")
    return cleaned


@tool
def query_historical_approved_rules(
    table_name: str,
    column_name: str = "",
    rule_type: str = "",
    limit: int = 5,
) -> dict[str, Any]:
    """Tra cứu các quy tắc kiểm thử chất lượng dữ liệu (Data Quality Rules) đã được Data Steward phê duyệt từ PostgreSQL.
    
    Sử dụng tool này để tham khảo các tiêu chuẩn, dải ngưỡng (parameters), mức độ nghiêm trọng (severity),
    và giải thích nghiệp vụ (business rationale) đã được chuẩn hóa trong hệ thống cho bảng hoặc cột tương tự.
    """
    limit = max(1, min(int(limit), 20))
    results: list[dict[str, Any]] = []

    try:
        with Session(get_engine()) as db:
            # 1. Query from ActiveRuleModel (published & active rules)
            active_q = db.query(ActiveRuleModel).filter(ActiveRuleModel.status == "ACTIVE")
            if table_name:
                active_q = active_q.filter(
                    ActiveRuleModel.table_name == table_name
                )
            if column_name:
                active_q = active_q.filter(
                    (ActiveRuleModel.column_name == column_name)
                    | (ActiveRuleModel.column_name.ilike(f"%{column_name}%"))
                )
            if rule_type:
                active_q = active_q.filter(ActiveRuleModel.rule_type.ilike(f"%{rule_type}%"))

            active_rows = active_q.limit(limit).all()
            for r in active_rows:
                params = {}
                try:
                    params = json.loads(r.parameters) if isinstance(r.parameters, str) else (r.parameters or {})
                except Exception:
                    pass
                results.append(
                    {
                        "source": "active_rules",
                        "rule_id": r.rule_id,
                        "table_name": r.table_name,
                        "column_name": r.column_name,
                        "rule_type": r.rule_type,
                        "parameters": params,
                        "severity": r.severity,
                        "dimension": r.dimension,
                        "rule_description": r.rule_description,
                    }
                )

            # 2. Query from RuleProposalModel (APPROVED status) if still need more examples
            remaining = limit - len(results)
            if remaining > 0:
                prop_q = db.query(RuleProposalModel).filter(RuleProposalModel.status == "APPROVED")
                if column_name:
                    prop_q = prop_q.filter(RuleProposalModel.rule_spec.ilike(f"%{column_name}%"))
                if rule_type:
                    prop_q = prop_q.filter(RuleProposalModel.rule_type.ilike(f"%{rule_type}%"))

                prop_rows = prop_q.limit(remaining).all()
                for p in prop_rows:
                    spec = {}
                    try:
                        spec = json.loads(p.rule_spec) if isinstance(p.rule_spec, str) else (p.rule_spec or {})
                    except Exception:
                        pass
                    spec_table = spec.get("table") or spec.get("table_name")
                    if table_name and spec_table and spec_table != table_name:
                        continue
                    results.append(
                        {
                            "source": "approved_proposals",
                            "proposal_id": p.id,
                            "rule_name": p.rule_name or p.title,
                            "rule_type": p.rule_type,
                            "column_name": spec.get("column"),
                            "parameters": spec.get("parameters", {}),
                            "severity": p.severity,
                            "business_rationale": p.business_rationale,
                            "description": p.description,
                        }
                    )

        return {
            "query": {"table_name": table_name, "column_name": column_name, "rule_type": rule_type},
            "count": len(results),
            "approved_rules": results,
        }
    except Exception as exc:
        logger.warning(f"Lỗi khi tra cứu lịch sử rule trong DB: {exc}")
        return {"query": {"table_name": table_name}, "count": 0, "approved_rules": [], "error": str(exc)}


@tool
def dry_run_rule_candidate(
    table_name: str,
    column_name: str = "",
    rule_type: str = "",
    parameters: dict[str, Any] | None = None,
    dataset_id: str = "",
    sample_limit: int = 1000,
) -> dict[str, Any]:
    """Chạy thử nghiệm (Dry-Run) một rule dự kiến trên dữ liệu thực tế để kiểm tra tỉ lệ vi phạm (violation rate).
    
    Tool này giúp Agent kiểm tra xem ngưỡng đề xuất (ví dụ min/max của RANGE, accepted_values, regex)
    có quá chặt (gây fail nhiều dòng hợp lệ) hoặc quá lỏng không, trước khi đưa ra quyết định cuối cùng.
    
    Args:
        table_name: Tên bảng cần kiểm tra.
        column_name: Tên cột kiểm tra (để trống nếu là rule cấp bảng ROW_COUNT).
        rule_type: Loại rule (NOT_NULL, UNIQUE, RANGE, ACCEPTED_VALUES, REGEX_FORMAT, CROSS_FIELD_COMPARISON, NULL_RATE).
        parameters: Tham số của rule (ví dụ: {"min": 0, "max": 100}, {"accepted_values": ["A", "B"]}, v.v.).
        dataset_id: ID dataset nếu có (dùng lọc trong source_rows).
        sample_limit: Số dòng tối đa kiểm tra (mặc định 1000 dòng).
    """
    params = parameters or {}
    sample_limit = max(10, min(int(sample_limit), 5000))
    norm_rule_type = rule_type.strip().upper()

    try:
        engine = get_engine()
        with engine.connect() as conn:
            # Check if source_rows table exists and has rows for this dataset
            use_source_rows = False
            try:
                check_q = text("SELECT COUNT(*) FROM source_rows" + (" WHERE dataset_id = :ds_id" if dataset_id else ""))
                cnt = conn.execute(check_q, {"ds_id": dataset_id} if dataset_id else {}).scalar() or 0
                if cnt > 0:
                    use_source_rows = True
            except Exception:
                use_source_rows = False

            target_table = "source_rows" if use_source_rows else _sanitize_identifier(table_name)
            where_base = "WHERE dataset_id = :dataset_id" if (use_source_rows and dataset_id) else "WHERE 1=1"
            query_params = {"dataset_id": dataset_id} if (use_source_rows and dataset_id) else {}

            # Execute dry run based on rule type
            col_safe = _sanitize_identifier(column_name) if column_name else ""

            if norm_rule_type in ("NOT_NULL", "UNIQUE", "RANGE", "ACCEPTED_VALUES", "REGEX_FORMAT", "NULL_RATE", "CROSS_FIELD_COMPARISON") and not col_safe:
                return {
                    "rule_type": norm_rule_type,
                    "table_name": table_name,
                    "error": f"Rule {norm_rule_type} requires a valid 'column_name'.",
                }

            if norm_rule_type == "NOT_NULL":
                sql = f"""
                SELECT 
                    COUNT(*) AS total_checked,
                    SUM(CASE WHEN "{col_safe}" IS NULL THEN 1 ELSE 0 END) AS failed_count
                FROM (SELECT * FROM "{target_table}" {where_base} LIMIT {sample_limit}) t
                """
                res = conn.execute(text(sql), query_params).mappings().first()
                sample_violations_sql = f'SELECT "{col_safe}" FROM "{target_table}" {where_base} AND "{col_safe}" IS NULL LIMIT 3'
                viol_rows = conn.execute(text(sample_violations_sql), query_params).mappings().all()

            elif norm_rule_type == "UNIQUE":
                sql = f"""
                SELECT 
                    COUNT(*) AS total_checked,
                    COUNT(DISTINCT "{col_safe}") AS distinct_count
                FROM (SELECT * FROM "{target_table}" {where_base} LIMIT {sample_limit}) t
                """
                res = conn.execute(text(sql), query_params).mappings().first()
                total = int(res["total_checked"] or 0)
                distinct = int(res["distinct_count"] or 0)
                failed = max(0, total - distinct)
                sample_violations_sql = f"""
                SELECT "{col_safe}", COUNT(*) as cnt 
                FROM "{target_table}" {where_base} AND "{col_safe}" IS NOT NULL 
                GROUP BY "{col_safe}" HAVING COUNT(*) > 1 LIMIT 3
                """
                viol_rows = conn.execute(text(sample_violations_sql), query_params).mappings().all()
                res = {"total_checked": total, "failed_count": failed}

            elif norm_rule_type == "RANGE":
                min_v = params.get("min")
                max_v = params.get("max")
                if min_v is None and max_v is None:
                    min_max_sql = f'SELECT MIN("{col_safe}"), MAX("{col_safe}") FROM "{target_table}" {where_base}'
                    min_max_row = conn.execute(text(min_max_sql), query_params).first()
                    obs_min = float(min_max_row[0]) if min_max_row and min_max_row[0] is not None else 0.0
                    obs_max = float(min_max_row[1]) if min_max_row and min_max_row[1] is not None else 0.0
                    return {
                        "rule_type": norm_rule_type,
                        "column": column_name,
                        "observed_min": obs_min,
                        "observed_max": obs_max,
                        "message": f"Chưa chỉ định min/max. Giá trị thực tế trên DB: min={obs_min}, max={obs_max}",
                        "suggestion": f"Hãy sử dụng parameters={{'min': {obs_min}, 'max': {obs_max}}}",
                        "total_checked": sample_limit,
                        "failed_count": 0,
                        "pass_rate_pct": 100.0,
                        "assessment": "PASS",
                    }
                conds = []
                if min_v is not None:
                    conds.append(f'"{col_safe}" < {float(min_v)}')
                if max_v is not None:
                    conds.append(f'"{col_safe}" > {float(max_v)}')

                fail_expr = " OR ".join(conds) if conds else "1=0"
                sql = f"""
                SELECT 
                    COUNT(*) AS total_checked,
                    SUM(CASE WHEN "{col_safe}" IS NOT NULL AND ({fail_expr}) THEN 1 ELSE 0 END) AS failed_count
                FROM (SELECT * FROM "{target_table}" {where_base} LIMIT {sample_limit}) t
                """
                res = conn.execute(text(sql), query_params).mappings().first()
                sample_violations_sql = f'SELECT "{col_safe}" FROM "{target_table}" {where_base} AND "{col_safe}" IS NOT NULL AND ({fail_expr}) LIMIT 3'
                viol_rows = conn.execute(text(sample_violations_sql), query_params).mappings().all()

            elif norm_rule_type == "ACCEPTED_VALUES":
                allowed = params.get("accepted_values") or []
                if not allowed:
                    dist_sql = f'SELECT DISTINCT "{col_safe}" FROM "{target_table}" {where_base} AND "{col_safe}" IS NOT NULL LIMIT 10'
                    dist_vals = [str(r[0]) for r in conn.execute(text(dist_sql), query_params).fetchall()]
                    return {
                        "rule_type": norm_rule_type,
                        "column": column_name,
                        "observed_distinct_values": dist_vals,
                        "message": f"Chưa chỉ định accepted_values. Giá trị thực tế trên DB: {dist_vals}",
                        "suggestion": f"Hãy sử dụng parameters={{'accepted_values': {dist_vals}}}",
                        "total_checked": sample_limit,
                        "failed_count": 0,
                        "pass_rate_pct": 100.0,
                        "assessment": "PASS",
                    }

                # Format string literals safely
                escaped_allowed = ", ".join(["'" + str(v).replace("'", "''") + "'" for v in allowed])
                sql = f"""
                SELECT 
                    COUNT(*) AS total_checked,
                    SUM(CASE WHEN "{col_safe}" IS NOT NULL AND CAST("{col_safe}" AS VARCHAR) NOT IN ({escaped_allowed}) THEN 1 ELSE 0 END) AS failed_count
                FROM (SELECT * FROM "{target_table}" {where_base} LIMIT {sample_limit}) t
                """
                res = conn.execute(text(sql), query_params).mappings().first()
                sample_violations_sql = f'SELECT "{col_safe}" FROM "{target_table}" {where_base} AND "{col_safe}" IS NOT NULL AND CAST("{col_safe}" AS VARCHAR) NOT IN ({escaped_allowed}) LIMIT 3'
                viol_rows = conn.execute(text(sample_violations_sql), query_params).mappings().all()

            elif norm_rule_type == "CROSS_FIELD_COMPARISON":
                raw_target_col = params.get("target_column") or params.get("target_col") or ""
                if not raw_target_col:
                    return {
                        "rule_type": norm_rule_type,
                        "column": column_name,
                        "error": "CROSS_FIELD_COMPARISON requires 'target_column' in parameters (e.g. {'target_column': 'dropoff_at', 'operator': '<='})",
                        "total_checked": 0,
                        "failed_count": 0,
                        "pass_rate_pct": 100.0,
                        "assessment": "PASS",
                    }
                target_col = _sanitize_identifier(raw_target_col)
                operator = params.get("operator") or "<="
                if operator not in ("<=", "<", ">=", ">", "==", "=", "!="):
                    return {
                        "rule_type": norm_rule_type,
                        "column": column_name,
                        "error": f"Invalid comparison operator: {operator}",
                        "total_checked": 0,
                        "failed_count": 0,
                        "pass_rate_pct": 100.0,
                        "assessment": "PASS",
                    }
                sql_op = "=" if operator == "==" else operator

                sql = f"""
                SELECT 
                    COUNT(*) AS total_checked,
                    SUM(CASE WHEN "{col_safe}" IS NOT NULL AND "{target_col}" IS NOT NULL AND NOT ("{col_safe}" {sql_op} "{target_col}") THEN 1 ELSE 0 END) AS failed_count
                FROM (SELECT * FROM "{target_table}" {where_base} LIMIT {sample_limit}) t
                """
                res = conn.execute(text(sql), query_params).mappings().first()
                sample_violations_sql = f'SELECT "{col_safe}", "{target_col}" FROM "{target_table}" {where_base} AND "{col_safe}" IS NOT NULL AND "{target_col}" IS NOT NULL AND NOT ("{col_safe}" {sql_op} "{target_col}") LIMIT 3'
                viol_rows = conn.execute(text(sample_violations_sql), query_params).mappings().all()

            elif norm_rule_type == "NULL_RATE":
                max_null_pct = float(params.get("max_null_pct", 5.0))
                sql = f"""
                SELECT 
                    COUNT(*) AS total_checked,
                    SUM(CASE WHEN "{col_safe}" IS NULL THEN 1 ELSE 0 END) AS null_count
                FROM (SELECT * FROM "{target_table}" {where_base} LIMIT {sample_limit}) t
                """
                res = conn.execute(text(sql), query_params).mappings().first()
                total = int(res["total_checked"] or 0)
                null_cnt = int(res["null_count"] or 0)
                actual_null_pct = round((null_cnt / total) * 100, 2) if total > 0 else 0.0
                is_violated = actual_null_pct > max_null_pct
                return {
                    "rule_type": norm_rule_type,
                    "column": column_name,
                    "total_checked": total,
                    "null_count": null_cnt,
                    "actual_null_pct": actual_null_pct,
                    "max_null_pct_threshold": max_null_pct,
                    "assessment": "VIOLATED" if is_violated else "PASS",
                }

            else:
                return {
                    "rule_type": norm_rule_type,
                    "column": column_name,
                    "message": f"Rule type {norm_rule_type} không yêu cầu dry-run SQL trực tiếp.",
                    "assessment": "SKIPPED_SQL_DRYRUN",
                }

            total_checked = int(res["total_checked"] or 0)
            failed_count = int(res["failed_count"] or 0)
            passed_count = total_checked - failed_count
            violation_rate = round((failed_count / total_checked) * 100, 3) if total_checked > 0 else 0.0

            if violation_rate == 0.0:
                assessment = "PERFECT_FIT (100% pass)"
            elif violation_rate <= 0.5:
                assessment = f"EXCELLENT_FIT ({violation_rate}% edge cases / outliers)"
            elif violation_rate <= 5.0:
                assessment = f"MODERATE_VIOLATION ({violation_rate}% fail - review if threshold is too tight)"
            else:
                assessment = f"HIGH_VIOLATION ({violation_rate}% fail - THRESHOLD LIKELY INVALID)"

            return {
                "rule_type": norm_rule_type,
                "column": column_name,
                "parameters": params,
                "total_checked": total_checked,
                "passed_count": passed_count,
                "failed_count": failed_count,
                "violation_rate_pct": violation_rate,
                "sample_violations": [dict(r) for r in viol_rows],
                "assessment": assessment,
            }

    except Exception as exc:
        logger.warning(f"Lỗi khi thực thi dry_run_rule_candidate: {exc}")
        return {
            "rule_type": norm_rule_type,
            "column": column_name,
            "error": str(exc),
            "assessment": "EXECUTION_ERROR",
        }


@tool
def inspect_data_samples(
    table_name: str,
    columns: list[str] | None = None,
    filter_condition: str = "",
    dataset_id: str = "",
    limit: int = 10,
) -> dict[str, Any]:
    """Truy vấn mẫu dữ liệu an toàn (Read-Only) để khảo sát các trường hợp bất thường (ví dụ: tiền âm, null, hoặc quan hệ liên cột).
    
    Tool này cho phép Agent xem các dòng dữ liệu thực tế thỏa mãn một điều kiện cụ thể.
    
    Args:
        table_name: Tên bảng dữ liệu.
        columns: Danh sách cột cần lấy (mặc định lấy tất cả hoặc tối đa 10 cột).
        filter_condition: Biểu thức lọc SQL đơn giản (ví dụ: "fare_amount < 0" hoặc "passenger_count = 0"). CẤM các lệnh DDL/DML.
        dataset_id: ID dataset nếu có.
        limit: Số lượng dòng tối đa cần xem (mặc định 10, tối đa 50).
    """
    limit = max(1, min(int(limit), 50))

    # Strict SQL injection & safety guardrails
    if filter_condition:
        forbidden = [
            ";", "--", "/*", "*/", "drop", "delete", "insert", "update", "alter",
            "truncate", "create", "grant", "exec", "execute", "union", "select",
            "pragma", "attach", "detach"
        ]
        lowered = filter_condition.lower()
        if any(re.search(rf"\b{kw}\b", lowered) if len(kw) > 2 else kw in lowered for kw in forbidden):
            return {"error": "Invalid filter_condition: Unsafe SQL keyword or symbol detected."}

    try:
        engine = get_engine()
        with engine.connect() as conn:
            use_source_rows = False
            try:
                check_q = text("SELECT COUNT(*) FROM source_rows" + (" WHERE dataset_id = :ds_id" if dataset_id else ""))
                cnt = conn.execute(check_q, {"ds_id": dataset_id} if dataset_id else {}).scalar() or 0
                if cnt > 0:
                    use_source_rows = True
            except Exception:
                use_source_rows = False

            target_table = "source_rows" if use_source_rows else _sanitize_identifier(table_name)
            where_clauses = []
            query_params = {}
            if use_source_rows and dataset_id:
                where_clauses.append("dataset_id = :dataset_id")
                query_params["dataset_id"] = dataset_id
            if filter_condition.strip():
                where_clauses.append(f"({filter_condition})")

            where_str = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

            if columns:
                safe_cols = ", ".join([f'"{_sanitize_identifier(c)}"' for c in columns])
            else:
                safe_cols = "*"

            query = text(f'SELECT {safe_cols} FROM "{target_table}" {where_str} LIMIT {limit}')
            rows = conn.execute(query, query_params).mappings().all()

            # Clean and serialize row values
            formatted_rows = []
            for r in rows:
                row_dict = {}
                for k, v in dict(r).items():
                    if k in {"source_row_id", "dataset_id"}:
                        continue
                    row_dict[k] = v.isoformat() if hasattr(v, "isoformat") else v
                formatted_rows.append(row_dict)

            return {
                "table_name": table_name,
                "row_count_returned": len(formatted_rows),
                "rows": formatted_rows,
            }

    except Exception as exc:
        return {"table_name": table_name, "error": str(exc), "rows": []}


@tool
def get_column_deep_stats(
    table_name: str,
    column_name: str,
    dataset_id: str = "",
) -> dict[str, Any]:
    """Lấy số liệu phân phối thống kê chuyên sâu của một cột (quantiles p1..p99, tỷ lệ null, top categories, min/max, độ dài chuỗi).
    
    Tool này cung cấp căn cứ số học chính xác để Agent tính toán dải ngưỡng RANGE hoặc danh sách ACCEPTED_VALUES.
    """
    col_name = column_name.strip()
    try:
        with Session(get_engine()) as db:
            # Look for existing persisted ColumnProfile
            query = db.query(ColumnProfileModel).filter(ColumnProfileModel.name == col_name)
            if dataset_id:
                query = query.filter(ColumnProfileModel.profile_dataset_id == dataset_id)

            profiles = query.limit(2).all()
            if len(profiles) > 1:
                return {"table_name": table_name, "column_name": col_name, "error": "AMBIGUOUS_COLUMN_PROFILE"}
            col_prof = profiles[0] if profiles else None
            if col_prof:
                quantiles = {}
                try:
                    quantiles = json.loads(col_prof.quantiles_json or "{}")
                except Exception:
                    pass

                return {
                    "table_name": table_name,
                    "column_name": col_name,
                    "data_type": col_prof.data_type,
                    "null_rate_pct": round(col_prof.null_rate * 100, 2),
                    "distinct_count": col_prof.distinct_count,
                    "full_distinct_count": col_prof.full_distinct_count,
                    "is_unique_full_table": col_prof.is_unique_full_table,
                    "min_value": col_prof.min_value,
                    "max_value": col_prof.max_value,
                    "negative_rate_pct": round((col_prof.negative_rate or 0) * 100, 2),
                    "quantiles": quantiles,
                    "sample_value": col_prof.sample_value,
                }

        # Fallback to direct calculation if not in column_profiles
        engine = get_engine()
        with engine.connect() as conn:
            col_safe = _sanitize_identifier(col_name)
            target = "source_rows"
            where_ds = "WHERE dataset_id = :dataset_id" if dataset_id else ""

            stats_q = text(f"""
            SELECT 
                COUNT(*) as total_rows,
                COUNT("{col_safe}") as non_null_count,
                COUNT(DISTINCT "{col_safe}") as distinct_count,
                MIN("{col_safe}") as min_val,
                MAX("{col_safe}") as max_val
            FROM "{target}" {where_ds}
            """)
            res = conn.execute(stats_q, {"dataset_id": dataset_id} if dataset_id else {}).mappings().first()
            if res:
                total = int(res["total_rows"] or 0)
                non_null = int(res["non_null_count"] or 0)
                null_rate = round(((total - non_null) / total) * 100, 2) if total > 0 else 0.0
                return {
                    "table_name": table_name,
                    "column_name": col_name,
                    "total_rows": total,
                    "null_rate_pct": null_rate,
                    "distinct_count": int(res["distinct_count"] or 0),
                    "min_value": res["min_val"],
                    "max_value": res["max_val"],
                }

        return {"table_name": table_name, "column_name": col_name, "error": "COLUMN_NOT_FOUND"}

    except Exception as exc:
        return {"table_name": table_name, "column_name": col_name, "error": str(exc)}


@tool
def inspect_semantic_metadata(
    table_name: str,
    column_name: str = "",
) -> dict[str, Any]:
    """Tra cứu hợp đồng ngữ nghĩa (Semantic Contract) và định nghĩa Từ điển dữ liệu của bảng/cột."""
    try:
        with Session(get_engine()) as db:
            contract_row = (
                db.query(SemanticContractModel)
                .order_by(SemanticContractModel.created_at.desc())
                .first()
            )
            if not contract_row:
                return {"table_name": table_name, "status": "NO_CONTRACT_FOUND"}

            contract_payload = {}
            try:
                contract_payload = json.loads(contract_row.payload_json or "{}")
            except Exception:
                pass

            tables = contract_payload.get("tables", {})
            table_meta = tables.get(table_name) or {}

            if column_name:
                cols = table_meta.get("columns", [])
                matched_col = next((c for c in cols if isinstance(c, dict) and c.get("name") == column_name), None)
                return {
                    "table_name": table_name,
                    "column_name": column_name,
                    "column_contract": matched_col or "NOT_SPECIFIED_IN_CONTRACT",
                }

            return {
                "table_name": table_name,
                "table_contract": table_meta,
                "relationships": contract_payload.get("relationships", []),
            }
    except Exception as exc:
        return {"table_name": table_name, "error": str(exc)}


RULE_PROPOSER_TOOLS = [
    query_historical_approved_rules,
    dry_run_rule_candidate,
    inspect_data_samples,
    get_column_deep_stats,
    inspect_semantic_metadata,
]


def _safe_input(prompt_text: str, default_val: str = "") -> str:
    """Safe input helper that falls back to default on EOF/empty."""
    try:
        val = input(prompt_text).strip().strip("\ufeff")
        return val if val else default_val
    except (EOFError, KeyboardInterrupt):
        print()
        return default_val


if __name__ == "__main__":
    """Manual smoke test: run with ``python -m src.agents.tools.rule_proposer_tools``.

    The prompts make it possible to test against the configured database without
    hard-coding IDs into source control. Press Enter to use defaults or skip.
    """
    from pprint import pprint

    print("=" * 70)
    print("RULE PROPOSER TOOLS - SMOKE TEST")
    print("=" * 70)
    print("Available tools:")
    for rule_tool in RULE_PROPOSER_TOOLS:
        print(f"- {rule_tool.name}: {rule_tool.description.splitlines()[0]}")
    print("=" * 70)

    # 0. Discover default dataset, table, and columns from database
    default_dataset_id = ""
    default_table_name = "source_rows"
    default_column_name = "fare_amount"
    sample_columns: list[str] = []

    try:
        with Session(get_engine()) as db:
            ds = db.query(DatasetModel).first()
            if ds:
                default_dataset_id = ds.id

            cols = (
                db.query(ColumnProfileModel)
                .filter(ColumnProfileModel.profile_dataset_id == default_dataset_id)
                .limit(15)
                .all()
                if default_dataset_id
                else []
            )
            if not cols:
                cols = db.query(ColumnProfileModel).limit(15).all()
            if cols:
                sample_columns = [c.name for c in cols]
                # Pick a recognizable numeric/business column as default if available
                for candidate in ["fare_amount", "trip_distance", "passenger_count", "price"]:
                    if candidate in sample_columns:
                        default_column_name = candidate
                        break
                else:
                    default_column_name = sample_columns[0]
    except Exception as exc:
        print(f"[Warning] Could not fetch defaults from database: {exc}")

    # ---------------------------------------------------------
    # 1. Test query_historical_approved_rules
    # ---------------------------------------------------------
    print("\n--- [1/5] Testing query_historical_approved_rules ---")
    table_input = _safe_input(f"Table name [{default_table_name}] (Enter to use default/skip): ", default_table_name)
    if table_input:
        col_input = _safe_input("Column name filter (optional, Enter to skip column filter) []: ", "")
        rule_type_input = _safe_input("Rule type filter (optional, e.g. numeric_range, not_null) []: ", "")
        print(f"\n>> Invoking query_historical_approved_rules(table_name='{table_input}', column_name='{col_input}', rule_type='{rule_type_input}'):")
        approved_res = query_historical_approved_rules.invoke({
            "table_name": table_input,
            "column_name": col_input,
            "rule_type": rule_type_input,
            "limit": 5,
        })
        pprint(approved_res)

    # ---------------------------------------------------------
    # 2. Test get_column_deep_stats
    # ---------------------------------------------------------
    print("\n--- [2/5] Testing get_column_deep_stats ---")
    stat_col = _safe_input(f"Column name for deep stats [{default_column_name}] (Enter to use default/skip): ", default_column_name)
    if stat_col:
        stat_ds = _safe_input(f"Dataset ID [{default_dataset_id}] (Enter to use default): ", default_dataset_id)
        print(f"\n>> Invoking get_column_deep_stats(table_name='{default_table_name}', column_name='{stat_col}', dataset_id='{stat_ds}'):")
        stats_res = get_column_deep_stats.invoke({
            "table_name": default_table_name,
            "column_name": stat_col,
            "dataset_id": stat_ds,
        })
        pprint(stats_res)

    # ---------------------------------------------------------
    # 3. Test inspect_data_samples
    # ---------------------------------------------------------
    print("\n--- [3/5] Testing inspect_data_samples ---")
    sample_filter = _safe_input("Filter condition (e.g. 'fare_amount < 0' or 'trip_distance > 10') ['fare_amount < 0']: ", "fare_amount < 0")
    sample_ds = _safe_input(f"Dataset ID [{default_dataset_id}] (Enter to use default): ", default_dataset_id)
    print(f"\n>> Invoking inspect_data_samples(table_name='{default_table_name}', filter_condition='{sample_filter}', dataset_id='{sample_ds}', limit=3):")
    sample_res = inspect_data_samples.invoke({
        "table_name": default_table_name,
        "filter_condition": sample_filter,
        "dataset_id": sample_ds,
        "limit": 3,
    })
    pprint(sample_res)

    # ---------------------------------------------------------
    # 4. Test dry_run_rule_candidate
    # ---------------------------------------------------------
    print("\n--- [4/5] Testing dry_run_rule_candidate ---")
    dry_rule_type = _safe_input("Rule type to dry-run (RANGE, NOT_NULL, ACCEPTED_VALUES, CROSS_FIELD_COMPARISON) [RANGE]: ", "RANGE").upper()
    dry_col = _safe_input(f"Target column [{default_column_name}]: ", default_column_name)
    dry_ds = _safe_input(f"Dataset ID [{default_dataset_id}]: ", default_dataset_id)

    dry_params: dict[str, Any] = {}
    if dry_rule_type == "RANGE":
        param_str = _safe_input("Parameters JSON [{'min': 0, 'max': 500}]: ", '{"min": 0, "max": 500}')
        try:
            import ast
            dry_params = json.loads(param_str) if (param_str.startswith("{") and '"' in param_str) else ast.literal_eval(param_str)
        except Exception:
            dry_params = {"min": 0, "max": 500}
    elif dry_rule_type == "ACCEPTED_VALUES":
        dry_params = {"accepted_values": ["1", "2", "3", "4", "5", "6"]}
    elif dry_rule_type == "CROSS_FIELD_COMPARISON":
        dry_params = {"target_column": "dropoff_at", "operator": "<="}

    print(f"\n>> Invoking dry_run_rule_candidate(table_name='{default_table_name}', column_name='{dry_col}', rule_type='{dry_rule_type}', parameters={dry_params}, dataset_id='{dry_ds}'):")
    dry_res = dry_run_rule_candidate.invoke({
        "table_name": default_table_name,
        "column_name": dry_col,
        "rule_type": dry_rule_type,
        "parameters": dry_params,
        "dataset_id": dry_ds,
        "sample_limit": 500,
    })
    pprint(dry_res)

    # ---------------------------------------------------------
    # 5. Test inspect_semantic_metadata
    # ---------------------------------------------------------
    print("\n--- [5/5] Testing inspect_semantic_metadata ---")
    meta_tbl = _safe_input(f"Table name [{default_table_name}] (Enter to use default/skip): ", default_table_name)
    if meta_tbl:
        meta_col = _safe_input(f"Column name (optional) [{default_column_name}]: ", default_column_name)
        print(f"\n>> Invoking inspect_semantic_metadata(table_name='{meta_tbl}', column_name='{meta_col}'):")
        meta_res = inspect_semantic_metadata.invoke({
            "table_name": meta_tbl,
            "column_name": meta_col,
        })
        pprint(meta_res)

    print("\n" + "=" * 70)
    print("ALL TOOLS SMOKE TEST COMPLETED!")
    print("=" * 70)
