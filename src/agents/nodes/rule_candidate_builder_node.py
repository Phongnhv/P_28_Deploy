import logging

from src.agents.state import AgentState
from src.agents.tools.profile_digest import split_digest_by_table

logger = logging.getLogger(__name__)

def rule_candidate_builder_node(state: AgentState) -> dict:
    """Deterministic Rule Candidate Builder.

    Sinh các candidates (NOT_NULL, UNIQUE, RANGE, v.v.) dựa trên Semantic Contract đã duyệt và Profile Digest.
    """
    contract = state.get("semantic_contract")
    digest = state.get("dataset_profile_digest", {})
    if not contract:
        logger.warning("Không tìm thấy semantic_contract trong state của rule_candidate_builder_node.")
        return {"rule_candidates": []}

    per_table_digest = split_digest_by_table(digest)
    candidates = []

    # Duyệt qua từng bảng trong Semantic Contract
    tables_contract = contract.get("tables", {})
    for table_name, table_contract in tables_contract.items():
        table_digest = per_table_digest.get(table_name, {})
        available_columns = {col.get("name") for col in table_digest.get("columns", []) if col.get("name")}

        # 1. NOT_NULL & UNIQUE & RANGE & ACCEPTED_VALUES từ columns contract
        for col_contract in table_contract.get("columns", []):
            col_name = col_contract.get("name")
            if not col_name:
                continue

            sem_type = col_contract.get("semantic_type", "")
            nullable_expected = col_contract.get("nullable_expected", True)

            # Lấy profile info cho cột nếu có
            col_digest = next((c for c in table_digest.get("columns", []) if c.get("name") == col_name), {})
            set(col_digest.get("signals", []))

            # --- NOT_NULL ---
            if not nullable_expected or sem_type == "identifier":
                candidates.append({
                    "table": table_name,
                    "column": col_name,
                    "rule_type": "NOT_NULL",
                    "parameters": {},
                    "evidence": ["schema:semantic_contract:nullable_expected_false"],
                })

            # --- UNIQUE ---
            if sem_type == "identifier":
                candidates.append({
                    "table": table_name,
                    "column": col_name,
                    "rule_type": "UNIQUE",
                    "parameters": {},
                    "evidence": ["schema:semantic_contract:identifier_type"],
                })

            # --- RANGE ---
            if sem_type in ("currency", "numeric"):
                # Lấy range thực tế từ profile digest
                val_range = col_digest.get("range") or []
                quantiles = col_digest.get("quantiles") or col_digest.get("percentiles") or {}

                # Ưu tiên dùng typical range [p5, p95] hoặc [p05, p95]
                p5 = quantiles.get("p5") or quantiles.get("p05") or (val_range[0] if val_range else None)
                p95 = quantiles.get("p95") or (val_range[1] if val_range else None)

                if p5 is not None and p95 is not None:
                    # Mở rộng biên 10%
                    span = p95 - p5
                    suggested_min = p5 - (span * 0.1) if sem_type != "currency" else max(0.0, p5 - (span * 0.1))
                    suggested_max = p95 + (span * 0.1)

                    # Tránh số lẻ thập phân quá nhiều
                    if isinstance(suggested_min, float):
                        suggested_min = round(suggested_min, 2)
                    if isinstance(suggested_max, float):
                        suggested_max = round(suggested_max, 2)

                    candidates.append({
                        "table": table_name,
                        "column": col_name,
                        "rule_type": "RANGE",
                        "parameters": {
                            "min": suggested_min,
                            "max": suggested_max,
                        },
                        "evidence": ["profile:typical_range"],
                    })

            # --- ACCEPTED_VALUES ---
            if sem_type == "category":
                # Lấy values quan sát được
                observed_values = [v for v in col_digest.get("values", []) if v is not None]
                if observed_values:
                    candidates.append({
                        "table": table_name,
                        "column": col_name,
                        "rule_type": "ACCEPTED_VALUES",
                        "parameters": {
                            "accepted_values": observed_values,
                        },
                        "evidence": ["profile:observed_categories"],
                    })

            # --- REGEX_FORMAT ---
            if sem_type == "PII" or col_name.lower() in ("email", "phone", "zipcode"):
                # Regex mẫu
                regex_pattern = "^.+$"
                if "email" in col_name.lower():
                    regex_pattern = "^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\\.[a-zA-Z0-9-.]+$"
                elif "phone" in col_name.lower():
                    regex_pattern = "^\\+?[0-9\\s\\-\\.]{7,15}$"

                candidates.append({
                    "table": table_name,
                    "column": col_name,
                    "rule_type": "REGEX_FORMAT",
                    "parameters": {
                        "regex": regex_pattern,
                    },
                    "evidence": ["schema:semantic_type_pii"],
                })

            # --- FRESHNESS ---
            if sem_type == "timestamp" and any(k in col_name.lower() for k in ("update", "create", "modified", "time", "date")):
                candidates.append({
                    "table": table_name,
                    "column": col_name,
                    "rule_type": "FRESHNESS",
                    "parameters": {
                        "max_age_hours": 24.0,
                    },
                    "evidence": ["schema:timestamp_column"],
                })

            # --- NULL_RATE ---
            null_pct = col_digest.get("null_pct", 0.0) or 0.0
            if null_pct > 5.0:
                candidates.append({
                    "table": table_name,
                    "column": col_name,
                    "rule_type": "NULL_RATE",
                    "parameters": {
                        "max_null_pct": min(100.0, null_pct + 10.0),
                    },
                    "evidence": ["profile:observed_null_rate"],
                })

        # 2. CROSS_FIELD_COMPARISON từ relationships contract
        for rel in table_contract.get("relationships", []):
            left_col = rel.get("left_column")
            right_col = rel.get("right_column")
            operator = rel.get("operator")
            if left_col in available_columns and right_col in available_columns:
                candidates.append({
                    "table": table_name,
                    "column": left_col,
                    "rule_type": "CROSS_FIELD_COMPARISON",
                    "parameters": {
                        "target_column": right_col,
                        "operator": operator,
                    },
                    "evidence": ["schema:semantic_relationship"],
                })

        # 3. ROW_COUNT (cấp bảng)
        rows_count = table_digest.get("rows", 0)
        if rows_count > 0:
            candidates.append({
                "table": table_name,
                "column": None,
                "rule_type": "ROW_COUNT",
                "parameters": {
                    "min_row_count": int(rows_count * 0.8),
                },
                "evidence": ["profile:observed_row_count"],
            })

    # Đính kèm evidence_items (enrich evidence references)
    from src.agents.nodes.rule_proposer_node import _attach_evidence_items

    enriched_candidates = []
    for cand in candidates:
        table_name = cand["table"]
        table_digest = per_table_digest.get(table_name, {})
        enriched_cand = cand.copy()

        res = _attach_evidence_items([enriched_cand], table_digest)
        if res:
            enriched_candidates.append(res[0])

    logger.info(f"Đã tạo {len(enriched_candidates)} candidates từ Semantic Contract.")

    # Xuất trace JSON
    import json
    from datetime import datetime
    from pathlib import Path

    from src.config import get_settings

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_id = state.get("rule_run_id") or "test_run"
    try:
        settings = get_settings()
        out_dir = getattr(settings, "output_dir", None)
        res_dir = getattr(settings, "results_dir", None)
        base_dir = out_dir if isinstance(out_dir, (str, Path)) else (res_dir if isinstance(res_dir, (str, Path)) else "./output")
        candidates_dir = Path(base_dir) / "candidates"
        candidates_dir.mkdir(parents=True, exist_ok=True)
        dump_file = candidates_dir / f"debug_rule_candidates_{timestamp}_{run_id}.json"
        dump_file.write_text(json.dumps(enriched_candidates, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"Đã xuất trace rule candidates ra {dump_file}")
    except Exception as e:
        logger.warning(f"Không thể ghi file trace rule candidates: {e}")

    return {"rule_candidates": enriched_candidates}
