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
import hashlib
import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path

from langchain_core.messages import SystemMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError

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
    ProposedRule,
    RuleEvidenceSnapshot,
    TableRuleProposal,
)
from src.services.llm import get_llm

logger = logging.getLogger(__name__)


class CandidateProposedRule(ProposedRule):
    """Node 8 contract: every LLM rule must point back to one server candidate."""

    candidate_id: str = Field(min_length=1)


class CandidateTableRuleProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: str
    rules: list[CandidateProposedRule] = Field(default_factory=list)

# ---------------------------------------------------------------------------
# Domain context & Data dictionary injected vào mỗi lần gọi LLM
# ---------------------------------------------------------------------------

def _merge_table_business_contexts(state: AgentState) -> dict[str, str]:
    """Merge new and legacy context fields, with the new field taking precedence."""
    legacy = state.get("specialized_system_prompts") or {}
    current = state.get("table_business_contexts") or {}
    return {
        **(legacy if isinstance(legacy, dict) else {}),
        **(current if isinstance(current, dict) else {}),
    }


def _dictionary_for_table(normalized_dictionary: object, table_name: str) -> dict | str | None:
    """Resolve one table's dictionary from supported state shapes.

    Inferred dictionaries are stored as ``{"tables": {name: payload}}`` while
    some callers provide a directly keyed mapping or a single-table payload.
    Keep this normalization at the node boundary so prompt construction always
    receives the dictionary for the table being proposed.
    """
    if not isinstance(normalized_dictionary, dict):
        return normalized_dictionary if normalized_dictionary else None

    tables = normalized_dictionary.get("tables")
    if isinstance(tables, dict) and table_name in tables:
        return tables[table_name]

    if table_name in normalized_dictionary:
        return normalized_dictionary[table_name]

    if normalized_dictionary.get("table_name") == table_name:
        return normalized_dictionary

    return None


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
        column.get("name") for column in digest_columns if isinstance(column, dict) and column.get("name")
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

        if "no_nulls" in signals and (role in {"id", "datetime", "category", "categorical"}):
            requirements.append(
                {
                    "column": name,
                    "rule_type": "NOT_NULL",
                    "evidence": ["no_nulls", f"role={role}"],
                }
            )

        if signals.intersection({"has_pk_constraint", "has_unique_constraint", "unique_full_table"}):
            requirements.append(
                {
                    "column": name,
                    "rule_type": "UNIQUE",
                    "evidence": sorted(
                        signals.intersection({"has_pk_constraint", "has_unique_constraint", "unique_full_table"})
                    ),
                }
            )

        if role == "numeric" and signals.intersection(
            {"has_extreme_outliers", "has_negative_values", "has_zero_values"}
        ):
            requirements.append(
                {
                    "column": name,
                    "rule_type": "RANGE",
                    "evidence": sorted(
                        signals.intersection({"has_extreme_outliers", "has_negative_values", "has_zero_values"})
                    ),
                }
            )

        values = [value for value in column.get("values", []) if value is not None]
        if role == "categorical" and values:
            requirements.append(
                {
                    "column": name,
                    "rule_type": "ACCEPTED_VALUES",
                    "evidence": {"values": values},
                }
            )

        if role == "datetime":
            requirements.append(
                {
                    "column": name,
                    "rule_type": "FRESHNESS",
                    "evidence": {"range": column.get("range")},
                }
            )

        if null_pct > 5.0:
            requirements.append(
                {
                    "column": name,
                    "rule_type": "NULL_RATE",
                    "evidence": {"null_pct": null_pct},
                }
            )

        is_datetime_col = role == "datetime" or any(
            suffix in name.lower() for suffix in ["_at", "_time", "_date", "datetime"]
        )
        if "fixed_length" in signals and not is_datetime_col:
            requirements.append(
                {
                    "column": name,
                    "rule_type": "REGEX_FORMAT",
                    "evidence": {"length_stats": column.get("length_stats")},
                }
            )

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

        requirements.append(
            {
                "column": source_column,
                "rule_type": "CROSS_FIELD_COMPARISON",
                "parameters": {
                    "target_column": target_column,
                    "operator": operator,
                },
                "evidence": hint,
            }
        )

    requirements.append(
        {
            "column": None,
            "rule_type": "ROW_COUNT",
            "parameters": {
                "min_row_count": max(1, int((table_digest.get("rows") or 0) * 0.8)),
            },
            "evidence": {"rows": table_digest.get("rows", 0)},
        }
    )
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
                evidence_items.append(
                    {
                        "id": reference,
                        "source_type": _evidence_source_type(reference),
                        "metric": metric,
                        "value": _digest_metric_value(table_digest, column, metric),
                    }
                )
        elif isinstance(raw_evidence, dict):
            for metric, value in raw_evidence.items():
                evidence_items.append(
                    {
                        "id": f"profile:{column or '_table'}:{metric}",
                        "source_type": "DATA_PROFILE",
                        "metric": metric,
                        "value": value,
                    }
                )
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


