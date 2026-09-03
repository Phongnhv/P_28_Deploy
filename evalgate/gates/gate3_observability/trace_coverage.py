"""Gate 3: is the run observable, and is the observability itself measured?

Two questions, and only the first one was ever asked:

    field completeness  do the events that were written carry their identity fields?
    instrumentation     did the nodes that ran write an event at all?

The first alone is a ratio over its own numerator. A system that instruments three nodes
out of thirty and stamps all three correctly scores 1.0 -- and that is precisely what
happened: the 02/09 bundle carried **six events covering four nodes, all from one graph**,
while ``src/agents/graph.py`` registers six graphs and dozens of nodes. Gate 3 published
100.00 and contributed a tenth of the release score for observing almost nothing.

So the denominator is read from the product's own graph definitions. ``add_node("x", ...)``
is parsed out of the source with the AST -- nothing is imported, so a broken product module
cannot take the gate down with it -- and compared against the node names the trace actually
contains. ``HG-O1`` blocks on the result, in the same family as ``HG-A9``: a gate that
guards the mechanism its own headline number depends on.
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path

from evalgate.core.context import EvalRunContext
from evalgate.normalizers import normalizers as norm
from evalgate.schemas.eval_result import (
    CostRecord,
    EvalResult,
    EvalStatus,
    Evidence,
    Finding,
    MetricValue,
    Severity,
    Threshold,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
GRAPH_MODULE = PROJECT_ROOT / "src" / "agents" / "graph.py"

GATE = "observability"
EVALUATOR = "trace_coverage_v1"

REQUIRED_FIELDS = {"trace_id", "workflow_run_id", "dataset_id", "event", "timestamp"}


def declared_nodes(module: Path = GRAPH_MODULE) -> dict[str, list[str]]:
    """Node names each ``build_*_graph`` registers, read statically.

    Returns ``{graph_function: [node, ...]}``. A node added through a variable rather
    than a literal is invisible here; that under-counts the denominator, which is the
    safe direction -- it can only make coverage look better than it is, never worse.
    """
    if not module.exists():
        return {}
    try:
        tree = ast.parse(module.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return {}

    graphs: dict[str, list[str]] = {}
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not function.name.startswith("build_"):
            continue
        names: list[str] = []
        for node in ast.walk(function):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_node"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                names.append(node.args[0].value)
        if names:
            graphs[function.name] = sorted(set(names))
    return graphs


def evaluate(
    *, write_evidence: bool = True, context: EvalRunContext | None = None
) -> EvalResult:
    trace_path = os.getenv("EVALGATE_TRACE_FILE", "")
    record = context.records("traces")[0] if context and context.records("traces") else None
    if not trace_path and record is None:
        return EvalResult(
            gate=GATE, evaluator=EVALUATOR, status=EvalStatus.NOT_MEASURED,
            metadata={"reason": "no traces artifact and EVALGATE_TRACE_FILE is not configured"},
        )

    total = complete = errors = 0
    latencies: list[float] = []
    estimated_cost = 0.0
    total_tokens = 0
    observed: set[str] = set()
    try:
        source = context.path_for(record) if record else Path(trace_path)
        for line in source.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            total += 1
            event = json.loads(line)
            required = REQUIRED_FIELDS | ({"node"} if event.get("event") == "node" else set())
            complete += int(required <= set(event))
            errors += int(event.get("event") == "error")
            if event.get("node"):
                observed.add(str(event["node"]))
            if event.get("latency_ms") is not None:
                latencies.append(float(event["latency_ms"]))
            estimated_cost += float(event.get("estimated_cost_usd") or 0.0)
            total_tokens += int(event.get("total_tokens") or 0)
    except (OSError, json.JSONDecodeError) as exc:
        return EvalResult(
            gate=GATE, evaluator=EVALUATOR, status=EvalStatus.NOT_EXECUTED,
            metadata={"reason": f"invalid trace: {exc}"},
        )

    field_coverage = complete / total if total else 0.0

    graphs = declared_nodes()
    declared = {name for names in graphs.values() for name in names}
    instrumented = observed & declared
    node_coverage = len(instrumented) / len(declared) if declared else None
    graphs_seen = sorted(
        function for function, names in graphs.items() if observed & set(names)
    )
    graph_coverage = len(graphs_seen) / len(graphs) if graphs else None

    p95 = norm.percentile(latencies, 0.95)

    findings: list[Finding] = []
    if node_coverage is not None and node_coverage < 0.5:
        silent = sorted(declared - observed)
        findings.append(
            Finding(
                id="HG-O1",
                severity=Severity.CRITICAL,
                title=(
                    f"{len(declared) - len(instrumented)}/{len(declared)} graph nodes "
                    "emitted no trace event"
                ),
                detail=(
                    f"trace_coverage reads {field_coverage:.0%} because it divides by the "
                    f"events that were written. Only {len(graphs_seen)}/{len(graphs)} graph(s) "
                    f"are instrumented at all. Silent nodes include: {silent[:8]}"
                ),
                root_cause_hint=(
                    "run_graph_streamed wraps one call site; the remaining graphs execute "
                    "without publishing to the node event broker"
                ),
                evidence_ref="evalgate/evidence/gate3/trace_coverage.json",
                blocks_release=True,
            )
        )

    evidence: list[Evidence] = []
    if write_evidence:
        target = PROJECT_ROOT / "evalgate" / "evidence" / "gate3" / "trace_coverage.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "events": total,
                    "field_complete": complete,
                    "nodes_declared": sorted(declared),
                    "nodes_observed": sorted(observed),
                    "nodes_instrumented": sorted(instrumented),
                    "nodes_silent": sorted(declared - observed),
                    "graphs_declared": {k: v for k, v in graphs.items()},
                    "graphs_instrumented": graphs_seen,
                },
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        evidence.append(Evidence(type="file", path=str(target.relative_to(PROJECT_ROOT))))

    healthy = (
        field_coverage >= 0.95
        and errors == 0
        and (node_coverage is None or node_coverage >= 0.5)
    )
    # The published score is the weaker of the two coverages. Reporting field
    # completeness alone is what let this gate score 100 while observing four nodes.
    score = norm.ratio(
        field_coverage if node_coverage is None else min(field_coverage, node_coverage)
    )

    return EvalResult(
        gate=GATE,
        evaluator=EVALUATOR,
        status=EvalStatus.PASS if healthy else EvalStatus.FAIL,
        score=score,
        metrics={
            "trace_coverage": MetricValue(
                raw=round(field_coverage, 4), unit="ratio", normalized=norm.ratio(field_coverage),
                note=f"{complete}/{total} events carry every identity field",
            ),
            "instrumented_node_coverage": MetricValue(
                raw=round(node_coverage, 4) if node_coverage is not None else None,
                unit="ratio",
                normalized=norm.ratio(node_coverage) if node_coverage is not None else None,
                status=None if node_coverage is not None else EvalStatus.NOT_MEASURED,
                note=(
                    f"{len(instrumented)}/{len(declared)} declared graph nodes emitted an event"
                    if node_coverage is not None
                    else "src/agents/graph.py declares no parseable add_node call"
                ),
            ),
            "instrumented_workflow_coverage": MetricValue(
                raw=round(graph_coverage, 4) if graph_coverage is not None else None,
                unit="ratio",
                normalized=norm.ratio(graph_coverage) if graph_coverage is not None else None,
                note=f"{len(graphs_seen)}/{len(graphs)} graph(s) produced any event",
            ),
            "trace_events_recorded": MetricValue(raw=total, unit="count", normalized=None),
            "critical_node_errors": MetricValue(
                raw=errors, unit="count", normalized=norm.zero_tolerance(errors)
            ),
            "trace_p95_latency_ms": MetricValue(
                raw=p95, unit="ms", normalized=norm.latency_band(p95)
            ),
            "llm_cost_usd": MetricValue(raw=estimated_cost, unit="usd", normalized=None),
        },
        thresholds={
            "trace_coverage": Threshold(**{"pass": 100.0, "warn": 95.0}),
            "instrumented_node_coverage": Threshold(**{"pass": 100.0, "warn": 50.0}),
        },
        evidence=evidence,
        critical_findings=findings,
        cost=CostRecord(llm_usd=estimated_cost, llm_tokens=total_tokens),
        metadata={
            "source": record.relative_path if record else trace_path,
            "events": total,
            "nodes_silent": sorted(declared - observed)[:20],
            "graphs_instrumented": graphs_seen,
        },
    )
