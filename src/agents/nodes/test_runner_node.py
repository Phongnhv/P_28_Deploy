"""Test Runner Node — Thực thi các câu lệnh test SQL trên cơ sở dữ liệu và thu thập metrics.

Thu thập:
- total_rows, violation_count, violation_rate
- sample_failures (tối đa SAMPLE_FAILURE_LIMIT ID dòng vi phạm — chỉ ID, không phải nội dung dòng)
- duration_ms
- status: PASSED / FAILED / ERROR / SKIPPED
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text

from src.agents.nodes.dbt_validation import get_state_dbt_yaml, materialize_dbt_project, validate_dbt_yaml_structure
from src.agents.state import AgentState
from src.config import get_settings
from src.models.rule_schemas import RuleType
from src.services.rule_store import get_engine
from src.time_utils import utc_now

logger = logging.getLogger(__name__)


def _quote_ident(ident: str, dialect_name: str = "sqlite") -> str:
    clean_ident = ident.replace('"', '""').strip()
    return f'"{clean_ident}"'


# Số ID vi phạm tối đa được gom cho mỗi rule. Khớp với `failed_id_limit` của đường
# thực thi Supabase (src/services/supabase_dataset.py) để hai pipeline cùng một giới hạn.
SAMPLE_FAILURE_LIMIT = 20

# Thứ tự ưu tiên khi dò cột định danh của bảng.
_IDENTITY_COLUMN_PREFERENCES = ("source_row_id", "id", "row_id")


def _resolve_identity_column(table_name: str) -> str | None:
    """Tìm cột định danh của bảng để trích ID dòng vi phạm.

    Ưu tiên: source_row_id → khóa chính → id/row_id → cột đầu tiên có hậu tố `_id`.
    Trả về None nếu không xác định được (khi đó bỏ qua việc lấy mẫu thay vì đọc cả dòng).
    """
    try:
        inspector = sa_inspect(get_engine())
        columns = [col["name"] for col in inspector.get_columns(table_name)]
    except Exception as exc:
        logger.warning("Không thể đọc schema của bảng %s: %s", table_name, exc)
        return None

    if not columns:
        return None

    lookup = {name.lower(): name for name in columns}
    for preferred in _IDENTITY_COLUMN_PREFERENCES:
        if preferred in lookup:
            return lookup[preferred]

    try:
        pk_columns = inspector.get_pk_constraint(table_name).get("constrained_columns") or []
        if pk_columns:
            return pk_columns[0]
    except Exception:
        pass

    for name in columns:
        if name.lower().endswith("_id"):
            return name
    return None


def _assert_safe_predicate(predicate: str) -> None:
    """Chốt chặn chống SQL injection cho predicate do hệ thống tự sinh.

    Dùng `raise` chứ KHÔNG dùng `assert`: Python xoá mọi câu lệnh assert khi chạy với
    cờ -O / PYTHONOPTIMIZE=1 (nhiều image production bật mặc định), tức chốt chặn bảo mật
    sẽ biến mất đúng lúc cần nhất.
    """
    if "--" in predicate or ";" in predicate or "/*" in predicate or "*/" in predicate:
        raise ValueError(
            "Security violation: potential SQL injection detected in predicate"
        )


def _fetch_sample_failures(
    table_name: str,
    predicate: str | None,
    bind_params: dict,
    dialect_name: str = "sqlite",
    limit: int = SAMPLE_FAILURE_LIMIT,
) -> list[str]:
    """Lấy tối đa `limit` ID của các dòng vi phạm điều kiện.

    Chỉ trả về ID, KHÔNG trả về nội dung dòng: kết quả này được ghi vào cột
    `dq_results.failed_row_ids` và hiển thị trên UI. Bản trước dùng `SELECT *` nên lưu
    nguyên bản ghi (toạ độ, thời gian, số tiền...) vào một cột mang tên "row_ids".

    CRITICAL SAFETY GUARD: predicate MUST be programmatically constructed (e.g. from _build_row_predicate)
    and must not accept raw user input directly.
    """
    if not predicate or predicate == "1=0":
        return []

    _assert_safe_predicate(predicate)

    identity_column = _resolve_identity_column(table_name)
    if not identity_column:
        logger.warning(
            "Bỏ qua lấy mẫu vi phạm cho %s: không xác định được cột định danh", table_name
        )
        return []

    quoted_table = _quote_ident(table_name, dialect_name)
    quoted_id = _quote_ident(identity_column, dialect_name)
    sample_sql = (
        f"SELECT {quoted_id} FROM {quoted_table} WHERE {predicate} "
        f"ORDER BY {quoted_id} LIMIT {limit}"
    )

    engine = get_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(sample_sql), bind_params).fetchall()
            return [str(row[0]) for row in rows if row[0] is not None]
    except Exception as exc:
        logger.warning("Không thể lấy sample failures cho %s: %s", table_name, exc)
        return []


def _fetch_unique_samples(
    table_name: str,
    col_name: str,
    dialect_name: str = "sqlite",
    limit: int = SAMPLE_FAILURE_LIMIT,
) -> list[str]:
    """Lấy tối đa `limit` ID của các dòng có giá trị bị trùng lặp (rule UNIQUE)."""
    identity_column = _resolve_identity_column(table_name)
    if not identity_column:
        logger.warning(
            "Bỏ qua lấy mẫu trùng lặp cho %s: không xác định được cột định danh", table_name
        )
        return []

    quoted_table = _quote_ident(table_name, dialect_name)
    quoted_col = _quote_ident(col_name, dialect_name)
    quoted_id = _quote_ident(identity_column, dialect_name)
    sample_sql = (
        f"SELECT {quoted_id} FROM {quoted_table} "
        f"WHERE {quoted_col} IN ("
        f"SELECT {quoted_col} FROM {quoted_table} "
        f"WHERE {quoted_col} IS NOT NULL "
        f"GROUP BY {quoted_col} HAVING COUNT(*) > 1"
        f") ORDER BY {quoted_id} LIMIT {limit}"
    )
    engine = get_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(sample_sql)).fetchall()
            return [str(row[0]) for row in rows if row[0] is not None]
    except Exception as exc:
        logger.warning("Không thể lấy duplicate samples cho %s.%s: %s", table_name, col_name, exc)
        return []


def _execute_single_test(test: dict, dialect_name: str) -> list[dict]:
    """Thực thi một test query và trả về danh sách test results của các rule trong test."""
    results: list[dict] = []
    table_name = test.get("table_name", "")
    rules_meta = test.get("rules_meta", [])

    # Trường hợp query không valid (sau 3 lần repair vẫn lỗi)
    if not test.get("valid"):
        for meta in rules_meta:
            rule = meta.get("rule", {})
            results.append({
                "rule_id": rule.get("rule_id", ""),
                "table_name": table_name,
                "column": rule.get("column"),
                "rule_type": rule.get("rule_type", ""),
                "severity": rule.get("severity", "MEDIUM"),
                "dimension": rule.get("dimension", "VALIDITY"),
                "status": "ERROR",
                "violation_count": 0,
                "total_rows": 0,
                "violation_rate": 0.0,
                "sample_failures": None,
                "sql_text": test.get("sql_text", ""),
                "duration_ms": 0.0,
                "error": test.get("error", "SQL validation failed"),
            })
        return results

    sql = test.get("sql_text", "")
    params = test.get("bind_params", {})
    query_type = test.get("query_type", "batch_row")
    engine = get_engine()

    start_t = time.perf_counter()
    try:
        with engine.connect() as conn:
            stmt = text(sql)
            if params:
                stmt = stmt.bindparams(**params)
            res = conn.execute(stmt)
            row = res.mappings().fetchone()
    except Exception as exc:
        duration_ms = (time.perf_counter() - start_t) * 1000
        logger.error("Lỗi thực thi SQL test query [%s]: %s", sql, exc)
        for meta in rules_meta:
            rule = meta.get("rule", {})
            results.append({
                "rule_id": rule.get("rule_id", ""),
                "table_name": table_name,
                "column": rule.get("column"),
                "rule_type": rule.get("rule_type", ""),
                "severity": rule.get("severity", "MEDIUM"),
                "dimension": rule.get("dimension", "VALIDITY"),
                "status": "ERROR",
                "violation_count": 0,
                "total_rows": 0,
                "violation_rate": 0.0,
                "sample_failures": None,
                "sql_text": sql,
                "duration_ms": round(duration_ms, 2),
                "error": str(exc),
            })
        return results

    duration_ms = (time.perf_counter() - start_t) * 1000

    if not row:
        row = {}

    total_rows = int(row.get("total_rows") or 0)

    # 1. BATCH ROW
    if query_type == "batch_row":
        for meta in rules_meta:
            rule = meta.get("rule", {})
            alias = meta.get("alias", "")
            pred = meta.get("predicate")
            r_type = rule.get("rule_type", "")
            p_params = rule.get("effective_parameters") or rule.get("parameters") or {}

            v_count = int(row.get(alias) or 0)
            v_rate = round(v_count / total_rows, 4) if total_rows > 0 else 0.0

            # Đánh giá status
            if r_type in (RuleType.NULL_RATE.value, "NULL_RATE"):
                max_null_pct = float(p_params.get("max_null_pct", 5.0))
                status = "PASSED" if (v_rate * 100.0) <= max_null_pct else "FAILED"
            else:
                status = "PASSED" if v_count == 0 else "FAILED"

            samples = None
            if v_count > 0 and pred:
                samples = _fetch_sample_failures(table_name, pred, params, dialect_name)

            results.append({
                "rule_id": rule.get("rule_id", ""),
                "table_name": table_name,
                "column": rule.get("column"),
                "rule_type": r_type,
                "severity": rule.get("severity", "MEDIUM"),
                "dimension": rule.get("dimension", "VALIDITY"),
                "status": status,
                "violation_count": v_count,
                "total_rows": total_rows,
                "violation_rate": v_rate,
                "sample_failures": samples,
                "sql_text": sql,
                "duration_ms": round(duration_ms, 2),
                "error": None,
            })

    # 2. UNIQUE
    elif query_type == "unique":
        meta = rules_meta[0] if rules_meta else {}
        rule = meta.get("rule", {})
        col = rule.get("column", "")
        v_count = max(0, int(row.get("violation_count") or 0))
        v_rate = round(v_count / total_rows, 4) if total_rows > 0 else 0.0
        status = "PASSED" if v_count == 0 else "FAILED"

        samples = None
        if v_count > 0:
            samples = _fetch_unique_samples(table_name, col, dialect_name)

        results.append({
            "rule_id": rule.get("rule_id", ""),
            "table_name": table_name,
            "column": col,
            "rule_type": rule.get("rule_type", "UNIQUE"),
            "severity": rule.get("severity", "CRITICAL"),
            "dimension": rule.get("dimension", "UNIQUENESS"),
            "status": status,
            "violation_count": v_count,
            "total_rows": total_rows,
            "violation_rate": v_rate,
            "sample_failures": samples,
            "sql_text": sql,
            "duration_ms": round(duration_ms, 2),
            "error": None,
        })

    # 3. ROW COUNT
    elif query_type == "row_count":
        meta = rules_meta[0] if rules_meta else {}
        rule = meta.get("rule", {})
        min_rows = test.get("min_row_count", 0)
        is_pass = total_rows >= min_rows
        status = "PASSED" if is_pass else "FAILED"
        v_count = 0 if is_pass else 1
        v_rate = 0.0 if is_pass else 1.0

        results.append({
            "rule_id": rule.get("rule_id", ""),
            "table_name": table_name,
            "column": None,
            "rule_type": rule.get("rule_type", "ROW_COUNT"),
            "severity": rule.get("severity", "HIGH"),
            "dimension": rule.get("dimension", "COMPLETENESS"),
            "status": status,
            "violation_count": v_count,
            "total_rows": total_rows,
            "violation_rate": v_rate,
            "sample_failures": None,
            "sql_text": sql,
            "duration_ms": round(duration_ms, 2),
            "error": None,
        })

    # 4. FRESHNESS
    elif query_type == "freshness":
        meta = rules_meta[0] if rules_meta else {}
        rule = meta.get("rule", {})
        col = rule.get("column", "")
        max_ts = row.get("max_timestamp")
        max_age_hours = test.get("max_age_hours", 24.0)

        status = "PASSED"
        v_count = 0
        v_rate = 0.0
        err_msg = None

        if max_ts is None:
            status = "FAILED"
            v_count = 1
            v_rate = 1.0
            err_msg = "Không tìm thấy dữ liệu thời gian (NULL)."
        else:
            try:
                # Xử lý parsing datetime
                if isinstance(max_ts, str):
                    ts_val = datetime.fromisoformat(max_ts.replace("Z", "+00:00"))
                else:
                    ts_val = max_ts

                if ts_val.tzinfo is None:
                    # Timestamp không mang timezone → theo hợp đồng naive-UTC của
                    # src/time_utils.py, phải so với ĐỒNG HỒ UTC. Bản trước dùng
                    # datetime.now() (giờ địa phương) nên ở múi UTC+7 tuổi dữ liệu bị
                    # thổi phồng thêm 7 giờ → FRESHNESS báo FAILED giả.
                    now_t = utc_now()
                else:
                    now_t = datetime.now(UTC)

                age_delta = now_t - ts_val
                if age_delta > timedelta(hours=max_age_hours):
                    status = "FAILED"
                    v_count = 1
                    v_rate = 1.0
                    err_msg = (
                        f"Dữ liệu đã cũ {age_delta.total_seconds() / 3600:.1f} giờ "
                        f"(ngưỡng {max_age_hours} giờ); mốc mới nhất: {max_ts}."
                    )
            except Exception as exc:
                logger.warning("Không thể parse timestamp freshness: %s", exc)

        results.append({
            "rule_id": rule.get("rule_id", ""),
            "table_name": table_name,
            "column": col,
            "rule_type": rule.get("rule_type", "FRESHNESS"),
            "severity": rule.get("severity", "MEDIUM"),
            "dimension": rule.get("dimension", "FRESHNESS"),
            "status": status,
            "violation_count": v_count,
            "total_rows": total_rows,
            "violation_rate": v_rate,
            # Freshness là kiểm tra cấp bảng, không có dòng vi phạm cụ thể. Mốc thời gian
            # đã nằm trong `error`; giữ danh sách rỗng để đúng hợp đồng list[str].
            "sample_failures": [],
            "sql_text": sql,
            "duration_ms": round(duration_ms, 2),
            "error": err_msg,
        })

    return results



def _run_dbt_cli_test(dbt_dir: Path) -> bool:

    """Thực thi lệnh CLI dbt test đối với dự án dbt_project chứa file YML đã sinh."""
    dbt_cmd = shutil.which("dbt")
    settings = get_settings()
    if not dbt_cmd:
        if settings.app_env in ("local", "development", "test"):
            logger.warning("dbt executable unavailable; using legacy SQL result fallback")
            return False
        raise RuntimeError("dbt executable is required in production")
    try:
        res = subprocess.run(
            [dbt_cmd, "test", "--project-dir", str(dbt_dir), "--profiles-dir", str(dbt_dir), "--select", "generated_dq_tests"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        output = "\n".join(part.strip() for part in (res.stdout, res.stderr) if part.strip())
        if res.returncode != 0:
            raise RuntimeError(f"dbt test failed (returncode={res.returncode}): {output}")
        logger.info("Chạy dbt test CLI thành công (returncode=%d): %s", res.returncode, output[:200])
        return True
    except Exception:
        raise


async def test_runner_node(state: AgentState) -> dict:
    """LangGraph Node: Thực thi dbt test CLI (nếu có) hoặc fallback thực thi các test queries đã sinh."""
    # These deterministic SQL queries remain the compatibility metrics source.
    # The dbt artifact quality gate, rather than per-query EXPLAIN, authorizes execution.
    tests = [{**test, "valid": True, "error": None} for test in state.get("generated_tests", [])]
    if state.get("dbt_validation_valid") is not True:
        raise RuntimeError("test_runner requires a successfully validated dbt artifact")
    engine = get_engine()
    dialect_name = engine.dialect.name

    root_dir = Path(__file__).resolve().parent.parent.parent.parent
    dbt_template_dir = root_dir / "dbt_project"

    # Materialize the immutable project template and this run's YAML in an isolated workspace.
    # The direct SQL checks below remain the persisted result source for the existing pipeline.
    with tempfile.TemporaryDirectory(prefix=f"dbt-{state.get('test_run_id', 'run')}-") as workspace:
        content = get_state_dbt_yaml(state)
        validate_dbt_yaml_structure(content)
        dbt_dir = materialize_dbt_project(dbt_template_dir, Path(workspace), content)
        dbt_executed = _run_dbt_cli_test(dbt_dir)

        all_results: list[dict] = []

        # Chạy các test queries bất đồng bộ trong threadpool
        tasks = [
            asyncio.to_thread(_execute_single_test, test, dialect_name)
            for test in tests
        ]
        outputs = await asyncio.gather(*tasks)
        for res_list in outputs:
            all_results.extend(res_list)

    # Post-process and normalize results to match the canonical result format (Phase 2.7)
    normalized_results = []
    for r in all_results:
        status_raw = r.get("status", "PASSED")
        status = "PASS" if status_raw in ("PASSED", "PASS") else "FAIL" if status_raw in ("FAILED", "FAIL") else "ERROR" if status_raw == "ERROR" else "SKIPPED"

        checked_count = r.get("total_rows", 0)
        failed_count = r.get("violation_count", 0)
        violation_rate = r.get("violation_rate", 0.0)

        dbt_status = "PASS" if dbt_executed and status == "PASS" else "FAIL" if dbt_executed and status == "FAIL" else "NOT_RUN"
        metrics_status = "PASS" if status == "PASS" else "FAIL" if status == "FAIL" else "ERROR"

        normalized_results.append({
            "rule_id": r.get("rule_id", ""),
            "rule_version": r.get("rule_version", "rule-v1"),
            "table_name": r.get("table_name", ""),
            "column": r.get("column"),
            "status": status,
            "checked_count": checked_count,
            "failed_count": failed_count,
            "violation_rate": violation_rate,
            "severity": r.get("severity", "MEDIUM"),
            "dimension": r.get("dimension", "VALIDITY"),
            "duration_ms": r.get("duration_ms", 0.0),
            "dbt_status": dbt_status,
            "metrics_status": metrics_status,
            "sample_refs": r.get("sample_failures", []),
            "error": r.get("error"),
            "evidence_refs": r.get("evidence_refs", []),
        })

    test_run_id = state.get("test_run_id") or state.get("rule_run_id") or "test_run"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Xuất trace file
    try:
        settings = get_settings()
        base_dir = getattr(settings, "output_dir", None) or "./output"
        out_dir = Path(base_dir) / "test_runner"
        out_dir.mkdir(parents=True, exist_ok=True)
        dump_file = out_dir / f"debug_test_results_{timestamp}_{test_run_id}.json"
        dump_file.write_text(
            json.dumps(normalized_results, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        logger.info("Đã xuất trace test results ra: %s", dump_file)
    except Exception as exc:
        logger.warning("Không thể ghi file trace test results: %s", exc)

    return {
        "test_results": normalized_results,
        "metadata": {**state.get("metadata", {}), "dbt_execution_mode": "dbt" if dbt_executed else "legacy_sql_fallback"},
    }
# Standalone Test Harness (Chạy từ file output thực tế)
# ---------------------------------------------------------------------------

async def main():
    """Hàm chạy test độc lập cho test_runner_node từ file output validated_tests.

    Run: python -m src.agents.nodes.test_runner_node
    """
    import glob
    import os

    from src.services.rule_store import init_db

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    print("\n" + "=" * 75)
    print("🚀 CHẠY THỬ ĐỘC LẬP: test_runner_node (Thực thi test queries trên DB thật)")
    print("=" * 75)

    init_db()

    # Tìm file validated tests từ validate_sql hoặc test_generator
    search_patterns = [
        "output/validate_sql/debug_validated_tests_*.json",
        "output/test_generator/debug_generated_tests_*.json",
    ]
    files = []
    for pat in search_patterns:
        files.extend(glob.glob(pat))

    if not files:
        print("❌ Không tìm thấy file trong output/validate_sql/ hoặc output/test_generator/.")
        print("💡 Hãy chạy validate_sql_node trước: python -m src.agents.nodes.validate_sql_node")
        return

    latest_file = sorted(files, key=os.path.getmtime)[-1]
    print(f"📖 Đọc test queries từ: {latest_file}")

    with open(latest_file, encoding="utf-8") as f:
        tests = json.load(f)

    valid_tests = [t for t in tests if t.get("valid", True)]
    print(f"🎯 Tổng số queries hợp lệ sẽ thực thi trên DB: {len(valid_tests)}")

    state: AgentState = {
        "dataset_id": "yellow_tripdata",
        "test_run_id": "exec_standalone_test",
        "generated_tests": valid_tests,
        # Harness chạy tay bỏ qua chốt chặn dbt một cách CÓ CHỦ ĐÍCH: nó đọc thẳng file
        # test đã sinh sẵn. Thiếu cờ này, test_runner_node ném RuntimeError ngay dòng đầu
        # nên toàn bộ harness là code chết, không ai chạy được.
        "dbt_validation_valid": True,
    }

    res = await test_runner_node(state)

    # test_runner_node trả về status ĐÃ chuẩn hoá (PASS/FAIL), không phải PASSED/FAILED.
    results = res.get("test_results", [])
    passed_count = sum(1 for r in results if r["status"] == "PASS")
    failed_count = sum(1 for r in results if r["status"] == "FAIL")
    error_count = sum(1 for r in results if r["status"] == "ERROR")

    print(f"\n📊 Kết quả thực thi ({len(results)} rules): {passed_count} PASSED | {failed_count} FAILED | {error_count} ERROR")
    for idx, r in enumerate(results[:10], 1):
        status_icon = "PASS" if r["status"] == "PASS" else ("FAIL" if r["status"] == "FAIL" else "ERROR")
        print(f"\n[{idx}] Rule: {r['rule_id']} -> {status_icon}")
        print(f"    Tong dong: {r['checked_count']} | Vi pham: {r['failed_count']} | Ty le loi: {r['violation_rate']:.2%}")
        print(f"    Thoi gian: {r['duration_ms']} ms")
        if r.get("sample_refs"):
            print(f"    ID dong loi mau (toi da {SAMPLE_FAILURE_LIMIT}): {json.dumps(r['sample_refs'], ensure_ascii=False, default=str)}")

    if len(results) > 10:
        print(f"\n... và còn {len(results) - 10} kết quả khác được lưu trong file trace.")

    print("\n" + "=" * 75 + "\n")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())



