"""Node-level telemetry for the LangGraph agents.

Every node in ``src/agents/graph.py`` is wrapped with :func:`instrument`, which
writes one ``graph_node_runs`` row per execution: when the node started, how
long it took, whether it succeeded, and a redacted summary of what went in and
came out.

Two rules shape this module:

1. **Telemetry never breaks the graph.**  A failure to record is logged and
   swallowed.  The node's own exception, by contrast, is recorded and then
   re-raised untouched -- callers must still see real failures.

2. **Row values never land in the summary.**  :func:`summarize` keeps key names,
   container sizes and short scalars, and nothing else.  The platform's central
   privacy claim is that raw source rows stay out of the agent tier; a telemetry
   table that quietly mirrored node payloads would break exactly that claim.
"""

from __future__ import annotations

import functools
import inspect
import json
import logging
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from src.models.database import GraphNodeRunModel
from src.time_utils import utc_now

logger = logging.getLogger(__name__)

# Longest string kept verbatim in a summary.  Anything longer is truncated: a
# long string in agent state is far more likely to be a prompt or a rendered
# sample than a label worth storing whole.
MAX_STRING_LENGTH = 200

# Cap on how many keys of a dict are described, so a wide profile digest cannot
# produce an unbounded summary.
MAX_KEYS = 40

# How deep to descend before collapsing to a type name.
MAX_DEPTH = 3


@dataclass
class GraphRunContext:
    """Correlation ids shared by every node of one graph invocation."""

    graph_run_id: str
    workflow_run_id: str | None = None
    dataset_id: str | None = None
    dq_run_id: str | None = None
    anomaly_run_id: str | None = None
    sequence: int = field(default=0)

    def next_sequence(self) -> int:
        self.sequence += 1
        return self.sequence


_context: ContextVar[GraphRunContext | None] = ContextVar("graph_run_context", default=None)


def start_graph_run(
    *,
    workflow_run_id: str | None = None,
    dataset_id: str | None = None,
    dq_run_id: str | None = None,
    anomaly_run_id: str | None = None,
) -> GraphRunContext:
    """Open a correlation scope so the nodes of one invocation share an id.

    Callers that skip this still get telemetry -- :func:`instrument` falls back
    to a standalone context -- but the rows will not group into a single run.
    """
    context = GraphRunContext(
        graph_run_id=f"gr-{uuid.uuid4().hex[:16]}",
        workflow_run_id=workflow_run_id,
        dataset_id=dataset_id,
        dq_run_id=dq_run_id,
        anomaly_run_id=anomaly_run_id,
    )
    _context.set(context)
    return context


def current_graph_run() -> GraphRunContext | None:
    return _context.get()


def bind_run_ids(
    *,
    dq_run_id: str | None = None,
    anomaly_run_id: str | None = None,
    dataset_id: str | None = None,
) -> None:
    """Attach ids that only become known partway through a graph.

    Graph 2 mints its test-run id inside the first node, and Graph 3 the anomaly
    run id -- neither is available when the scope opens.
    """
    context = _context.get()
    if context is None:
        return
    if dq_run_id:
        context.dq_run_id = dq_run_id
    if anomaly_run_id:
        context.anomaly_run_id = anomaly_run_id
    if dataset_id:
        context.dataset_id = dataset_id


def summarize(payload: Any, *, depth: int = 0) -> Any:
    """Reduce a node payload to a shape safe to persist.

    Scalars survive (strings truncated), containers collapse to their size and
    -- for dicts -- their keys.  A list of records becomes ``{"count": n}``
    rather than its contents, which is what keeps source rows out of the table.
    """
    if payload is None or isinstance(payload, bool | int | float):
        return payload

    if isinstance(payload, str):
        if len(payload) <= MAX_STRING_LENGTH:
            return payload
        return f"{payload[:MAX_STRING_LENGTH]}… (+{len(payload) - MAX_STRING_LENGTH} chars)"

    if isinstance(payload, dict):
        if depth >= MAX_DEPTH:
            return {"type": "dict", "keys": len(payload)}
        summary: dict[str, Any] = {}
        for index, (key, value) in enumerate(payload.items()):
            if index >= MAX_KEYS:
                summary["…"] = f"+{len(payload) - MAX_KEYS} more keys"
                break
            summary[str(key)] = summarize(value, depth=depth + 1)
        return summary

    if isinstance(payload, list | tuple | set):
        items = list(payload)
        # A list of dicts is a record set.  Describe its shape, never its rows.
        if items and all(isinstance(item, dict) for item in items):
            keys: list[str] = []
            for key in items[0]:
                if key not in keys:
                    keys.append(str(key))
            return {"type": "records", "count": len(items), "fields": keys[:MAX_KEYS]}
        if depth >= MAX_DEPTH:
            return {"type": "list", "count": len(items)}
        return {
            "type": "list",
            "count": len(items),
            "sample": [summarize(item, depth=depth + 1) for item in items[:5]],
        }

    return {"type": type(payload).__name__}


