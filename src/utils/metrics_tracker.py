"""Metrics & Token Tracker for LangGraph Agent workflows."""

from __future__ import annotations

import contextvars
import logging
import threading
import time
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

logger = logging.getLogger(__name__)

# Context variable to track active node name across async tasks
_current_node_cv: contextvars.ContextVar[str] = contextvars.ContextVar("current_node", default="general")


class MetricsTracker(BaseCallbackHandler):
    """Callback handler and execution monitor that tracks per-node time and LLM token usage."""

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        """Reset all collected metrics."""
        with self._lock:
            self.start_time: float = time.perf_counter()
            self.end_time: float | None = None
            self.node_timings: dict[str, float] = {}
            self.node_tokens: dict[str, dict[str, int]] = {}
            self.deepagent_runs: list[dict[str, Any]] = []

    def set_current_node(self, node_name: str) -> contextvars.Token:
        """Set the active node context for token attribution."""
        return _current_node_cv.set(node_name)

    def reset_current_node(self, token: contextvars.Token) -> None:
        """Reset the active node context."""
        _current_node_cv.reset(token)

    def record_node_time(self, node_name: str, duration_seconds: float) -> None:
        """Record execution duration for a given node."""
        with self._lock:
            self.node_timings[node_name] = self.node_timings.get(node_name, 0.0) + duration_seconds

    def record_deepagent_run(
        self,
        table_name: str,
        batch_index: int,
        duration_seconds: float,
        rules_count: int,
        mode: str = "deepagent",
        status: str = "SUCCESS",
        error: str | None = None,
    ) -> None:
        """Record fine-grained timing for individual DeepAgent table/batch runs."""
        with self._lock:
            self.deepagent_runs.append(
                {
                    "table_name": table_name,
                    "batch_index": batch_index,
                    "duration_seconds": duration_seconds,
                    "rules_count": rules_count,
                    "mode": mode,
                    "status": status,
                    "error": error,
                }
            )

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        """Capture token usage from LLM responses and attribute them to the current node."""
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0

        # 1. Check llm_output
        if response.llm_output and isinstance(response.llm_output, dict):
            usage = (
                response.llm_output.get("token_usage")
                or response.llm_output.get("usage")
                or response.llm_output.get("estimated_tokens")
                or {}
            )
            prompt_tokens += usage.get("prompt_tokens") or usage.get("input_tokens") or 0
            completion_tokens += usage.get("completion_tokens") or usage.get("output_tokens") or 0
            total_tokens += usage.get("total_tokens") or 0

        # 2. Check each generation's usage_metadata & response_metadata
        for gen_list in response.generations:
            for gen in gen_list:
                msg = getattr(gen, "message", None)
                if msg:
                    # LangChain Standard usage_metadata
                    usage_meta = getattr(msg, "usage_metadata", None)
                    if isinstance(usage_meta, dict):
                        prompt_tokens = max(prompt_tokens, usage_meta.get("input_tokens", 0))
                        completion_tokens = max(completion_tokens, usage_meta.get("output_tokens", 0))
                        total_tokens = max(total_tokens, usage_meta.get("total_tokens", 0))

                    # Provider response_metadata (OpenAI, Google, Anthropic, etc.)
                    resp_meta = getattr(msg, "response_metadata", {})
                    if isinstance(resp_meta, dict):
                        token_meta = (
                            resp_meta.get("token_usage")
                            or resp_meta.get("usage")
                            or resp_meta.get("usage_metadata")
                            or {}
                        )
                        if isinstance(token_meta, dict):
                            p = token_meta.get("prompt_tokens") or token_meta.get("input_tokens") or 0
                            c = token_meta.get("completion_tokens") or token_meta.get("output_tokens") or 0
                            t = token_meta.get("total_tokens") or 0
                            prompt_tokens = max(prompt_tokens, p)
                            completion_tokens = max(completion_tokens, c)
                            total_tokens = max(total_tokens, t)

                # Generation info check
                gen_info = getattr(gen, "generation_info", {})
                if isinstance(gen_info, dict):
                    token_meta = gen_info.get("usage") or gen_info.get("token_usage") or {}
                    if isinstance(token_meta, dict):
                        p = token_meta.get("prompt_tokens") or token_meta.get("input_tokens") or 0
                        c = token_meta.get("completion_tokens") or token_meta.get("output_tokens") or 0
                        t = token_meta.get("total_tokens") or 0
                        prompt_tokens = max(prompt_tokens, p)
                        completion_tokens = max(completion_tokens, c)
                        total_tokens = max(total_tokens, t)

        # 3. Check kwargs
        if "usage" in kwargs and isinstance(kwargs["usage"], dict):
            u = kwargs["usage"]
            prompt_tokens = max(prompt_tokens, u.get("input_tokens", 0) or u.get("prompt_tokens", 0))
            completion_tokens = max(completion_tokens, u.get("output_tokens", 0) or u.get("completion_tokens", 0))
            total_tokens = max(total_tokens, u.get("total_tokens", 0))

        if total_tokens == 0 and (prompt_tokens > 0 or completion_tokens > 0):
            total_tokens = prompt_tokens + completion_tokens

        node_name = _current_node_cv.get()

        with self._lock:
            if node_name not in self.node_tokens:
                self.node_tokens[node_name] = {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "llm_calls": 0,
                }

            self.node_tokens[node_name]["prompt_tokens"] += prompt_tokens
            self.node_tokens[node_name]["completion_tokens"] += completion_tokens
            self.node_tokens[node_name]["total_tokens"] += total_tokens
            self.node_tokens[node_name]["llm_calls"] += 1

    def finish(self) -> float:
        """Mark completion and return total elapsed seconds."""
        self.end_time = time.perf_counter()
        return self.end_time - self.start_time

    def get_summary(self) -> dict[str, Any]:
        """Generate a complete structured summary of execution time and tokens."""
        with self._lock:
            total_duration = (
                (self.end_time or time.perf_counter()) - self.start_time
            )

            total_prompt = sum(v["prompt_tokens"] for v in self.node_tokens.values())
            total_comp = sum(v["completion_tokens"] for v in self.node_tokens.values())
            total_tokens = sum(v["total_tokens"] for v in self.node_tokens.values())
            total_calls = sum(v["llm_calls"] for v in self.node_tokens.values())

            return {
                "total_duration_seconds": round(total_duration, 2),
                "total_tokens": {
                    "prompt_tokens": total_prompt,
                    "completion_tokens": total_comp,
                    "total_tokens": total_tokens,
                    "llm_calls": total_calls,
                },
                "node_timings": {k: round(v, 2) for k, v in self.node_timings.items()},
                "node_tokens": self.node_tokens,
                "deepagent_runs": self.deepagent_runs,
            }

    def print_report(self, title: str = "GRAPH 1 (PROPOSAL) EXECUTION & RESOURCE REPORT") -> None:
        """Print a formatted console report showing per-node timing and token breakdown."""
        summary = self.get_summary()
        total_time = summary["total_duration_seconds"] or 0.001
        tokens_info = summary["total_tokens"]
        deepagent_runs = summary["deepagent_runs"]

        all_nodes = set(self.node_timings.keys()) | set(self.node_tokens.keys())

        # Define preferred display order for Graph 1
        preferred_order = [
            "raw_profiler",
            "profiler_digest",
            "data_dictionary_generator",
            "dataset_understanding",
            "hitl_semantic_gate",
            "rule_candidate_builder",
            "prompt_customizer",
            "rule_proposer",
            "hitl_gate",
            "persist_rules",
        ]
        ordered_nodes = [n for n in preferred_order if n in all_nodes]
        remaining_nodes = [n for n in all_nodes if n not in ordered_nodes]
        display_nodes = ordered_nodes + sorted(remaining_nodes)

        border = "=" * 86
        sep = "-" * 86

        print("\n" + border)
        print(f"📊 {title}")
        print(border)
        print(f"⏱️  Tổng thời gian chạy:   {total_time:.2f}s ({total_time / 60:.2f} phút)")
        print(
            f"🔢 Tổng Tokens tiêu tốn:  {tokens_info['total_tokens']:,} "
            f"(Prompt: {tokens_info['prompt_tokens']:,} | Completion: {tokens_info['completion_tokens']:,})"
        )
        print(f"📞 Tổng số lần gọi LLM:   {tokens_info['llm_calls']} calls")
        print(sep)
        print(
            f"{'NODE NAME':<28} | {'TIME (s)':<10} | {'% TIME':<8} | {'TOKENS (In / Out)':<20} | {'LLM CALLS':<9}"
        )
        print(sep)

        for node in display_nodes:
            duration = self.node_timings.get(node, 0.0)
            pct = (duration / total_time) * 100.0 if total_time > 0 else 0.0
            t_data = self.node_tokens.get(node, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "llm_calls": 0})
            tokens_str = f"{t_data['prompt_tokens']:,} / {t_data['completion_tokens']:,}"
            calls_str = str(t_data["llm_calls"])

            # Highlight DeepAgent node
            node_label = f"⭐ {node}" if node == "rule_proposer" else f"  {node}"
            print(
                f"{node_label:<28} | {duration:>8.2f}s | {pct:>6.1f}% | {tokens_str:>20} | {calls_str:>9}"
            )

        print(sep)

        if deepagent_runs:
            print("🤖 CHI TIẾT CÁC LẦN CHẠY DEEPAGENT / BATCH:")
            for idx, r in enumerate(deepagent_runs, start=1):
                status_icon = "✅" if r["status"] == "SUCCESS" else "❌"
                print(
                    f"  {status_icon} [{r['table_name']}] Batch #{r['batch_index']} "
                    f"| Mode: {r['mode'].upper()} "
                    f"| Thời gian: {r['duration_seconds']:.2f}s "
                    f"| Số rules sinh ra: {r['rules_count']} "
                    f"| Trạng thái: {r['status']}"
                )
                if r.get("error"):
                    print(f"     ⚠️ Lỗi: {r['error']}")
            print(sep)

        print(border + "\n")


# Global singleton instance
_global_metrics_tracker = MetricsTracker()


def get_metrics_tracker() -> MetricsTracker:
    """Return the global MetricsTracker instance."""
    return _global_metrics_tracker
