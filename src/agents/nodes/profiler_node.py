import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, inspect

from src.agents.state import AgentState
from src.agents.tools.db_profiler_tool import profile_database
from src.agents.tools.profile_digest import generate_profile_digest
from src.config import get_settings

logger = logging.getLogger(__name__)

# Giới hạn số bảng profile đồng thời để không đá vào constraint tài nguyên
# (mỗi lần profile mở 1 connection + có thể materialize temp table).
DEFAULT_MAX_CONCURRENT_TABLES = 4


async def _profile_one_table(connection_string: str, table: str, sampling_rate: float, semaphore: asyncio.Semaphore) -> tuple:
    """Profile 1 bảng, chạy trong semaphore để giới hạn concurrency.
    profile_database.invoke là hàm sync (langchain tool) -> chạy trong thread riêng để không block event loop.
    """
    async with semaphore:
        logger.info(f"Profiling table: {table}")
        try:
            profile_json_str = await asyncio.to_thread(
                profile_database.invoke,
                {"connection_string": connection_string, "table_name": table, "sampling_rate": sampling_rate},
            )
            profile_data = json.loads(profile_json_str)
            if "error" in profile_data:
                logger.warning(f"Lỗi khi profile bảng {table}: {profile_data['error']}")
            return table, profile_data
        except Exception as e:
            logger.error(f"Lỗi khi thực thi profiling bảng {table}: {str(e)}", exc_info=True)
            return table, {"error": f"Lỗi thực thi profiling: {str(e)}"}


async def _profile_all_tables(
    connection_string: str,
    tables: list,
    sampling_rate: float,
    max_concurrent_tables: int = DEFAULT_MAX_CONCURRENT_TABLES,
) -> dict:
    """Profile nhiều bảng song song, giới hạn bởi max_concurrent_tables."""
    semaphore = asyncio.Semaphore(max_concurrent_tables)
    results = await asyncio.gather(
        *[_profile_one_table(connection_string, table, sampling_rate, semaphore) for table in tables]
    )
    return dict(results)


