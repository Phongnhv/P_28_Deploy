"""In-process node-event broker + streaming graph runner (Gap A fix).

The product graphs are executed with a blocking ``graph.ainvoke(...)`` inside
FastAPI ``BackgroundTasks`` or a daemon thread, so their per-node output never
leaves the LangGraph in-memory state.  This module adds a lightweight,
thread-safe publish/subscribe broker plus :func:`run_graph_streamed`, a drop-in
replacement for ``ainvoke`` that emits one event per node as the graph runs.

Design constraints (see rule_proposer_workflow.run_analysis_report):
- A publisher may run on a *different* event loop / thread than the SSE
  subscriber (Graph 3 runs under ``asyncio.run`` in a threadpool worker).  We
  therefore hand events to each subscriber's loop via ``call_soon_threadsafe``.
- A client may connect a moment *after* the run starts, so every stream keeps a
  bounded replay buffer that new subscribers receive immediately.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

#: Maximum number of events retained per stream for late-joining subscribers.
_MAX_BUFFER = 500

#: Keys whose values must never be streamed to a browser client.
_SENSITIVE_KEYS = {
    "connection_string", "database_url", "password", "password_hash", "api_key", "secret",
    "rows", "raw_rows", "raw_data", "dataframe", "sample_failures",
}

#: Maximum characters of any single previewed string/blob.
_PREVIEW_CHARS = 2000


class _Subscriber:
    __slots__ = ("loop", "queue")

    def __init__(self, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue[dict]) -> None:
        self.loop = loop
        self.queue = queue


class NodeEventBroker:
    """Thread-safe fan-out of node events keyed by an arbitrary ``stream_id``."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[str, list[_Subscriber]] = defaultdict(list)
        self._buffer: dict[str, deque[dict]] = defaultdict(lambda: deque(maxlen=_MAX_BUFFER))

    def publish(self, stream_id: str, event: dict[str, Any]) -> None:
        """Record ``event`` and push it to every live subscriber of ``stream_id``."""
        enriched = {**event, "ts": time.time()}
        with self._lock:
            self._buffer[stream_id].append(enriched)
            subscribers = list(self._subscribers.get(stream_id, ()))
        for sub in subscribers:
            try:
                sub.loop.call_soon_threadsafe(sub.queue.put_nowait, enriched)
            except RuntimeError:
                # Subscriber's loop is already closed; it will be cleaned up on unsubscribe.
                pass

    def subscribe(self, stream_id: str) -> tuple[_Subscriber, asyncio.Queue[dict], list[dict]]:
        """Register the calling coroutine's loop and return (handle, queue, backlog)."""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict] = asyncio.Queue()
        sub = _Subscriber(loop, queue)
        with self._lock:
            backlog = list(self._buffer.get(stream_id, ()))
            self._subscribers[stream_id].append(sub)
        return sub, queue, backlog

    def unsubscribe(self, stream_id: str, sub: _Subscriber) -> None:
        with self._lock:
            subs = self._subscribers.get(stream_id)
            if subs and sub in subs:
                subs.remove(sub)
            if subs is not None and not subs:
                self._subscribers.pop(stream_id, None)

    def reset(self, stream_id: str) -> None:
        """Drop the replay buffer for a stream (call before re-running a graph)."""
        with self._lock:
            self._buffer.pop(stream_id, None)


#: Process-wide singleton used by graph runners and the SSE endpoint.
broker = NodeEventBroker()


def _safe_preview(value: Any, _depth: int = 0) -> Any:
    """Return a JSON-safe, size-bounded, secret-stripped preview of node output."""
    if _depth > 4:
        return "…"
    if isinstance(value, dict):
        preview: dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and key.lower() in _SENSITIVE_KEYS:
                preview[key] = "***redacted***"
            else:
                preview[key] = _safe_preview(item, _depth + 1)
        return preview
    if isinstance(value, (list, tuple)):
        return [_safe_preview(item, _depth + 1) for item in value[:50]]
    if isinstance(value, (str, bytes)):
        text = value.decode("utf-8", "replace") if isinstance(value, bytes) else value
        return text[:_PREVIEW_CHARS] + ("…" if len(text) > _PREVIEW_CHARS else "")
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:_PREVIEW_CHARS]


async def run_graph_streamed(
    graph: Any,
    initial_state: dict[str, Any],
    stream_id: str,
) -> dict[str, Any]:
    """Run a compiled LangGraph while publishing per-node events; return final state.

    Behaviourally equivalent to ``await graph.ainvoke(initial_state)`` for callers
    (the merged final state is returned), but every node completion is published to
    :data:`broker` under ``stream_id`` as it happens.
    """
    broker.reset(stream_id)
    trace_id = uuid.uuid4().hex
    dataset_id = str(initial_state.get("dataset_id") or "unknown")

    def event(event_type: str, **extra: Any) -> dict[str, Any]:
        return {
            "type": event_type,
            "event": event_type,
            "trace_id": trace_id,
            "workflow_run_id": stream_id,
            "dataset_id": dataset_id,
            "timestamp": datetime.now(UTC).isoformat(),
            **extra,
        }

    broker.publish(stream_id, event("run_start", stream_id=stream_id))
    final_state: dict[str, Any] = dict(initial_state)
    try:
        # stream_mode="updates" yields {node_name: delta} per node; "values" yields the
        # full accumulated state so the caller receives an ainvoke-equivalent result.
        async for mode, chunk in graph.astream(initial_state, stream_mode=["updates", "values"]):
            if mode == "updates" and isinstance(chunk, dict):
                for node_name, delta in chunk.items():
                    broker.publish(stream_id, event(
                        "node",
                        node=node_name,
                        preview=_safe_preview(delta),
                    ))
            elif mode == "values" and isinstance(chunk, dict):
                final_state = chunk
    except Exception as exc:  # surface the failure to the client, then re-raise
        logger.error("Streamed graph run failed for stream_id=%s: %s", stream_id, exc, exc_info=True)
        broker.publish(stream_id, event("error", error_type=type(exc).__name__))
        broker.publish(stream_id, event("done", stream_id=stream_id))
        raise
    broker.publish(stream_id, event("done", stream_id=stream_id))
    return final_state
