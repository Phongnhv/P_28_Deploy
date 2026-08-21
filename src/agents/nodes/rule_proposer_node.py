"""Rule Proposer Node — fan-out LLM structured output per table.

Luồng:
  rule_proposer_node(state):
    1. Tách digest thành dict {table: table_digest}
    2. Nếu debug flag: dump mỗi bảng ra file JSON
    3. Fan-out asyncio.gather với semaphore, mỗi table 1 LLM call
    4. Stamp rule_id, flatten thành list[dict], tách errors
    5. Trả {proposed_rules, rule_proposal_errors, rule_run_id}

  persist_rules_node(state):
    Thin node: đọc proposed_rules + rule_run_id, gọi save_proposed_rules,
    trả {metadata: {..., rules_saved: n}}
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from src.agents.nodes.templates import (
    _RULE_PROPOSER_FEW_SHOT,
    dashboard_rule_proposer_prompt,
    rule_proposer_prompt,
)
from src.agents.state import AgentState
from src.agents.tools.chroma_rag_tool import query_historical_rules
from src.agents.tools.profile_digest import (
    dump_table_digests,
    split_digest_by_table,
)
from src.config import get_settings
from src.models.rule_schemas import (
    EvidenceSourceType,
    ProposedRule,
    RuleEvidenceSnapshot,
    TableRuleProposal,
)
from src.services.llm import get_llm

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Domain context & Data dictionary injected vào mỗi lần gọi LLM
# ---------------------------------------------------------------------------

DOMAIN_CONTEXT = """\
Hệ thống quản lý dữ liệu vận tải taxi thành phố New York (NYC TLC Yellow Taxi Trip Records).
Dataset ghi nhận chi tiết các chuyến đi taxi: thời gian đón/trả khách, địa điểm đón/trả (Taxi Zone),
số lượng hành khách, khoảng cách di chuyển, các khoản cước phí, phụ phí, thuế, phí cầu đường, tiền tip và tổng tiền thanh toán.
"""

DATA_DICTIONARY_PATH = Path(__file__).resolve().parents[3] / "data" / "data_dictionary_trip_records_yellow.json"


def _build_coverage_requirements(table_digest: dict) -> list[dict]:
    """Sinh checklist rule ứng viên hoàn toàn từ evidence trong digest.

    Checklist hướng LLM phủ hết tín hiệu có căn cứ thay vì chỉ trả một nhóm rule
    đại diện. Đây không phải rule output và không tự đặt threshold thay cho LLM.
    """
    dashboard_candidates = table_digest.get("dashboard_rule_candidates")
    if table_digest.get("dashboard_candidate_mode") and isinstance(dashboard_candidates, list):
        # The public dashboard workflow has already transformed persisted aggregate
        # profile evidence into a small policy-approved candidate set.  Do not add
        # legacy heuristic candidates here: doing so lets a model spend all five
        # slots on repeated NOT_NULL rules and weakens the product contract.
        return _attach_evidence_items(
            [candidate for candidate in dashboard_candidates if isinstance(candidate, dict)],
            table_digest,
        )

    requirements: list[dict] = []
    digest_columns = table_digest.get("columns") or []
    available_columns = {
        column.get("name")
        for column in digest_columns
        if isinstance(column, dict) and column.get("name")
    }

    for column in digest_columns:
        if not isinstance(column, dict):
            continue
        name = column.get("name")
        if not name:
            continue

        role = column.get("role")
        signals = set(column.get("signals", []))
        null_pct = column.get("null_pct", 0.0) or 0.0

        if "no_nulls" in signals and (
            role in {"id", "datetime"}
            or name in {"vendor_id", "rate_code_id", "payment_type"}
        ):
            requirements.append({
                "column": name,
                "rule_type": "NOT_NULL",
                "evidence": ["no_nulls", f"role={role}"],
            })

        if signals.intersection({"has_pk_constraint", "has_unique_constraint", "unique_full_table"}):
            requirements.append({
                "column": name,
                "rule_type": "UNIQUE",
                "evidence": sorted(signals.intersection({
                    "has_pk_constraint", "has_unique_constraint", "unique_full_table"
                })),
            })

        if role == "numeric" and signals.intersection({
            "has_extreme_outliers", "has_negative_values", "has_zero_values"
        }):
            requirements.append({
                "column": name,
                "rule_type": "RANGE",
                "evidence": sorted(signals.intersection({
                    "has_extreme_outliers", "has_negative_values", "has_zero_values"
                })),
            })

        values = [value for value in column.get("values", []) if value is not None]
        if role == "categorical" and values:
            requirements.append({
                "column": name,
                "rule_type": "ACCEPTED_VALUES",
                "evidence": {"values": values},
            })

        if role == "datetime":
            requirements.append({
                "column": name,
                "rule_type": "FRESHNESS",
                "evidence": {"range": column.get("range")},
            })

        if null_pct > 5.0 and name not in {
            "congestion_surcharge", "airport_fee", "cbd_congestion_fee"
        }:
            requirements.append({
                "column": name,
                "rule_type": "NULL_RATE",
                "evidence": {"null_pct": null_pct},
            })

        is_datetime_col = any(suffix in name for suffix in ["_at", "_time", "_date", "datetime", "pickup", "dropoff"])
        if "fixed_length" in signals and not is_datetime_col:
            requirements.append({
                "column": name,
                "rule_type": "REGEX_FORMAT",
                "evidence": {"length_stats": column.get("length_stats")},
            })

    cross_field_operators = {
        "datetime_order": "<=",
    }
    for hint in table_digest.get("cross_column_hints") or []:
        if not isinstance(hint, dict):
            continue

        columns = hint.get("columns")
        if not isinstance(columns, list) or len(columns) < 2:
            continue

        source_column, target_column = columns[0], columns[1]
        if (
            not isinstance(source_column, str)
            or not isinstance(target_column, str)
            or source_column not in available_columns
            or target_column not in available_columns
        ):
            logger.warning(
                "Bỏ qua cross-column hint có cột không hợp lệ: %s",
                hint,
            )
            continue

        operator = cross_field_operators.get(hint.get("type"))
        if operator is None:
            logger.warning(
                "Bỏ qua cross-column hint chưa hỗ trợ type=%r",
                hint.get("type"),
            )
            continue

        requirements.append({
            "column": source_column,
            "rule_type": "CROSS_FIELD_COMPARISON",
            "parameters": {
                "target_column": target_column,
                "operator": operator,
            },
            "evidence": hint,
        })

    requirements.append({
        "column": None,
        "rule_type": "ROW_COUNT",
        "parameters": {
            "min_row_count": max(1, int((table_digest.get("rows") or 0) * 0.8)),
        },
        "evidence": {"rows": table_digest.get("rows", 0)},
    })
    return _attach_evidence_items(requirements, table_digest)


def _evidence_source_type(reference: str) -> str:
    if reference.startswith(("policy.", "policy:")):
        return "POLICY"
    if reference.startswith(("schema.", "schema:")):
        return "SCHEMA_CONSTRAINT"
    if reference.startswith("dictionary."):
        return "DATA_DICTIONARY"
    if reference.startswith("history."):
        return "HISTORICAL_RULE"
    return "DATA_PROFILE"


def _digest_metric_value(table_digest: dict, column: str | None, metric: str):
    if metric == "rows":
        return table_digest.get("rows")
    if column:
        column_digest = next(
            (item for item in table_digest.get("columns", []) if item.get("name") == column),
            {},
        )
        aliases = {
            "null_rate": "null_pct",
            "negative_rate": "negative_pct",
            "min_value": "range",
            "max_value": "range",
            "distinct_count": "full_distinct_count",
            "uniqueness_rate": "uniqueness_pct",
            "out_of_domain_rate": "out_of_domain_pct",
            "data_type": "type",
        }
        key = aliases.get(metric, metric)
        value = column_digest.get(key)
        if value is None and metric in {"p05", "p50", "p95", "p5", "p95"}:
            quantiles = column_digest.get("quantiles") or column_digest.get("percentiles") or {}
            value = quantiles.get(metric) or quantiles.get(metric.replace("p0", "p"))
        if metric == "min_value" and isinstance(value, list):
            return value[0]
        if metric == "max_value" and isinstance(value, list):
            return value[1]
        return value
    return None


def _attach_evidence_items(requirements: list[dict], table_digest: dict) -> list[dict]:
    """Convert legacy evidence hints into allow-listed, stable evidence references."""
    enriched: list[dict] = []
    for requirement in requirements:
        item = dict(requirement)
        column = item.get("column")
        raw_evidence = item.pop("evidence", [])
        evidence_items: list[dict] = []
        if isinstance(raw_evidence, list):
            for raw in raw_evidence:
                reference = str(raw)
                if not reference.startswith(("profile.", "policy.", "schema.", "dictionary.", "history.")):
                    prefix = "schema" if "constraint" in reference or reference == "has_pk_constraint" else "profile"
                    reference = f"{prefix}:{column or '_table'}:{reference}"
                metric = reference.rsplit(".", 1)[-1].rsplit(":", 1)[-1]
                evidence_items.append({
                    "id": reference,
                    "source_type": _evidence_source_type(reference),
                    "metric": metric,
                    "value": _digest_metric_value(table_digest, column, metric),
                })
        elif isinstance(raw_evidence, dict):
            for metric, value in raw_evidence.items():
                evidence_items.append({
                    "id": f"profile:{column or '_table'}:{metric}",
                    "source_type": "DATA_PROFILE",
                    "metric": metric,
                    "value": value,
                })
        item["evidence_items"] = evidence_items
        enriched.append(item)
    return enriched


def _find_requirement(rule: ProposedRule, requirements: list[dict]) -> dict | None:
    for requirement in requirements:
        if rule.candidate_id and requirement.get("candidate_id") == rule.candidate_id:
            return requirement
        if (
            not rule.candidate_id
            and requirement.get("column") == rule.column
            and requirement.get("rule_type") == rule.rule_type.value
        ):
            return requirement
    return None


def _load_data_dictionary() -> str:
    """Đọc data dictionary JSON từ file data_dictionary_trip_records_yellow.json."""
    target_path = DATA_DICTIONARY_PATH
    if not target_path.exists():
        target_path = Path("data/data_dictionary_trip_records_yellow.json")

    if target_path.exists():
        try:
            with open(target_path, encoding="utf-8") as f:
                data = json.load(f)
            return json.dumps(data, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.warning("Không thể đọc data dictionary từ %s: %s", target_path, exc)
    return "None"


async def _propose_for_table(
    table_name: str,
    table_digest: dict,
    structured_llm,
    semaphore: asyncio.Semaphore,
    max_retries: int,
    semantic_contract: dict | None = None,
    candidates: list[dict] | None = None,
    dataset_id: str = "unknown",
) -> TableRuleProposal:
    """Gọi LLM một lần cho một bảng, bảo vệ bằng semaphore + retry."""
    columns = [col["name"] for col in table_digest.get("columns", [])]
    dashboard_mode = bool(table_digest.get("dashboard_candidate_mode"))
    historical = [] if dashboard_mode else query_historical_rules(table_name, columns)

    async with semaphore:
        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                entry_ts = datetime.now()
                logger.info(
                    "[%s] Bắt đầu gọi LLM (attempt %d/%d) lúc %s",
                    table_name,
                    attempt + 1,
                    max_retries + 1,
                    entry_ts.isoformat(),
                )
                
                is_taxi = dataset_id.lower().startswith("nyc-yellow") or "taxi" in dataset_id.lower()
                
                if candidates is not None:
                    coverage_requirements = json.dumps(candidates, ensure_ascii=False)
                else:
                    coverage_requirements = json.dumps(
                        _build_coverage_requirements(table_digest),
                        ensure_ascii=False,
                    )

                if dashboard_mode:
                    messages = dashboard_rule_proposer_prompt.format_messages(
                        table_name=table_name,
                        table_digest=json.dumps(table_digest, ensure_ascii=False),
                        coverage_requirements=coverage_requirements,
                    )
                elif not is_taxi and semantic_contract:
                    from src.agents.nodes.templates import generic_rule_proposer_prompt
                    messages = generic_rule_proposer_prompt.format_messages(
                        table_name=table_name,
                        table_digest=json.dumps(table_digest, ensure_ascii=False),
                        semantic_contract=json.dumps(semantic_contract, ensure_ascii=False),
                        historical_rules=json.dumps(historical, ensure_ascii=False),
                        coverage_requirements=coverage_requirements,
                    )
                else:
                    messages = rule_proposer_prompt.format_messages(
                        table_name=table_name,
                        table_digest=json.dumps(table_digest, ensure_ascii=False),
                        domain_context=DOMAIN_CONTEXT,
                        data_dictionary=_load_data_dictionary(),
                        historical_rules=json.dumps(historical, ensure_ascii=False),
                        coverage_requirements=coverage_requirements,
                        few_shot_examples=_RULE_PROPOSER_FEW_SHOT,
                    )
                result: TableRuleProposal = await structured_llm.ainvoke(messages)
                exit_ts = datetime.now()
                logger.info(
                    "[%s] Hoàn thành (attempt %d) sau %.2fs — %d rules",
                    table_name,
                    attempt + 1,
                    (exit_ts - entry_ts).total_seconds(),
                    len(result.rules),
                )
                return result

            except (ValidationError, Exception) as exc:
                last_exc = exc
                if attempt < max_retries:
                    wait_seconds = 2 ** attempt  # 1s, 2s, 4s …
                    logger.warning(
                        "[%s] Lỗi attempt %d: %s — thử lại sau %ds",
                        table_name,
                        attempt + 1,
                        exc,
                        wait_seconds,
                    )
                    await asyncio.sleep(wait_seconds)
                else:
                    logger.error(
                        "[%s] Đã thử %d lần, từ bỏ. Lỗi cuối: %s",
                        table_name,
                        max_retries + 1,
                        exc,
                    )

        raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Helper: validate + stamp rule_id cho một ProposedRule
# ---------------------------------------------------------------------------

def _stamp_rule(
    rule: ProposedRule,
    table_name: str,
    run_id: str,
    used_ids: set[str] | None = None,
    requirement: dict | None = None,
    table_digest: dict | None = None,
) -> dict:
    """Chuyển ProposedRule sang dict có rule_id, table_name, run_id đính kèm.

    used_ids: set chỏa các rule_id đã dùng trong run này (dedup bằng suffix #2, #3).
    """
    col_key = rule.column if rule.column else "_table"
    if rule.rule_type.value == "CROSS_FIELD_COMPARISON":
        target_column = rule.parameters.target_column
        base_id = (
            f"{table_name}.{col_key}.VS.{target_column}.{rule.rule_type.value}"
        )
    else:
        base_id = f"{table_name}.{col_key}.{rule.rule_type.value}"

    # Unique hoá rule_id trong phạm vi 1 run
    rule_id = base_id
    if used_ids is not None:
        counter = 2
        while rule_id in used_ids:
            rule_id = f"{base_id}#{counter}"
            counter += 1
        used_ids.add(rule_id)

    # Lọc validation guardrail — validate lại lần nữa phòng trường hợp LLM bypass
    try:
        ProposedRule.model_validate(rule.model_dump())
    except ValidationError as e:
        logger.warning(
            "Rule bị loại do không qua validation: %s | table=%s | errors=%s",
            rule_id,
            table_name,
            e.errors(),
        )
        return {}  # sentinel

    evidence_items = (requirement or {}).get("evidence_items", [])
    if requirement is None:
        evidence_items = [
            {"id": ref, "source_type": _evidence_source_type(ref), "value": None}
            for ref in rule.selected_evidence_refs
        ]
    evidence_by_id = {item["id"]: item for item in evidence_items}
    selected_refs = list(rule.selected_evidence_refs)
    allowed_refs = list(evidence_by_id)
    invalid_refs = [ref for ref in selected_refs if ref not in evidence_by_id]
    if invalid_refs:
        logger.warning(
            "Rule %s tham chiếu evidence không thuộc candidate; tự động chuẩn hóa %s",
            rule_id,
            invalid_refs,
        )
        selected_refs = allowed_refs
    if not selected_refs:
        logger.warning("Rule %s không còn evidence hợp lệ sau chuẩn hóa", rule_id)
        return {}

    # parameter_provenance trỏ vào cùng tập evidence với selected_evidence_refs.
    # Khi ở trên đã chuẩn hóa refs, provenance phải đi theo — nếu không, rule được
    # lưu với chứng cứ trỏ vào một id không tồn tại và mọi truy vết sau này đều gãy.
    provenance = [item.model_dump() for item in rule.parameter_provenance]
    if invalid_refs:
        for entry in provenance:
            if entry["source_ref"] in evidence_by_id:
                continue
            # Ưu tiên ref cùng loại nguồn để không đổi ý nghĩa của chứng cứ.
            original_type = str(entry["source_type"])
            replacement = next(
                (ref for ref in selected_refs if _evidence_source_type(ref) == original_type),
                selected_refs[0],
            )
            logger.warning(
                "Rule %s: provenance của tham số %s trỏ vào %s không hợp lệ — chuyển sang %s",
                rule_id,
                entry["parameter_name"],
                entry["source_ref"],
                replacement,
            )
            entry["source_ref"] = replacement
            entry["source_type"] = _evidence_source_type(replacement)

    digest = table_digest or {}
    sample = digest.get("sample") or {}
    dashboard_full_table = bool(digest.get("dashboard_candidate_mode"))
    evidence = RuleEvidenceSnapshot(
        sample_row_count=int(digest.get("rows") or 0) if dashboard_full_table else int(sample.get("n") or digest.get("rows") or 0),
        sample_rate=1.0 if dashboard_full_table else float(sample.get("rate", 1.0)),
        sampling_caveat=sample.get("caveat"),
        observed_metrics={
            ref: evidence_by_id[ref].get("value") for ref in selected_refs
        },
        source_refs=selected_refs,
    )

    return {
        "candidate_id": rule.candidate_id,
        "rule_id": rule_id,
        "run_id": run_id,
        "table_name": table_name,
        "column": rule.column,
        "rule_type": rule.rule_type.value,
        "parameters": rule.parameters.model_dump(exclude_none=True),
        "rule_name": rule.rule_name,
        "business_rationale": rule.business_rationale,
        "proposal_basis": rule.proposal_basis.value,
        "selected_evidence_refs": selected_refs,
        "parameter_provenance": provenance,
        "assumptions": list(rule.assumptions),
        "confidence": rule.confidence.model_dump(),
        "confidence_score": rule.confidence.overall,
        "evidence": evidence.model_dump(),
        "severity": rule.severity.value,
        "dimension": rule.dimension.value,
        "rule_description": rule.rule_description,
        "ai_reasoning": rule.ai_reasoning,
    }


# ---------------------------------------------------------------------------
# Main node: rule_proposer_node
# ---------------------------------------------------------------------------

async def rule_proposer_node(state: AgentState) -> dict:
    """Rule Proposer Node — fan-out LLM structured output per table.

    Đọc state["dataset_profile_digest"], tách theo bảng, gọi LLM song song,
    trả về proposed_rules, rule_proposal_errors, rule_run_id.
    """
    settings = get_settings()
    metadata = state.get("metadata", {})
    max_retries = metadata.get("max_retries", settings.rule_proposer_max_retries)

    digest = state.get("dataset_profile_digest", {})
    if not digest:
        logger.warning("dataset_profile_digest rỗng — không có gì để đề xuất.")
        return {
            "proposed_rules": [],
            "rule_proposal_errors": [{"error": "dataset_profile_digest rỗng"}],
            "rule_run_id": "",
        }

    # 1. Tách digest
    per_table = split_digest_by_table(digest)
    if not per_table:
        logger.warning("Không có bảng hợp lệ sau khi split_digest_by_table.")
        return {
            "proposed_rules": [],
            "rule_proposal_errors": [{"error": "Không có bảng hợp lệ trong digest"}],
            "rule_run_id": "",
        }

    out_dir = getattr(settings, "output_dir", None)
    res_dir = getattr(settings, "results_dir", None)
    base_dir = out_dir if isinstance(out_dir, (str, Path)) else (res_dir if isinstance(res_dir, (str, Path)) else "./output")
    rule_proposer_dir = Path(base_dir) / "rule_proposer"

    # 2. Debug dump (tuỳ chọn)
    if settings.debug_dump_table_digests:
        paths = dump_table_digests(per_table, str(rule_proposer_dir / "digest_by_table"))
        logger.info("Đã dump %d table digest ra: %s", len(paths), paths)

    # 3. Chuẩn bị LLM với structured output
    llm = get_llm(settings.llm_provider, temperature=0.1)
    structured_llm = llm.with_structured_output(TableRuleProposal)

    # 4. Fan-out
    semaphore = asyncio.Semaphore(settings.rule_proposer_concurrency)
    table_names = list(per_table.keys())
    dataset_id = state.get("dataset_id", "unknown")

    contract = state.get("semantic_contract") or {}
    tables_contract = contract.get("tables", {})

    all_candidates = state.get("rule_candidates", [])
    candidates_by_table = {}
    for c in all_candidates:
        tb = c.get("table")
        if tb:
            candidates_by_table.setdefault(tb, []).append(c)

    results = await asyncio.gather(
        *[
            _propose_for_table(
                table_name=t,
                table_digest=per_table[t],
                structured_llm=structured_llm,
                semaphore=semaphore,
                max_retries=max_retries,
                semantic_contract=tables_contract.get(t),
                candidates=candidates_by_table.get(t),
                dataset_id=dataset_id,
            )
            for t in table_names
        ],
        return_exceptions=True,
    )

    # 5. Xử lý kết quả
    run_id = state.get("rule_run_id") or uuid.uuid4().hex
    flat_rules: list[dict] = []
    errors: list[dict] = []
    used_ids: set[str] = set()

    for table_name, result in zip(table_names, results):
        if isinstance(result, Exception):
            logger.error("Bảng '%s' thất bại: %s", table_name, result)
            errors.append({"table": table_name, "error": str(result)})
            continue

        proposal: TableRuleProposal = result
        requirements = candidates_by_table.get(table_name)
        if requirements is None:
            requirements = _build_coverage_requirements(per_table[table_name])

        for rule in proposal.rules:
            stamped = _stamp_rule(
                rule,
                table_name,
                run_id,
                used_ids,
                _find_requirement(rule, requirements),
                per_table[table_name],
            )
            if stamped:
                flat_rules.append(stamped)

    logger.info(
        "rule_proposer_node hoàn thành: run_id=%s | %d rules | %d errors",
        run_id,
        len(flat_rules),
        len(errors),
    )

    # Xuất trace JSON proposed rules
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        rule_proposer_dir.mkdir(parents=True, exist_ok=True)
        dump_file = rule_proposer_dir / f"debug_proposed_rules_{timestamp}_{run_id}.json"
        dump_payload = {
            "run_id": run_id,
            "generated_at": datetime.now().isoformat(),
            "total_rules": len(flat_rules),
            "total_errors": len(errors),
            "proposed_rules": flat_rules,
            "errors": errors,
        }
        dump_file.write_text(json.dumps(dump_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Đã xuất trace proposed rules ra %s", dump_file)
    except Exception as exc:
        logger.warning("Không thể ghi file trace proposed rules: %s", exc)

    result: dict = {
        "proposed_rules": flat_rules,
        "rule_proposal_errors": errors,
        "rule_run_id": run_id,
    }

    # Thất bại toàn bộ (ví dụ LLM hết quota / mất mạng) trước đây chỉ được ghi vào
    # `rule_proposal_errors` rồi graph vẫn chạy tiếp và runner báo DONE với 0 rules —
    # không phân biệt được với trường hợp hợp lệ "dataset sạch, không cần rule nào".
    if errors and not flat_rules:
        result["error"] = (
            f"Rule proposer thất bại trên toàn bộ {len(errors)}/{len(table_names)} bảng: "
            + "; ".join(f"{e.get('table')}: {e.get('error')}" for e in errors[:3])
        )
    elif errors:
        logger.warning(
            "rule_proposer_node thành công một phần: %d/%d bảng thất bại",
            len(errors),
            len(table_names),
        )

    return result

# ---------------------------------------------------------------------------


async def persist_rules_node(state: AgentState) -> dict:
    """Lưu proposed_rules vào DB qua asyncio.to_thread (SQLAlchemy sync).

    Thin node: chỉ gọi save_proposed_rules và cập nhật metadata.
    """
    from src.services.rule_store import save_proposed_rules  # lazy import

    proposed_rules: list[dict] = state.get("proposed_rules", [])
    run_id: str = state.get("rule_run_id", "")
    dataset_id: str = state.get("dataset_id", "unknown")
    metadata: dict = state.get("metadata", {})

    if not proposed_rules:
        logger.warning("persist_rules_node: không có rule nào để lưu.")
        n_saved = 0
    else:
        n_saved = await asyncio.to_thread(
            save_proposed_rules, run_id, dataset_id, proposed_rules
        )
        logger.info("persist_rules_node: đã lưu %d rules (run_id=%s)", n_saved, run_id)

    return {
        "metadata": {
            **metadata,
            "rules_saved": n_saved,
            "rule_run_id": run_id,
        }
    }


# ---------------------------------------------------------------------------
# Debug harness
# ---------------------------------------------------------------------------

async def main():
    """Chạy rule_proposer_node từ file digest đã lưu.

    Run: python -m src.agents.nodes.rule_proposer_node
    """
    import glob

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    # Tìm file digest mới nhất trong output/profiler/ hoặc data/results/
    patterns = [
        os.path.abspath("./output/profiler/debug_profile_digest_*.json"),
        os.path.abspath("./data/results/debug_profile_digest_*.json"),
    ]
    files = []
    for pat in patterns:
        files.extend(glob.glob(pat))
    files = sorted(files)
    if not files:
        print("Không tìm thấy file debug_profile_digest_*.json trong output/profiler/ hoặc data/results/")
        print("Hãy chạy profiler trước: python -m src.agents.nodes.profiler_node")
        return

    latest = files[-1]
    print(f"Đọc digest từ: {latest}")
    with open(latest, encoding="utf-8") as f:
        raw = json.load(f)

    # Xây fake state
    state: AgentState = {
        "dataset_profile_digest": raw,  # split_digest_by_table sẽ unwrap nếu cần
        "dataset_id": "olist_debug",
        "metadata": {},
    }

    print("Bắt đầu rule_proposer_node …")
    result = await rule_proposer_node(state)

    print("Hoàn thành rule_proposer_node.")
    print(f"  Tổng rules   : {len(result.get('proposed_rules', []))}")
    print(f"  Errors       : {len(result.get('rule_proposal_errors', []))}")
    print(f"  run_id       : {result.get('rule_run_id')}")


if __name__ == "__main__":
    asyncio.run(main())
    # Run test syntax: python -m src.agents.nodes.rule_proposer_node