async def raw_profiler_node(state: AgentState) -> dict:
    """Profiler Sub-Agent Node.

    Quét toàn bộ các bảng trong database và tổng hợp kết quả thống kê.
    """
    metadata = state.get("metadata", {})
    connection_string = metadata.get("connection_string")
    sampling_rate = metadata.get("sampling_rate", 1.0)
    max_concurrent_tables = metadata.get("max_concurrent_tables", DEFAULT_MAX_CONCURRENT_TABLES)

    # Lấy database_url mặc định nếu không có connection_string cụ thể
    if not connection_string:
        settings = get_settings()
        connection_string = settings.database_url

    if not connection_string:
        return {"error": "Không tìm thấy connection_string trong metadata hoặc cấu hình."}

    logger.info(f"Bắt đầu profile database với sampling_rate: {sampling_rate}")

    # Tương thích với SQLAlchemy 2.0+ khi nhận url postgresql://
    if connection_string.startswith("postgresql://"):
        connection_string = connection_string.replace("postgresql://", "postgresql+psycopg2://", 1)

    # Danh sách các bảng hệ thống/metadata của RidePulse DQ cần bỏ qua
    system_tables = {
        "proposal_runs",
        "proposed_rules",
        "active_rules",
        "test_runs",
        "test_results",
        "alembic_version",
        "sessions",
        "audit_events",
        "datasets",
        "dataset_access",
        "user_accounts",
        "profiles",
        "column_profiles",
        "rule_proposals",
        "rule_versions",
        "rule_configurations",
        "jobs",
        "dq_runs",
        "dq_results",
    }

    # 1. Quét danh sách các bảng trong database (lọc bỏ bảng hệ thống)
    try:
        from src.services.rule_store import get_engine
        settings = get_settings()
        if connection_string == settings.database_url:
            engine = get_engine()
            inspector = inspect(engine)
            all_tables = inspector.get_table_names()
            tables = [t for t in all_tables if t.lower() == "source_rows"]
        else:
            engine = create_engine(connection_string)
            try:
                with engine.connect():
                    inspector = inspect(engine)
                    all_tables = inspector.get_table_names()
                    tables = [t for t in all_tables if t.lower() == "source_rows"]
            finally:
                engine.dispose()
    except Exception as e:
        logger.error(f"Lỗi khi kết nối database hoặc lấy danh sách bảng: {str(e)}", exc_info=True)
        return {"error": f"Lỗi khi kết nối database hoặc lấy danh sách bảng: {str(e)}"}

    if not tables:
        logger.warning("Không tìm thấy bảng nghiệp vụ nào trong database (đã bỏ qua bảng hệ thống: %s).", system_tables)
        return {"dataset_profile": {}}

    # 2. Profile song song (có giới hạn concurrency) và tổng hợp
    aggregated_profile = await _profile_all_tables(connection_string, tables, sampling_rate, max_concurrent_tables)

    # Xuất trace JSON sau khi profile xong
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_id = state.get("rule_run_id") or "test_run"
    try:
        settings = get_settings()
        out_dir = getattr(settings, "output_dir", None)
        res_dir = getattr(settings, "results_dir", None)
        base_dir = out_dir if isinstance(out_dir, (str, Path)) else (res_dir if isinstance(res_dir, (str, Path)) else "./output")
        profiler_dir = Path(base_dir) / "profiler"
        profiler_dir.mkdir(parents=True, exist_ok=True)
        dump_file = profiler_dir / f"debug_raw_profile_{timestamp}_{run_id}.json"
        dump_file.write_text(json.dumps(aggregated_profile, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"Đã xuất trace raw profile ra {dump_file}")
    except Exception as e:
        logger.warning(f"Không thể ghi file trace raw profile: {e}")

    # Nếu TẤT CẢ các bảng đều lỗi, coi như node này thất bại để graph có thể dừng/route
    # sang nhánh xử lý lỗi thay vì âm thầm đi tiếp với profile rỗng.
    if aggregated_profile and all("error" in v for v in aggregated_profile.values()):
        return {
            "dataset_profile": aggregated_profile,
            "error": "Profiling thất bại trên toàn bộ các bảng, xem chi tiết trong dataset_profile.",
        }

    return {"dataset_profile": aggregated_profile}


async def profiler_digest_node(state: AgentState) -> dict:
    """Sinh digest từ dataset_profile.

    Nếu bước profiling trước đó đã lỗi (state["error"] có giá trị) hoặc không có dataset_profile,
    node này KHÔNG tự ý sinh digest rỗng một cách im lặng -> trả lỗi rõ ràng để graph route đúng nhánh,
    tránh việc lỗi thật bị "nuốt" thành digest = {}.
    """
    if state.get("error"):
        logger.warning("Bỏ qua sinh digest vì bước profiling trước đó đã lỗi: %s", state["error"])
        return {"dataset_profile_digest": {}, "error": state["error"]}

    dataset_profile = state.get("dataset_profile")
    if not dataset_profile:
        msg = "Không có dataset_profile để tạo digest (dataset_profile rỗng hoặc chưa được set)."
        logger.warning(msg)
        return {"dataset_profile_digest": {}, "error": msg}

    digest = generate_profile_digest(dataset_profile)

    # Xuất trace JSON sau khi sinh digest xong
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_id = state.get("rule_run_id") or "test_run"
    try:
        settings = get_settings()
        out_dir = getattr(settings, "output_dir", None)
        res_dir = getattr(settings, "results_dir", None)
        base_dir = out_dir if isinstance(out_dir, (str, Path)) else (res_dir if isinstance(res_dir, (str, Path)) else "./output")
        profiler_dir = Path(base_dir) / "profiler"
        profiler_dir.mkdir(parents=True, exist_ok=True)
        dump_file = profiler_dir / f"debug_profile_digest_{timestamp}_{run_id}.json"
        dump_file.write_text(json.dumps(digest, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"Đã xuất trace profile digest ra {dump_file}")
    except Exception as e:
        logger.warning(f"Không thể ghi file trace profile digest: {e}")

    return {"dataset_profile_digest": digest}


# ===================================================
# Test Profiler Node
# ===================================================
async def main():
    # Thiết lập workflow state giả lập
    state = {
        "metadata": {
            "connection_string": "postgresql+psycopg2://postgres:admin@localhost:5433/taxidb",  # Hardcode để thử nghiệm
            "sampling_rate": 1,  # Tỷ lệ lấy mẫu 5% để chạy nhanh. Bạn có thể đổi thành 1.0 (100%). Đã set về 1.0 để test chuẩn nhất
        }
    }

    try:
        print("Profiling database...")
        raw_result = await raw_profiler_node(state)
        state.update(raw_result)
        print("Hoàn thành raw profiling.")
    except Exception as e:
        logger.error(f"Lỗi khi thực thi raw_profiler_node: {str(e)}", exc_info=True)
        print(f"Lỗi khi thực thi raw_profiler_node: {str(e)}")

    try:
        print("Profiling digest...")
        await profiler_digest_node(state)
        print("Hoàn thành profiler digest.")
    except Exception as e:
        logger.error(f"Lỗi khi thực thi profiler_digest_node: {str(e)}", exc_info=True)
        print(f"Lỗi khi thực thi profiler_digest_node: {str(e)}")


if __name__ == "__main__":
    asyncio.run(main())

    # Run test syntax: python -m src.agents.nodes.profiler_node
