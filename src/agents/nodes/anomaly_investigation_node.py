"""LangGraph node backed by a Deep Agent for anomaly investigation."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from src.agents.nodes.templates import (
    ANOMALY_INVESTIGATION_SYSTEM_PROMPT,
    ANOMALY_INVESTIGATION_USER_PROMPT,
)
from src.agents.state import AnomalyGraphState
from src.agents.tools.anomaly_investigation_tools import scoped_investigation_tools
from src.config import get_settings
from src.models.rule_schemas import AnomalyInvestigationResponse
from src.services.llm import get_llm, telemetry_callbacks

# Ensure project root is in sys.path when executed directly as a script
_project_root = str(Path(__file__).resolve().parents[3])
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

try:
    from langchain.agents.middleware.todo import TodoListMiddleware
    from langchain.agents.middleware.tool_call_limit import ToolCallLimitMiddleware
except ImportError:
    try:
        from deepagents.middleware import TodoListMiddleware, ToolCallLimitMiddleware
    except ImportError:
        TodoListMiddleware = None
        ToolCallLimitMiddleware = None


def _message_content(result: Any) -> Any:
    if isinstance(result, AnomalyInvestigationResponse):
        return result
    if isinstance(result, dict):
        if "structured_response" in result and result["structured_response"] is not None:
            return result["structured_response"]
        messages = result.get("messages", [])
        if messages:
            last_msg = messages[-1]
            return getattr(last_msg, "content", last_msg)
    return result


def save_investigation_output(
    anomaly_run_id: str,
    output_data: dict[str, Any],
    output_dir: str | None = None,
) -> str:
    """Ghi kết quả điều tra của Deep Agent thành file JSON trong thư mục output/anomaly_investigation."""
    try:
        from datetime import datetime

        settings = get_settings()
        base_dir = Path(output_dir or getattr(settings, "output_dir", None) or "./output")
    except Exception:
        from datetime import datetime

        base_dir = Path(output_dir or "./output")

    target_dir = base_dir / "anomaly_investigation"
    target_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_name = f"investigation_result_{timestamp}_{anomaly_run_id}.json"
    file_path = target_dir / file_name

    file_path.write_text(
        json.dumps(output_data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return str(file_path)


async def anomaly_investigation_node(state: AnomalyGraphState) -> dict:
    """Investigate detector output while preserving the authoritative decision."""
    decision = state.get("anomaly_decision") or {}
    if decision.get("decision", "NORMAL") not in {"WATCH", "ANOMALY", "CRITICAL"}:
        return {"hypotheses": [], "hypothesis_status": "NOT_REQUIRED"}

    settings = get_settings()
    model = get_llm(settings.llm_provider, temperature=0.1)

    try:
        from deepagents import create_deep_agent

        if TodoListMiddleware is None or ToolCallLimitMiddleware is None:
            raise RuntimeError("DeepAgent middleware is unavailable; install compatible langchain/deepagents packages")
        middlewares = [
            TodoListMiddleware(),
            ToolCallLimitMiddleware(
                thread_limit=settings.anomaly_investigation_thread_tool_call_limit,
                run_limit=settings.anomaly_investigation_tool_call_limit,
                exit_behavior="continue",
            ),
        ]

        skill_path = str(Path(__file__).resolve().parents[1] / "skills" / "anomaly_investigator")

        agent = create_deep_agent(
            model=model,
            tools=scoped_investigation_tools(state),
            system_prompt=ANOMALY_INVESTIGATION_SYSTEM_PROMPT,
            response_format=AnomalyInvestigationResponse,
            middleware=middlewares,
            skills=[skill_path],
        )

        prompt = ANOMALY_INVESTIGATION_USER_PROMPT.format(
            anomaly_run_id=state.get("anomaly_run_id", ""),
            execution_run_id=state.get("execution_run_id", ""),
            dataset_id=state.get("dataset_id", ""),
            anomaly_decision=json.dumps(decision, ensure_ascii=False, default=str),
            signal_observations=json.dumps(state.get("signal_observations", []), ensure_ascii=False, default=str),
            current_features=json.dumps(state.get("current_features", {}), ensure_ascii=False, default=str),
            historical_features=json.dumps(state.get("historical_features", {}), ensure_ascii=False, default=str),
            prior_context=json.dumps(state.get("metadata", {}), ensure_ascii=False, default=str),
        )
        # Tool events are dispatched by the caller's callback manager, not the
        # model's, so the handlers are attached here as well. Otherwise the trace
        # shows every model call and no tool the agent actually used.
        # DeepAgent may perform several provider/tool turns and provider retry
        # policies can otherwise outlive the workflow's own deadline. Keep the
        # anomaly stage bounded so it can fall back to the deterministic,
        # citation-safe hypothesis path and still produce a report.
        agent_timeout_seconds = min(max(settings.llm_request_timeout_seconds, 5) * 2, 60)
        result = await asyncio.wait_for(
            agent.ainvoke(
                {"messages": [{"role": "user", "content": prompt}]},
                config={"callbacks": telemetry_callbacks()},
            ),
            timeout=agent_timeout_seconds,
        )
        content = _message_content(result)
        if isinstance(content, AnomalyInvestigationResponse):
            response = content
        elif isinstance(content, dict):
            response = AnomalyInvestigationResponse.model_validate(content)
        elif isinstance(content, str):
            try:
                response = AnomalyInvestigationResponse.model_validate_json(content)
            except Exception:
                response = AnomalyInvestigationResponse.model_validate(json.loads(content))
        else:
            response = AnomalyInvestigationResponse.model_validate(content)

        # Deterministic citation integrity: the model may not invent signal IDs.
        known_signal_ids = {
            str(item.get("signal_id"))
            for item in (state.get("signal_observations") or [])
            if isinstance(item, dict) and item.get("signal_id") is not None
        }
        for hypothesis in response.hypotheses:
            for field in ("supporting_signal_ids", "contradicting_signal_ids"):
                ids = getattr(hypothesis, field)
                unknown = [signal_id for signal_id in ids if str(signal_id) not in known_signal_ids]
                if unknown:
                    # Sanitize unknown IDs instead of failing
                    setattr(hypothesis, field, [i for i in ids if str(i) in known_signal_ids])

        anomaly_run_id = state.get("anomaly_run_id") or "anomaly_run"
        output_result = {
            "anomaly_run_id": anomaly_run_id,
            "execution_run_id": state.get("execution_run_id"),
            "dataset_id": state.get("dataset_id"),
            "anomaly_decision": decision,
            "hypothesis_status": "SUCCEEDED",
            "hypotheses": [item.model_dump() for item in response.hypotheses],
            "hypothesis_validation": response.model_dump(),
        }

        # Tự động lưu trace kết quả JSON vào thư mục output/anomaly_investigation
        try:
            saved_file_path = save_investigation_output(anomaly_run_id, output_result)
        except Exception:
            saved_file_path = ""

        return {
            "hypotheses": output_result["hypotheses"],
            "hypothesis_status": "SUCCEEDED",
            "hypothesis_validation": output_result["hypothesis_validation"],
            "metadata": {
                **(state.get("metadata") or {}),
                "investigation_trace_path": saved_file_path,
            },
        }

    except Exception as agent_exc:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(
            "DeepAgent anomaly investigation thất bại (%s). Tự động kích hoạt fallback sang steward_insights_node.",
            agent_exc,
            exc_info=True,
        )
        from src.agents.nodes.steward_insights_node import steward_insights_node

        fallback_result = await steward_insights_node(state)
        metadata = dict(fallback_result.get("metadata") or state.get("metadata") or {})
        metadata["deepagent_investigation_error"] = str(agent_exc)
        metadata["deepagent_fallback"] = True

        signal_errors = list(state.get("signal_errors") or [])
        signal_errors.append(f"DeepAgent investigation failed: {agent_exc}")

        return {
            **fallback_result,
            "hypothesis_status": "FALLBACK_FROM_DEEPAGENT",
            "deepagent_investigation_error": str(agent_exc),
            "signal_errors": signal_errors,
            "metadata": metadata,
        }



__all__ = ["AnomalyInvestigationResponse", "anomaly_investigation_node", "save_investigation_output"]


if __name__ == "__main__":
    import asyncio

    from dotenv import load_dotenv
    from sqlalchemy.orm import Session

    from src.models.database import AnomalyRunModel, AnomalySignalModel, DqResultModel, DqRunModel
    from src.services.rule_store import get_engine

    # Nạp biến môi trường từ .env (API Keys, LLM Provider, Database URL)
    load_dotenv()

    async def run_real_llm_test():
        print("=" * 75)
        print("🤖 CHẠY THỬ NGHIỆM DEEP AGENT (ANOMALY INVESTIGATION) VỚI DỮ LIỆU THẬT DATABASE")
        print("=" * 75)

        # 1. Truy vấn Anomaly Run thật mới nhất từ CSDL
        with Session(get_engine()) as db:
            anomaly_run = db.query(AnomalyRunModel).order_by(AnomalyRunModel.created_at.desc()).first()
            if not anomaly_run:
                print("❌ Không tìm thấy bản ghi AnomalyRun nào trong database. Vui lòng chạy pipeline Run 3 trước.")
                return

            anomaly_run_id = anomaly_run.id
            execution_run_id = anomaly_run.execution_run_id

            dq_run = db.get(DqRunModel, execution_run_id)
            dataset_id = dq_run.dataset_id if dq_run else "dataset-nyc-yellow-taxi-50k"

            signal_rows = db.query(AnomalySignalModel).filter_by(anomaly_run_id=anomaly_run_id).all()
            signals = [
                {
                    "signal_id": s.id,
                    "family": s.family,
                    "target_type": s.target_type,
                    "target_id": s.target_id,
                    "score": float(s.score),
                    "reliability": float(s.reliability),
                    "observed_value": s.observed_value,
                    "explanation_code": s.explanation_code,
                    "evidence_refs": json.loads(s.evidence_refs) if s.evidence_refs else [],
                }
                for s in signal_rows
            ]

            failed_rule_rows = (
                db.query(DqResultModel)
                .filter_by(run_id=execution_run_id)
                .filter(DqResultModel.status.in_(["FAIL", "FAILED", "ERROR"]))
                .all()
            )

        # Tạo State thật truyền vào Deep Agent
        real_state: AnomalyGraphState = {
            "anomaly_run_id": anomaly_run_id,
            "execution_run_id": execution_run_id,
            "dataset_id": dataset_id,
            "detector_config_version": anomaly_run.detector_config_version or "anomaly-v1",
            "anomaly_decision": {
                "decision": anomaly_run.decision or "ANOMALY",
                "score": float(anomaly_run.score or 0.8),
                "confidence": float(anomaly_run.confidence or 0.9),
                "severity": anomaly_run.severity or "HIGH",
                "override_reason": anomaly_run.error_message or "",
            },
            "signal_observations": signals,
            "current_features": {
                "failed_rules_count": len(failed_rule_rows),
                "total_checked": dq_run.total_checked if dq_run else 0,
                "total_failed": dq_run.total_failed if dq_run else len(failed_rule_rows),
            },
            "historical_features": {},
            "metadata": {
                "trigger_source": "real_db_test",
                "loaded_from_db": True,
            },
        }

        print("\n📥 [Dữ liệu thật nạp từ Database]:")
        print(f"  • Anomaly Run ID   : {real_state['anomaly_run_id']}")
        print(f"  • Execution Run ID : {real_state['execution_run_id']}")
        print(f"  • Dataset ID       : {real_state['dataset_id']}")
        print(
            f"  • Quyết định       : {real_state['anomaly_decision']['decision']} (Score: {real_state['anomaly_decision']['score']}, Severity: {real_state['anomaly_decision']['severity']})"
        )
        print(f"  • Số luật vi phạm  : {len(failed_rule_rows)} rules thất bại")
        print(f"  • Số tín hiệu nạp  : {len(signals)} signals từ database")

        high_signals = [s for s in signals if s.get("score", 0) >= 0.7][:5]
        if high_signals:
            print("\n  🔥 Top tín hiệu cảnh báo cao nhất:")
            for s in high_signals:
                print(f"    - [{s['signal_id']}] {s['target_id']}: {s['explanation_code']} (Score: {s['score']})")

        print("\n⏳ Đang khởi tạo Deep Agent và gọi LLM thật để điều tra...")
        try:
            result = await anomaly_investigation_node(real_state)

            print("\n" + "=" * 75)
            print("🎉 KẾT QUẢ ĐIỀU TRA TỪ DEEP AGENT (VỚI DỮ LIỆU THẬT & LLM THẬT)")
            print("=" * 75)
            print(f"• Trạng thái phân tích (status) : {result.get('hypothesis_status')}")

            validation = result.get("hypothesis_validation", {})
            if validation:
                print("\n📝 Đánh giá tổng thể (Overall Assessment):")
                print(f"  {validation.get('overall_assessment', 'N/A')}")
                print("\n🔍 Tóm tắt kết quả điều tra (Investigation Summary):")
                print(f"  {validation.get('investigation_summary', 'N/A')}")

            hypotheses = result.get("hypotheses", [])
            print(f"\n💡 Danh sách Giả thuyết Nguyên nhân do Deep Agent suy luận ({len(hypotheses)} hypotheses):")
            for idx, h in enumerate(hypotheses, 1):
                print(
                    f"\n  [{idx}] Loại nguyên nhân : {h.get('hypothesis_type')} (Độ tin cậy: {h.get('confidence', 0):.0%})"
                )
                print(f"      Tóm tắt           : {h.get('summary')}")
                print(f"      Tín hiệu ủng hộ   : {', '.join(h.get('supporting_signal_ids', [])) or 'None'}")
                print(f"      Bằng chứng chỉ dẫn: {', '.join(h.get('evidence_refs', [])) or 'None'}")
                print("      Hành động đề xuất (Recommended Checks):")
                for check in h.get("recommended_checks", []):
                    print(f"        • {check}")
                if h.get("missing_evidence"):
                    print(
                        f"      Bằng chứng còn thiếu: {', '.join(h.get('missing_evidence')) if isinstance(h.get('missing_evidence'), list) else h.get('missing_evidence')}"
                    )
                if h.get("limitations"):
                    print(
                        f"      Hạn chế / Rủi ro  : {', '.join(h.get('limitations')) if isinstance(h.get('limitations'), list) else h.get('limitations')}"
                    )

            trace_file = result.get("metadata", {}).get("investigation_trace_path")
            if trace_file:
                print(f"\n💾 File JSON kết quả đã được lưu tại:\n   👉 {trace_file}")

            print("\n" + "=" * 75)
            print("✅ TEST CHẠY THỰC TẾ VỚI DỮ LIỆU DATABASE HOÀN TẤT THÀNH CÔNG!")
            print("=" * 75 + "\n")

        except Exception as e:
            print(f"\n❌ LỖI TRONG QUÁ TRÌNH CHẠY: {e}")
            import traceback

            traceback.print_exc()

    asyncio.run(run_real_llm_test())