def _candidate_key(candidate: dict) -> str:
    return json.dumps(
        {
            "table": candidate.get("table"),
            "column": candidate.get("column"),
            "rule_type": candidate.get("rule_type"),
            "parameters": candidate.get("parameters") or {},
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def _dedupe_candidates(candidates: list[dict]) -> list[dict]:
    """Dedupe candidates while retaining every distinct evidence item."""
    merged: dict[str, dict] = {}
    for raw in candidates:
        if not isinstance(raw, dict):
            continue
        key = _candidate_key(raw)
        candidate = dict(raw)
        if not candidate.get("candidate_id"):
            digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
            candidate["candidate_id"] = f"candidate-{digest}"
        if key not in merged:
            merged[key] = {**candidate, "evidence_items": list(candidate.get("evidence_items") or [])}
            continue
        existing = merged[key]
        evidence = list(existing.get("evidence_items") or [])
        seen = {
            str(item.get("id"))
            for item in evidence
            if isinstance(item, dict) and item.get("id")
        }
        for item in raw.get("evidence_items") or []:
            if not isinstance(item, dict):
                continue
            evidence_id = str(item.get("id") or "")
            if evidence_id and evidence_id not in seen:
                evidence.append(item)
                seen.add(evidence_id)
        existing["evidence_items"] = evidence
    return list(merged.values())


def _candidate_batches(candidates: list[dict], batch_size: int) -> list[list[dict]]:
    return [candidates[index:index + batch_size] for index in range(0, len(candidates), batch_size)]


def _related_columns(candidates: list[dict]) -> set[str]:
    names: set[str] = set()
    for candidate in candidates:
        column = candidate.get("column")
        if isinstance(column, str) and column:
            names.add(column)
        target = (candidate.get("parameters") or {}).get("target_column")
        if isinstance(target, str) and target:
            names.add(target)
    return names


def _filter_table_context(table_digest: dict, candidates: list[dict]) -> dict:
    """Keep table-level metrics and only the columns referenced by a batch."""
    names = _related_columns(candidates)
    compact = {
        key: value
        for key, value in table_digest.items()
        if key not in {"columns", "dashboard_rule_candidates"}
    }
    columns = table_digest.get("columns") or []
    compact["columns"] = [
        column for column in columns
        if isinstance(column, dict) and column.get("name") in names
    ]
    return compact


def _filter_semantic_context(semantic_contract: dict | None, candidates: list[dict]) -> dict:
    if not isinstance(semantic_contract, dict):
        return {}
    names = _related_columns(candidates)
    compact = {key: value for key, value in semantic_contract.items() if key != "columns"}
    compact["columns"] = [
        column for column in semantic_contract.get("columns", [])
        if isinstance(column, dict) and column.get("name") in names
    ]
    return compact


async def _propose_for_table(
    table_name: str,
    table_digest: dict,
    structured_llm,
    semaphore: asyncio.Semaphore,
    max_retries: int,
    semantic_contract: dict | None = None,
    business_context: str | None = None,
    data_dictionary: dict | str | None = None,
    candidates: list[dict] | None = None,
    dataset_id: str = "unknown",
    specialized_system_prompt: str | None = None,
) -> CandidateTableRuleProposal:
    """Gọi LLM một lần cho một bảng, bảo vệ bằng semaphore + retry."""
    columns = [col["name"] for col in table_digest.get("columns", [])]
    dashboard_mode = bool(table_digest.get("dashboard_candidate_mode"))
    is_taxi = (
        "trip" in table_name.lower()
        or "taxi" in str(dataset_id).lower()
        or any("pickup" in str(c).lower() for c in columns)
        or table_name in ("source_rows", "trips_raw")
    )
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
                else:
                    dict_content = (
                        json.dumps(data_dictionary, ensure_ascii=False, indent=2)
                        if isinstance(data_dictionary, dict)
                        else (str(data_dictionary) if data_dictionary else "None")
                    )
                    messages = rule_proposer_prompt.format_messages(
                        table_name=table_name,
                        table_digest=json.dumps(table_digest, ensure_ascii=False),
                        semantic_contract=json.dumps(semantic_contract or {}, ensure_ascii=False),
                        business_context=business_context or "Không có ngữ cảnh nghiệp vụ bổ sung.",
                        data_dictionary=dict_content,
                        historical_rules=json.dumps(historical, ensure_ascii=False),
                        coverage_requirements=coverage_requirements,
                        few_shot_examples=_RULE_PROPOSER_FEW_SHOT,
                    )
                if specialized_system_prompt:
                    guardrails = (
                        "\n\nReturn only a valid TableRuleProposal. Propose rules only from the "
                        "provided candidates, keep candidate_id and evidence references exact, "
                        "and do not invent columns, metrics, thresholds, or evidence."
                    )
                    messages = [
                        SystemMessage(content=specialized_system_prompt + guardrails),
                        *messages[1:],
                    ]
                result: CandidateTableRuleProposal = await structured_llm.ainvoke(messages)
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
                    wait_seconds = 2**attempt  # 1s, 2s, 4s …
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
        base_id = f"{table_name}.{col_key}.VS.{target_column}.{rule.rule_type.value}"
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
            {"id": ref, "source_type": _evidence_source_type(ref), "value": None} for ref in rule.selected_evidence_refs
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
        sample_row_count=int(digest.get("rows") or 0)
        if dashboard_full_table
        else int(sample.get("n") or digest.get("rows") or 0),
        sample_rate=1.0 if dashboard_full_table else float(sample.get("rate", 1.0)),
        sampling_caveat=sample.get("caveat"),
        observed_metrics={ref: evidence_by_id[ref].get("value") for ref in selected_refs},
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
    base_dir = (
        out_dir if isinstance(out_dir, (str, Path)) else (res_dir if isinstance(res_dir, (str, Path)) else "./output")
    )
    rule_proposer_dir = Path(base_dir) / "rule_proposer"

    # 2. Debug dump (tuỳ chọn)
    if settings.debug_dump_table_digests:
        paths = dump_table_digests(per_table, str(rule_proposer_dir / "digest_by_table"))
        logger.info("Đã dump %d table digest ra: %s", len(paths), paths)

    # 3. Chuẩn bị LLM với structured output
    llm = get_llm(settings.llm_provider, temperature=0.1)
    structured_llm = llm.with_structured_output(CandidateTableRuleProposal)

    # 4. Fan-out in bounded batches. Each request sees only relevant columns.
    semaphore = asyncio.Semaphore(settings.rule_proposer_concurrency)
    table_names = list(per_table.keys())
    dataset_id = state.get("dataset_id", "unknown")
    configured_batch_size = getattr(settings, "rule_proposer_batch_size", 8)
    batch_size = configured_batch_size if isinstance(configured_batch_size, int) else 8

    contract = state.get("semantic_contract") or {}
    tables_contract = contract.get("tables", {})

    business_contexts = _merge_table_business_contexts(state)
    normalized_dict = state.get("normalized_data_dictionary") or {}

    all_candidates = state.get("rule_candidates", [])
    candidates_by_table: dict[str, list[dict]] = {}
    for c in all_candidates:
        tb = c.get("table") if isinstance(c, dict) else None
        if isinstance(tb, str) and tb:
            candidates_by_table.setdefault(tb, []).append(c)

    specialized_prompts = state.get("specialized_system_prompts") or {}
    batch_jobs: list[tuple[str, int, list[dict]]] = []
    for table_name in table_names:
        table_candidates = candidates_by_table.get(table_name)
        if table_candidates is None:
            table_candidates = _build_coverage_requirements(per_table[table_name])
        table_candidates = _dedupe_candidates(table_candidates)
        candidates_by_table[table_name] = table_candidates
        for batch_index, candidate_batch in enumerate(
            _candidate_batches(table_candidates, batch_size), start=1
        ):
            batch_jobs.append((table_name, batch_index, candidate_batch))

    results = await asyncio.gather(
        *[
            _propose_for_table(
                table_name=table_name,
                table_digest=_filter_table_context(per_table[table_name], candidate_batch),
                structured_llm=structured_llm,
                semaphore=semaphore,
                max_retries=max_retries,
                semantic_contract=_filter_semantic_context(
                    tables_contract.get(table_name), candidate_batch
                ),
                business_context=business_contexts.get(table_name),
                data_dictionary=_dictionary_for_table(normalized_dict, table_name),
                candidates=candidate_batch,
                dataset_id=dataset_id,
                specialized_system_prompt=specialized_prompts.get(table_name),
            )
            for table_name, _batch_index, candidate_batch in batch_jobs
        ],
        return_exceptions=True,
    )

    # 5. Xử lý kết quả
    run_id = state.get("rule_run_id") or uuid.uuid4().hex
    flat_rules: list[dict] = []
    errors: list[dict] = []
    used_ids: set[str] = set()

    stamped_rule_keys: set[str] = set()
    for (table_name, batch_index, requirements), result in zip(batch_jobs, results):
        if isinstance(result, Exception):
            logger.error("Bảng '%s' batch %d thất bại: %s", table_name, batch_index, result)
            errors.append({"table": table_name, "batch": batch_index, "error": str(result)})
            continue

        # Final strict validation remains the public contract after the
        # candidate-aware structured-output adapter has repaired provenance.
        proposal = TableRuleProposal(
            # The server-side batch owns the table identity. Do not allow an
            # LLM response (or a legacy adapter) to redirect rules elsewhere.
            table=table_name,
            rules=[ProposedRule.model_validate(rule.model_dump()) for rule in result.rules],
        )
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
                stamped["selected_evidence_refs"] = list(
                    dict.fromkeys(stamped.get("selected_evidence_refs") or [])
                )
                signature = json.dumps(
                    {
                        "table": stamped.get("table_name"),
                        "column": stamped.get("column"),
                        "rule_type": stamped.get("rule_type"),
                        "parameters": stamped.get("parameters") or {},
                    },
                    sort_keys=True,
                    default=str,
                )
                if signature in stamped_rule_keys:
                    continue
                stamped_rule_keys.add(signature)
                flat_rules.append(stamped)

    # Never persist a partial policy set when one of its batches failed.
    if errors:
        flat_rules = []

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

    if errors:
        result["error"] = (
            f"Rule proposer failed closed because {len(errors)}/{len(batch_jobs)} batch(es) failed: "
            + "; ".join(
                f"{e.get('table')} batch {e.get('batch')}: {e.get('error')}"
                for e in errors[:3]
            )
        )
    elif not flat_rules:
        result["error"] = "Rule proposer returned no valid structured proposals."

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
        n_saved = await asyncio.to_thread(save_proposed_rules, run_id, dataset_id, proposed_rules)
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
