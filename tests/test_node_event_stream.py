"""Tests for the node-event broker + streaming graph runner (Gap A / part 3.1)."""

import asyncio

import pytest
from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from src.services.node_event_stream import NodeEventBroker, broker, run_graph_streamed, _safe_preview


class _State(TypedDict, total=False):
    steps: list
    secret_connection_string: str
    connection_string: str


def _build_two_node_graph():
    def node_a(state):
        return {"steps": [*state.get("steps", []), "a"], "connection_string": "postgres://user:pw@host/db"}

    def node_b(state):
        return {"steps": [*state.get("steps", []), "b"]}

    g = StateGraph(_State)
    g.add_node("node_a", node_a)
    g.add_node("node_b", node_b)
    g.set_entry_point("node_a")
    g.add_edge("node_a", "node_b")
    g.add_edge("node_b", END)
    return g.compile()


@pytest.mark.asyncio
async def test_run_graph_streamed_emits_per_node_and_returns_final_state():
    graph = _build_two_node_graph()
    stream_id = "wf-test-1"

    # Subscribe first so we capture live events too.
    sub, queue, _backlog = broker.subscribe(stream_id)
    try:
        final_state = await run_graph_streamed(graph, {"steps": []}, stream_id)

        # ainvoke-equivalent final state.
        assert final_state.get("steps") == ["a", "b"]

        # Consume the queue exactly like the SSE endpoint: await until "done".
        received = []
        while True:
            event = await asyncio.wait_for(queue.get(), timeout=2.0)
            received.append(event)
            if event["type"] == "done":
                break
    finally:
        broker.unsubscribe(stream_id, sub)

    types = [e["type"] for e in received]
    assert types[0] == "run_start"
    assert types[-1] == "done"
    node_events = [e for e in received if e["type"] == "node"]
    assert [e["node"] for e in node_events] == ["node_a", "node_b"]

    # Secrets must be redacted in the streamed preview.
    a_event = next(e for e in node_events if e["node"] == "node_a")
    assert a_event["preview"]["connection_string"] == "***redacted***"


@pytest.mark.asyncio
async def test_late_subscriber_receives_replay_backlog():
    graph = _build_two_node_graph()
    stream_id = "wf-test-late"
    await run_graph_streamed(graph, {"steps": []}, stream_id)

    # Subscribe AFTER the run finished: backlog must contain the full history.
    sub, _queue, backlog = broker.subscribe(stream_id)
    try:
        types = [e["type"] for e in backlog]
        assert "run_start" in types and "done" in types
        assert [e["node"] for e in backlog if e["type"] == "node"] == ["node_a", "node_b"]
    finally:
        broker.unsubscribe(stream_id, sub)


def test_safe_preview_truncates_and_redacts():
    out = _safe_preview({"password": "hunter2", "big": "x" * 5000, "nested": {"api_key": "k"}})
    assert out["password"] == "***redacted***"
    assert out["nested"]["api_key"] == "***redacted***"
    assert len(out["big"]) <= 2001


@pytest.mark.asyncio
async def test_broker_reset_clears_buffer():
    b = NodeEventBroker()
    b.publish("s1", {"type": "node", "node": "x"})
    _sub, _q, backlog = b.subscribe("s1")
    assert len(backlog) == 1
    b.reset("s1")
    _sub2, _q2, backlog2 = b.subscribe("s1")
    assert backlog2 == []
