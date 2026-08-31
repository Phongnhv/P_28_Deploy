"""Opt-in, aggregate-only LLM telemetry for EvalGate and operations.

No prompt text, model response text, raw rows or credentials are written. The
callback is inert unless ``EVAL_TELEMETRY_PATH`` is explicitly configured.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

_LOCK = threading.Lock()


def _prompt_hash(prompts: list[str]) -> str:
    digest = hashlib.sha256()
    for prompt in prompts:
        digest.update(prompt.encode("utf-8", "replace"))
    return digest.hexdigest()


class EvalTelemetryCallback(BaseCallbackHandler):
    """Write redacted JSONL lifecycle events for model calls."""

    def __init__(self, *, provider: str, model: str) -> None:
        self.provider = provider
        self.model = model
        self._started: dict[str, float] = {}

    def _write(self, event: dict[str, Any]) -> None:
        target_value = os.getenv("EVAL_TELEMETRY_PATH", "")
        if not target_value:
            return
        target = Path(target_value)
        target.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "provider": self.provider,
            "model": self.model,
            **event,
        }
        with _LOCK:
            with target.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def on_llm_start(self, serialized, prompts, *, run_id, **kwargs) -> None:  # type: ignore[override]
        key = str(run_id)
        self._started[key] = time.perf_counter()
        self._write({
            "trace_id": key or uuid.uuid4().hex,
            "event": "llm_start",
            "prompt_hash": _prompt_hash([str(prompt) for prompt in prompts]),
            "prompt_count": len(prompts),
        })

    def on_llm_end(self, response, *, run_id, **kwargs) -> None:  # type: ignore[override]
        key = str(run_id)
        usage = getattr(response, "llm_output", None) or {}
        token_usage = usage.get("token_usage") or usage.get("usage") or {}
        input_tokens = token_usage.get("prompt_tokens") or token_usage.get("input_tokens")
        output_tokens = token_usage.get("completion_tokens") or token_usage.get("output_tokens")
        input_rate = float(os.getenv("EVAL_LLM_INPUT_USD_PER_MILLION", "0"))
        output_rate = float(os.getenv("EVAL_LLM_OUTPUT_USD_PER_MILLION", "0"))
        estimated_cost = (
            (float(input_tokens or 0) * input_rate)
            + (float(output_tokens or 0) * output_rate)
        ) / 1_000_000
        started = self._started.pop(key, None)
        self._write({
            "trace_id": key,
            "event": "llm_end",
            "latency_ms": None if started is None else round((time.perf_counter() - started) * 1000, 3),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": token_usage.get("total_tokens"),
            "estimated_cost_usd": round(estimated_cost, 8),
        })

    def on_llm_error(self, error, *, run_id, **kwargs) -> None:  # type: ignore[override]
        key = str(run_id)
        started = self._started.pop(key, None)
        self._write({
            "trace_id": key,
            "event": "llm_error",
            "error_type": type(error).__name__,
            "latency_ms": None if started is None else round((time.perf_counter() - started) * 1000, 3),
        })

    # -- tool lifecycle ----------------------------------------------------
    #
    # Which tools an agent reached for, and whether it reached for any before
    # asserting something, is the only observable record of *how* it decided. The
    # rest of this file describes model calls, which say what was asked and how much
    # it cost but nothing about whether a claim was checked against the data.
    #
    # Tool name and outcome only. Arguments and results are withheld deliberately:
    # a tool argument is a column name and a filter value, and a tool result is
    # rows -- exactly the raw data HG-S3 exists to keep inside the boundary.

    def on_tool_start(self, serialized, input_str, *, run_id, **kwargs) -> None:  # type: ignore[override]
        key = str(run_id)
        self._started[key] = time.perf_counter()
        name = (serialized or {}).get("name") if isinstance(serialized, dict) else None
        self._write({
            "trace_id": key or uuid.uuid4().hex,
            "event": "tool_start",
            "tool": name or "unknown",
            # Size, never content: enough to tell an empty call from a real one.
            "input_chars": len(str(input_str or "")),
        })

    def on_tool_end(self, output, *, run_id, **kwargs) -> None:  # type: ignore[override]
        key = str(run_id)
        started = self._started.pop(key, None)
        self._write({
            "trace_id": key,
            "event": "tool_end",
            "output_chars": len(str(output or "")),
            "latency_ms": None if started is None else round((time.perf_counter() - started) * 1000, 3),
        })

    def on_tool_error(self, error, *, run_id, **kwargs) -> None:  # type: ignore[override]
        key = str(run_id)
        started = self._started.pop(key, None)
        self._write({
            "trace_id": key,
            "event": "tool_error",
            "error_type": type(error).__name__,
            "latency_ms": None if started is None else round((time.perf_counter() - started) * 1000, 3),
        })
