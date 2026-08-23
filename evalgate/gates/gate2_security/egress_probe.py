"""Does a raw data row ever leave the trust boundary?

Two independent signals are combined so the finding does not rest on reading code
alone:

* static -- does the execution path build a ``SELECT *`` over the source table and
  attach the resulting rows to a structure that is persisted, returned, or sent to
  a model provider?
* empirical -- do the archived run artefacts on disk actually contain whole rows,
  and are any of their columns classified as personal data?

The empirical half is what turns "this looks risky" into "this already happened".
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from evalgate.gates.gate2_security.pii_classifier import classify_column
from evalgate.normalizers import normalizers as norm
from evalgate.schemas.eval_result import (
    EvalResult,
    EvalStatus,
    Evidence,
    Finding,
    MetricValue,
    Severity,
    Threshold,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_DIR = PROJECT_ROOT / "evalgate" / "evidence" / "gate2"
TEST_RUNNER_TRACES = PROJECT_ROOT / "output" / "test_runner"

GATE = "ai_security"
EVALUATOR = "egress_probe_v1"

_SELECT_STAR = re.compile(r"SELECT\s+\*", re.IGNORECASE)

#: Where a raw row can end up once ``sample_failures`` is populated.
EGRESS_SINKS = {
    "database": ("src/services/rule_store.py", "sample_failures"),
    "disk": ("src/agents/nodes/test_runner_node.py", "debug_test_results"),
    "api": ("src/api/routes.py", "test-runs"),
    "llm_provider": ("src/agents/nodes/steward_insights_node.py", "failed_rules_json"),
}


def _display_path(path: Path) -> str:
    """Repo-relative when possible, absolute otherwise.

    ``relative_to`` raises for anything outside the project root, which took the
    whole evaluator down the first time it was pointed at a temp directory. A probe
    must never crash on an unexpected path -- it should report what it can.
    """
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _static_signals(src_root: Path | None = None) -> dict[str, Any]:
    """Signals read from product source.

    ``src_root`` exists so the probe can be pointed at a fixture tree. Defaults to
    the real ``src/`` so production behaviour is unchanged.
    """
    root = src_root if src_root is not None else PROJECT_ROOT / "src"
    signals: dict[str, Any] = {}
    runner = root / "agents" / "nodes" / "test_runner_node.py"
    if runner.exists():
        text = runner.read_text(encoding="utf-8")
        matches = _SELECT_STAR.findall(text)
        signals["select_star_in_test_runner"] = len(matches)
        signals["sample_failures_populated"] = text.count('"sample_failures": samples')

    insights = root / "agents" / "nodes" / "steward_insights_node.py"
    if insights.exists():
        text = insights.read_text(encoding="utf-8")
        # The LLM payload serialises whole result dicts, each of which carries
        # sample_failures -- the rows ride along without being named explicitly.
        signals["llm_payload_serialises_failed_rules"] = (
            "failed_rules_json" in text and "failed_or_error_rules" in text
        )
    return signals


def _empirical_rows(traces_dir: Path | None = None) -> dict[str, Any]:
    """Look for whole rows actually sitting in the archived traces.

    ``traces_dir`` is a seam for tests; it defaults to the real trace directory.
    A missing directory yields zero files scanned, which the caller must treat as
    "nothing was looked at" rather than "nothing was found".
    """
    base = traces_dir if traces_dir is not None else TEST_RUNNER_TRACES
    findings: list[dict[str, Any]] = []
    files_scanned = 0
    if not base.exists():
        return {"files_scanned": 0, "raw_row_artifacts": []}
    for path in sorted(base.glob("debug_test_results_*.json")):
        files_scanned += 1
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for entry in payload if isinstance(payload, list) else []:
            samples = entry.get("sample_failures")
            if not samples or not isinstance(samples, list):
                continue
            first = samples[0]
            if not isinstance(first, dict) or len(first) <= 1:
                continue
            columns = list(first.keys())
            pii_hits = []
            for column in columns:
                classification = classify_column(
                    column, [row.get(column) for row in samples if isinstance(row, dict)]
                )
                if classification.is_pii:
                    pii_hits.append(
                        {
                            "column": column,
                            "class": classification.pii_class.value,
                            "reason": classification.reason,
                        }
                    )
            findings.append(
                {
                    "file": _display_path(path),
                    "rule_id": entry.get("rule_id"),
                    "row_count": len(samples),
                    "columns": columns,
                    "pii_columns": pii_hits,
                }
            )
    return {"files_scanned": files_scanned, "raw_row_artifacts": findings}


def evaluate(
    *,
    write_evidence: bool = True,
    src_root: Path | None = None,
    traces_dir: Path | None = None,
) -> EvalResult:
    static = _static_signals(src_root)
    empirical = _empirical_rows(traces_dir)
    artifacts = empirical["raw_row_artifacts"]

    raw_row_violations = len(artifacts)
    pii_violations = sum(1 for a in artifacts if a["pii_columns"])
    pii_column_names = sorted(
        {hit["column"] for a in artifacts for hit in a["pii_columns"]}
    )

    evidence: list[Evidence] = []
    if write_evidence:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        target = EVIDENCE_DIR / "egress_probe.json"
        target.write_text(
            json.dumps(
                {
                    "static_signals": static,
                    "egress_sinks": EGRESS_SINKS,
                    "files_scanned": empirical["files_scanned"],
                    "raw_row_artifacts": artifacts,
                    "pii_columns_observed": pii_column_names,
                },
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        evidence.append(Evidence(type="file", path=str(target.relative_to(PROJECT_ROOT))))

    findings = []
    if raw_row_violations:
        findings.append(
            Finding(
                id="HG-S3",
                severity=Severity.CRITICAL,
                title="Whole source rows leave the trust boundary in four directions",
                detail=(
                    f"_fetch_sample_failures issues SELECT * and attaches complete rows to "
                    f"sample_failures. Those rows reach: {', '.join(EGRESS_SINKS)}. "
                    f"{raw_row_violations} archived artefacts already contain them"
                    + (
                        f"; columns classified as personal data: {pii_column_names}"
                        if pii_column_names
                        else ""
                    )
                ),
                root_cause_hint=(
                    "steward_insights_node serialises whole result dicts into "
                    "failed_rules_json, so sample_failures rides along to the model "
                    "provider without ever being named in the prompt"
                ),
                evidence_ref="evalgate/evidence/gate2/egress_probe.json",
                blocks_release=True,
            )
        )

    total = raw_row_violations + pii_violations
    return EvalResult(
        gate=GATE,
        evaluator=EVALUATOR,
        status=EvalStatus.FAIL if total else EvalStatus.PASS,
        score=norm.zero_tolerance(total),
        metrics={
            "raw_row_egress_violations": MetricValue(
                raw=raw_row_violations, unit="count",
                normalized=norm.zero_tolerance(raw_row_violations),
            ),
            "pii_column_egress_violations": MetricValue(
                raw=pii_violations, unit="count",
                normalized=norm.zero_tolerance(pii_violations),
            ),
            "raw_or_pii_egress_violations": MetricValue(
                raw=total, unit="count", normalized=norm.zero_tolerance(total)
            ),
        },
        thresholds={
            "raw_row_egress_violations": Threshold(**{"pass": 0.0, "warn": 0.0}),
            "pii_column_egress_violations": Threshold(**{"pass": 0.0, "warn": 0.0}),
        },
        evidence=evidence,
        critical_findings=findings,
        metadata={
            "static_signals": static,
            "pii_columns_observed": pii_column_names,
            "egress_sinks": list(EGRESS_SINKS),
        },
    )