def _dump(payload: Any) -> str:
    try:
        return json.dumps(summarize(payload), ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return json.dumps({"type": "unserialisable"})


def _model_name_from(payload: Any) -> str | None:
    """Best-effort read of which model a node used, for LLM nodes."""
    if not isinstance(payload, dict):
        return None
    metadata = payload.get("metadata")
    if isinstance(metadata, dict) and metadata.get("model_name"):
        return str(metadata["model_name"])[:128]
    if payload.get("model_name"):
        return str(payload["model_name"])[:128]
    return None


def configured_model_name() -> str | None:
    """The model this deployment is set up to call.

    LLM nodes do not echo the model back in their output, so every LLM node run
    was stored with model_name NULL and the UI had no way to show that a model
    was involved at all -- which reads as "the agent did not really run". This
    is the configured model, not a claim about a specific response.
    """
    try:
        from src.config import get_settings

        settings = get_settings()
        return {
            "openai": settings.openai_model_name,
            "anthropic": settings.anthropic_model_name,
            "mistral": settings.mistral_model_name,
            "google": settings.google_model_name,
        }.get(settings.llm_provider)
    except Exception:  # pragma: no cover - telemetry must never break a graph
        return None


def _session() -> Session:
    from src.services.rule_store import get_engine

    return Session(get_engine())


def _record_start(
    *,
    graph_key: str,
    node_name: str,
    node_kind: str,
    context: GraphRunContext,
    state: Any,
) -> str | None:
    row_id = f"nr-{uuid.uuid4().hex[:16]}"
    try:
        with _session() as db:
            db.add(
                GraphNodeRunModel(
                    id=row_id,
                    graph_run_id=context.graph_run_id,
                    graph_key=graph_key,
                    node_name=node_name,
                    node_kind=node_kind,
                    sequence=context.next_sequence(),
                    status="RUNNING",
                    started_at=utc_now(),
                    input_summary_json=_dump(state),
                    workflow_run_id=context.workflow_run_id,
                    dataset_id=context.dataset_id,
                    dq_run_id=context.dq_run_id,
                    anomaly_run_id=context.anomaly_run_id,
                )
            )
            db.commit()
        return row_id
    except Exception:  # pragma: no cover - telemetry must never break a graph
        logger.warning("Could not open node telemetry for %s.%s", graph_key, node_name, exc_info=True)
        return None


def _record_end(
    row_id: str | None,
    *,
    started: datetime,
    status: str,
    result: Any = None,
    error: str | None = None,
    context: GraphRunContext | None = None,
) -> None:
    if row_id is None:
        return
    duration_ms = max(int((utc_now() - started).total_seconds() * 1000), 0)
    try:
        with _session() as db:
            row = db.get(GraphNodeRunModel, row_id)
            if row is None:
                return
            row.status = status
            row.completed_at = utc_now()
            row.duration_ms = duration_ms
            row.error_message = error
            if result is not None:
                row.output_summary_json = _dump(result)
                row.model_name = _model_name_from(result)
            # A node that called a model but did not report which one still has
            # to say a model was involved.
            if row.model_name is None and row.node_kind == "LLM" and status == "SUCCEEDED":
                row.model_name = configured_model_name()
            # Ids minted inside the node only exist now.
            if context is not None:
                row.dq_run_id = context.dq_run_id
                row.anomaly_run_id = context.anomaly_run_id
                row.dataset_id = context.dataset_id
            db.commit()
    except Exception:  # pragma: no cover - telemetry must never break a graph
        logger.warning("Could not close node telemetry row %s", row_id, exc_info=True)


@contextmanager
def record_stage(graph_key: str, node_name: str, node_kind: str, inputs: Any = None):
    """Record one stage of a non-langgraph execution path as a node run.

    ``instrument`` wraps langgraph nodes; work that runs as plain Python — the
    bounded SQL executor behind "Run approved rules" — had no way to report
    itself, so its graph showed every node as "not run" beside results that
    plainly existed. Yield value is a dict the caller fills with whatever the
    stage produced; it becomes the node's output summary.
    """
    context = current_graph_run() or start_graph_run()
    started = utc_now()
    row_id = _record_start(
        graph_key=graph_key,
        node_name=node_name,
        node_kind=node_kind,
        context=context,
        state=inputs,
    )
    result: dict[str, Any] = {}
    try:
        yield result
    except Exception as exc:
        _record_end(row_id, started=started, status="FAILED", error=str(exc)[:2000], context=context)
        raise
    _record_end(row_id, started=started, status="SUCCEEDED", result=result or None, context=context)


def _publish_node(node_name: str, started, **extra) -> None:
    """Mirror one node run onto the live event broker.

    Imported lazily and wrapped: observability must never be the reason a product node
    fails. A broker that is unavailable costs a trace event, not a run.
    """
    try:
        from src.services.node_event_stream import publish_node_event

        latency_ms = round((utc_now() - started).total_seconds() * 1000, 2)
        publish_node_event(node_name, latency_ms=latency_ms, **extra)
    except Exception:  # noqa: BLE001
        return


def instrument(
    graph_key: str,
    node_name: str,
    node_kind: str,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Wrap a LangGraph node so its execution is recorded.

    The wrapper keeps the node's signature and return value intact, so builders
    only change at the ``add_node`` call site.  Sync and async nodes both work.

    It also publishes the node to the live event broker. Persisting a row and
    streaming an event are different audiences -- one is queried later, one is watched
    now -- but they answer to the same fact, and wiring only the first is what left
    fifteen of nineteen graph nodes invisible to the trace.
    """

    def decorator(node: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(node)
        async def wrapper(state: Any, *args: Any, **kwargs: Any) -> Any:
            context = _context.get()
            if context is None:
                # A graph invoked without an explicit scope (CLI, tests) still
                # gets rows; they simply group per node rather than per run.
                context = GraphRunContext(graph_run_id=f"gr-{uuid.uuid4().hex[:16]}")
                _context.set(context)
            started = utc_now()
            row_id = _record_start(
                graph_key=graph_key,
                node_name=node_name,
                node_kind=node_kind,
                context=context,
                state=state,
            )
            try:
                # Nodes are a mix of coroutine functions and plain callables
                # (rule_candidate_builder is synchronous, and tests substitute
                # sync stubs), so await only what is actually awaitable.
                result = node(state, *args, **kwargs)
                if inspect.isawaitable(result):
                    result = await result
            except Exception as exc:
                _record_end(
                    row_id,
                    started=started,
                    status="FAILED",
                    error=f"{type(exc).__name__}: {exc}"[:2000],
                    context=context,
                )
                _publish_node(node_name, started, status="FAILED",
                              error_type=type(exc).__name__)
                raise
            # A node that returns an ``error`` key has failed without raising --
            # the graphs route on that convention rather than on exceptions.
            failed = isinstance(result, dict) and bool(result.get("error"))
            _record_end(
                row_id,
                started=started,
                status="FAILED" if failed else "SUCCEEDED",
                result=result,
                error=str(result.get("error"))[:2000] if failed else None,
                context=context,
            )
            _publish_node(node_name, started, status="FAILED" if failed else "SUCCEEDED")
            return result

        return wrapper

    return decorator


def serialize_node_run(row: GraphNodeRunModel, *, include_payload: bool = False) -> dict[str, Any]:
    """Shape a row for the API.  Payload summaries are opt-in to keep lists small."""
    payload: dict[str, Any] = {
        "id": row.id,
        "graph_run_id": row.graph_run_id,
        "graph_key": row.graph_key,
        "node_name": row.node_name,
        "node_kind": row.node_kind,
        "sequence": row.sequence,
        "status": row.status,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "duration_ms": row.duration_ms,
        "error_message": row.error_message,
        "model_name": row.model_name,
        "workflow_run_id": row.workflow_run_id,
        "dataset_id": row.dataset_id,
        "dq_run_id": row.dq_run_id,
        "anomaly_run_id": row.anomaly_run_id,
    }
    if include_payload:
        payload["input_summary"] = json.loads(row.input_summary_json or "{}")
        payload["output_summary"] = json.loads(row.output_summary_json or "{}")
    return payload
