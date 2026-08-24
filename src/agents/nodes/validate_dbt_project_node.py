from __future__ import annotations

import json
import logging
import tempfile
from datetime import datetime
from pathlib import Path

from src.agents.nodes.dbt_validation import (
    dbt_parse_was_skipped,
    get_state_dbt_yaml,
    materialize_dbt_project,
    run_dbt_parse,
    validate_dbt_yaml_structure,
)
from src.agents.state import AgentState
from src.config import get_settings
from src.services.dbt_artifact_store import get_dbt_artifact_store, validate_run_id

logger = logging.getLogger(__name__)


async def validate_dbt_project_node(state: AgentState) -> dict:
    run_id = validate_run_id(str(state.get("test_run_id") or state.get("rule_run_id") or "test_run"))
    content = ""
    valid = False
    error = None
    return_code = None
    dbt_skipped = False
    try:
        content = get_state_dbt_yaml(state)
        validate_dbt_yaml_structure(content)
        root_dir = Path(__file__).resolve().parents[3]
        with tempfile.TemporaryDirectory(prefix=f"dbt-parse-{run_id}-") as workspace:
            dbt_dir = materialize_dbt_project(root_dir / "dbt_project", Path(workspace), content)
            valid, output, return_code = run_dbt_parse(dbt_dir)
            dbt_skipped = dbt_parse_was_skipped(output)
            if dbt_skipped:
                logger.warning(
                    "Chốt chặn dbt BỊ BỎ QUA (không tìm thấy executable dbt): %s", output
                )
            if not valid:
                error = output or "dbt parse failed"
    except Exception as exc:
        error = str(exc)

    settings = get_settings()
    trace_dir = Path(settings.output_dir) / "validate_dbt_project" / run_id
    trace_dir.mkdir(parents=True, exist_ok=True)
    trace_path = trace_dir / f"validation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    trace_path.write_text(
        json.dumps({"valid": valid, "return_code": return_code, "error": error}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    updates: dict = {
        "generated_dbt_yaml": content,
        "dbt_validation_valid": valid,
        # Phân biệt "đã chạy dbt parse và đạt" với "chưa hề chạy dbt" — nếu không,
        # báo cáo ghi dbt_status=SUCCESS cho một lần kiểm tra chưa từng diễn ra.
        "dbt_validation_skipped": dbt_skipped,
        "dbt_validation_error": error,
        "dbt_validation_attempts": int(state.get("dbt_validation_attempts", 0)),
        "dbt_validation_trace_path": str(trace_path),
    }
    if valid and content:
        content_bytes = content.encode("utf-8")
        local_trace = Path(state.get("dbt_trace_file_path") or trace_dir / "generated_dq_tests.yml")
        local_trace.parent.mkdir(parents=True, exist_ok=True)
        local_trace.write_bytes(content_bytes)
        updates["dbt_trace_file_path"] = str(local_trace)
        try:
            ref = get_dbt_artifact_store().upload_yaml(
                run_id,
                content_bytes,
                dataset_id=state.get("dataset_id"),
                rule_run_id=state.get("rule_run_id"),
            )
            updates["dbt_artifact_ref"] = ref.to_dict()
        except Exception as exc:
            storage_is_configured = bool(
                settings.object_storage_endpoint_url
                or settings.object_storage_access_key_id
                or settings.object_storage_secret_access_key
            )
            if settings.app_env not in ("local", "development", "test") and storage_is_configured:
                updates["dbt_validation_valid"] = False
                updates["dbt_validation_error"] = f"Unable to persist validated dbt artifact: {exc}"
            else:
                logger.warning("Validated dbt artifact upload failed; using local trace: %s", exc)
    return updates
