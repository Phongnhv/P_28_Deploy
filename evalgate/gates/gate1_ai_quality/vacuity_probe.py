"""Can the rules the agent proposed ever fail?

This is the one question in Gate 1 that needs no ground truth at all, which makes
it the only one that still works when a user uploads a dataset nobody has ever
labelled.  A golden set calibrates the instrument before it touches user data; this
measures the rules actually produced *for that user's data*.

A rule is **vacuous** when its own parameters, checked against the data it guards,
make a violation impossible.  The clearest case is an allow-list built from the
values observed in the column it validates: every bad value present at profiling
time is admitted into its own allow-list, the rule reports zero violations forever,
and the report reads as a clean result rather than as a rule that cannot fire.

Vacuous is not the same as *currently satisfied*, and the difference decides which
rule types can be judged here at all:

    NOT_NULL on a column with no nulls   -> satisfied guard. Legitimate: it exists
                                            to catch a future regression.
    ACCEPTED_VALUES containing every
    observed value                        -> vacuous. It cannot catch anything, now
                                            or later, because the allow-list grows
                                            with whatever the data happens to hold.

Only rule types where "cannot fire" is provable from the parameters are judged.
The rest are counted as inspected-but-not-judgeable and reported as such, because
flagging a legitimate guard as dead weight would be worse than staying silent.

Structural mode is implemented and costs nothing.  Mutation mode -- corrupt a copy,
recompile the rule through the product's own compiler, and see whether it fires --
is the stronger test and is declared, not faked.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from evalgate.normalizers import normalizers as norm
from evalgate.schemas.eval_result import (
    DatasetBreakdown,
    EvalResult,
    EvalStatus,
    Evidence,
    Finding,
    MetricValue,
    Severity,
    Threshold,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PROPOSAL_DIRS = (
    PROJECT_ROOT / "output" / "hitl",
    PROJECT_ROOT / "output" / "rule_proposer",
)
DATASET_PARQUET = (
    PROJECT_ROOT / "data" / "yellow_tripdata_2025" / "semantic_data"
    / "yellow_tripdata_2025_semantic_50k.parquet"
)
EVIDENCE_DIR = PROJECT_ROOT / "evalgate" / "evidence" / "gate1"

GATE = "ai_quality"
EVALUATOR = "vacuity_probe_v1"

#: A rule type where more than this share of rules cannot fire is not unlucky --
#: the mechanism that produces that rule type is broken. Half is the point where
#: "some rules happen to be satisfied" stops being a credible explanation.
SYSTEMIC_VACUITY_THRESHOLD = 0.5

#: A row-count floor below this fraction of the observed count only fires on
#: near-total loss. Live, but close enough to useless that it is worth naming.
DEGENERATE_FLOOR_RATIO = 0.05

#: Why each unjudged type is unjudged. Written down so the gap is visible rather
#: than looking like an oversight.
NOT_JUDGED: dict[str, str] = {
    "NOT_NULL": "a column with no nulls is a satisfied guard, not a dead rule",
    "UNIQUE": "needs to know whether the column is a surrogate key; covered by a golden case",
    "REGEX_FORMAT": "would require running the pattern; possible, not yet implemented",
    "FRESHNESS": "time-dependent, so vacuity is not a property of the parameters alone",
    "CROSS_FIELD_COMPARISON": "relational; a violation depends on two columns at once",
}


@dataclass
class RuleVerdict:
    rule_id: str
    table_name: str | None
    column: str | None
    rule_type: str
    verdict: str  # VACUOUS | DEGENERATE | CAN_FIRE | NOT_JUDGED | NO_DATA
    reason: str


def _load_rules() -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    for directory in PROPOSAL_DIRS:
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            batch = payload.get("proposed_rules") if isinstance(payload, dict) else payload
            if isinstance(batch, list):
                rules.extend(r for r in batch if isinstance(r, dict))
    return rules


def _params(rule: dict[str, Any]) -> dict[str, Any]:
    return rule.get("effective_parameters") or rule.get("parameters") or {}


def judge_rule(rule: dict[str, Any], frame) -> RuleVerdict:
    """Decide whether one rule can ever report a violation on ``frame``."""
    import pandas as pd

    rule_type = str(rule.get("rule_type") or "")
    column = rule.get("column")
    base = {
        "rule_id": str(rule.get("rule_id") or ""),
        "table_name": rule.get("table_name"),
        "column": column,
        "rule_type": rule_type,
    }

    if rule_type in NOT_JUDGED:
        return RuleVerdict(**base, verdict="NOT_JUDGED", reason=NOT_JUDGED[rule_type])

    params = _params(rule)

    if rule_type == "ROW_COUNT":
        # A row-count floor below today's count is a *guard*, not a dead rule: it
        # exists to fire later, when rows go missing. Only a floor of zero (or none
        # at all) is genuinely unable to fire, because a count is never negative.
        #
        # A floor that is technically live but absurdly low -- 1 on a 50,000-row
        # table, which triggers only if the table empties completely -- is reported
        # separately as degenerate rather than being called vacuous, because the
        # difference is real and blurring it would overstate the finding.
        raw = params.get("min_row_count")
        minimum = int(raw) if isinstance(raw, (int, float)) else 0
        if raw is None or minimum <= 0:
            return RuleVerdict(
                **base, verdict="VACUOUS",
                reason=f"min_row_count={raw!r}: a row count is never below zero",
            )
        if len(frame) and minimum < max(1, int(len(frame) * DEGENERATE_FLOOR_RATIO)):
            return RuleVerdict(
                **base, verdict="DEGENERATE",
                reason=(
                    f"min_row_count={minimum} against {len(frame)} rows: fires only on "
                    "near-total data loss"
                ),
            )
        return RuleVerdict(**base, verdict="CAN_FIRE", reason=f"min_row_count={minimum}")

    if not column or column not in frame.columns:
        return RuleVerdict(
            **base, verdict="NO_DATA",
            reason=f"column {column!r} is not present in the dataset being checked",
        )

    series = frame[column]
    present = series.dropna()

    if rule_type == "ACCEPTED_VALUES":
        allowed = {str(v) for v in (params.get("accepted_values") or [])}
        if not allowed:
            return RuleVerdict(**base, verdict="CAN_FIRE", reason="empty allow-list")
        observed = {str(v) for v in present.unique()}
        missing = observed - allowed
        if not missing:
            return RuleVerdict(
                **base, verdict="VACUOUS",
                reason=(
                    f"allow-list of {len(allowed)} covers all {len(observed)} observed "
                    "values, so no row can violate it"
                ),
            )
        return RuleVerdict(
            **base, verdict="CAN_FIRE",
            reason=f"{len(missing)} observed value(s) fall outside the allow-list",
        )

    if rule_type == "RANGE":
        low, high = params.get("min"), params.get("max")
        if low is None and high is None:
            return RuleVerdict(**base, verdict="VACUOUS", reason="no bound is set")
        try:
            numeric = pd.to_numeric(present, errors="coerce").dropna()
        except (TypeError, ValueError):
            return RuleVerdict(**base, verdict="NO_DATA", reason="column is not numeric")
        if numeric.empty:
            return RuleVerdict(**base, verdict="NO_DATA", reason="no numeric value to compare")
        below = low is not None and float(low) > float(numeric.min())
        above = high is not None and float(high) < float(numeric.max())
        if not below and not above:
            return RuleVerdict(
                **base, verdict="VACUOUS",
                reason=(
                    f"bounds [{low}, {high}] already contain the observed range "
                    f"[{numeric.min():g}, {numeric.max():g}]"
                ),
            )
        return RuleVerdict(
            **base, verdict="CAN_FIRE",
            reason=f"observed range [{numeric.min():g}, {numeric.max():g}] crosses a bound",
        )

    if rule_type == "NULL_RATE":
        observed = float(series.isna().mean() * 100.0)
        threshold = float(params.get("max_null_pct", 5.0))
        if threshold >= observed:
            return RuleVerdict(
                **base, verdict="VACUOUS",
                reason=f"max_null_pct={threshold:g} is at or above the observed {observed:.2f}%",
            )
        return RuleVerdict(
            **base, verdict="CAN_FIRE",
            reason=f"observed {observed:.2f}% exceeds max_null_pct={threshold:g}",
        )

    return RuleVerdict(**base, verdict="NOT_JUDGED", reason="no vacuity rule for this type")


def judge_all(rules: list[dict[str, Any]], frame) -> list[RuleVerdict]:
    return [judge_rule(rule, frame) for rule in rules]


def evaluate(*, write_evidence: bool = True) -> EvalResult:
    try:
        import pandas as pd
    except ImportError:  # pragma: no cover - pandas is a hard dependency
        return EvalResult(
            gate=GATE, evaluator=EVALUATOR, status=EvalStatus.NOT_EXECUTED,
            metadata={"reason": "pandas is unavailable"},
        )

    if not DATASET_PARQUET.exists():
        return EvalResult(
            gate=GATE, evaluator=EVALUATOR,
            status=EvalStatus.BLOCKED_BY_SYSTEM_CAPABILITY,
            metadata={
                "reason": (
                    "the dataset the archived rules were proposed for is not present; "
                    f"expected {DATASET_PARQUET.relative_to(PROJECT_ROOT)}"
                )
            },
        )

    rules = _load_rules()
    if not rules:
        return EvalResult(
            gate=GATE, evaluator=EVALUATOR,
            status=EvalStatus.BLOCKED_BY_SYSTEM_CAPABILITY,
            metadata={"reason": "no archived agent proposals under output/"},
        )

    frame = pd.read_parquet(DATASET_PARQUET)
    verdicts = judge_all(rules, frame)

    judged = [v for v in verdicts if v.verdict in {"VACUOUS", "DEGENERATE", "CAN_FIRE"}]
    vacuous = [v for v in judged if v.verdict == "VACUOUS"]
    degenerate = [v for v in judged if v.verdict == "DEGENERATE"]
    no_data = [v for v in verdicts if v.verdict == "NO_DATA"]
    not_judged = [v for v in verdicts if v.verdict == "NOT_JUDGED"]

    by_type: dict[str, dict[str, int]] = defaultdict(lambda: {"judged": 0, "vacuous": 0})
    for verdict in judged:
        by_type[verdict.rule_type]["judged"] += 1
        if verdict.verdict == "VACUOUS":
            by_type[verdict.rule_type]["vacuous"] += 1

    rates = {
        rule_type: counts["vacuous"] / counts["judged"]
        for rule_type, counts in by_type.items()
        if counts["judged"]
    }
    worst_type = max(rates, key=lambda k: rates[k]) if rates else None
    worst_rate = rates.get(worst_type, 0.0) if worst_type else 0.0
    overall_rate = (len(vacuous) / len(judged)) if judged else 0.0
    systemic = sorted(t for t, r in rates.items() if r > SYSTEMIC_VACUITY_THRESHOLD)

    breakdown = [
        DatasetBreakdown(
            dataset_id=rule_type,
            status=(
                EvalStatus.FAIL if rates[rule_type] > SYSTEMIC_VACUITY_THRESHOLD
                else EvalStatus.WARN if rates[rule_type] > 0
                else EvalStatus.PASS
            ),
            score=norm.inverse_ratio(rates[rule_type]),
            reason=f"{by_type[rule_type]['vacuous']}/{by_type[rule_type]['judged']} cannot fire",
            metrics={"vacuity_rate": round(rates[rule_type], 4)},
        )
        for rule_type in sorted(rates)
    ]

    evidence: list[Evidence] = []
    if write_evidence:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        target = EVIDENCE_DIR / "vacuity_probe.json"
        target.write_text(
            json.dumps(
                {
                    "mode": "structural",
                    "dataset": str(DATASET_PARQUET.relative_to(PROJECT_ROOT)),
                    "rows": int(len(frame)),
                    "rules_loaded": len(rules),
                    "judged": len(judged),
                    "not_judged_reasons": NOT_JUDGED,
                    "by_type": {k: dict(v) for k, v in by_type.items()},
                    "vacuous": [asdict(v) for v in vacuous],
                    "degenerate": [asdict(v) for v in degenerate],
                    "no_data": [asdict(v) for v in no_data[:20]],
                },
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        evidence.append(Evidence(type="file", path=str(target.relative_to(PROJECT_ROOT))))

    findings: list[Finding] = []
    for rule_type in systemic:
        counts = by_type[rule_type]
        sample = next((v for v in vacuous if v.rule_type == rule_type), None)
        findings.append(
            Finding(
                id="HG-A6",
                severity=Severity.CRITICAL,
                title=f"{rule_type} rules are structurally unable to fail",
                detail=(
                    f"{counts['vacuous']}/{counts['judged']} ({rates[rule_type]:.1%}) of "
                    f"{rule_type} rules cannot report a violation on the data they guard. "
                    + (f"Example: {sample.rule_id} -- {sample.reason}" if sample else "")
                ),
                root_cause_hint=(
                    "the parameter is derived from the same column the rule validates, so "
                    "every value present at profiling time is admitted by the rule itself; "
                    "this is measurable without any ground truth and applies to any dataset"
                ),
                evidence_ref="evalgate/evidence/gate1/vacuity_probe.json",
                blocks_release=True,
            )
        )

    if not judged:
        return EvalResult(
            gate=GATE, evaluator=EVALUATOR, status=EvalStatus.NOT_MEASURED,
            evidence=evidence,
            metadata={"reason": "no rule was of a type whose vacuity can be decided"},
        )

    return EvalResult(
        gate=GATE,
        evaluator=EVALUATOR,
        status=EvalStatus.FAIL if findings else (
            EvalStatus.WARN if vacuous else EvalStatus.PASS
        ),
        score=norm.inverse_ratio(overall_rate),
        metrics={
            "vacuous_rule_rate": MetricValue(
                raw=round(overall_rate, 4), unit="ratio",
                normalized=norm.inverse_ratio(overall_rate),
                note=f"{len(vacuous)}/{len(judged)} judged rules cannot fire",
            ),
            "worst_type_vacuity_rate": MetricValue(
                raw=round(worst_rate, 4), unit="ratio",
                normalized=norm.inverse_ratio(worst_rate),
                note=f"worst rule type: {worst_type}",
            ),
            "systemic_vacuous_rule_types": MetricValue(
                raw=len(systemic), unit="count",
                normalized=norm.zero_tolerance(len(systemic)),
            ),
            "degenerate_threshold_rules": MetricValue(
                raw=len(degenerate), unit="count", normalized=None,
                note="live but fires only on near-total loss; a guard in name only",
            ),
            "rules_not_judgeable": MetricValue(
                raw=len(not_judged) + len(no_data), unit="count", normalized=None,
                note="type has no vacuity criterion, or the column is absent from the data",
            ),
        },
        per_dataset_breakdown=breakdown,
        thresholds={
            "vacuous_rule_rate": Threshold(**{"pass": 0.0, "warn": 0.1}),
            "systemic_vacuous_rule_types": Threshold(**{"pass": 0.0, "warn": 0.0}),
        },
        evidence=evidence,
        critical_findings=findings,
        metadata={
            "mode": "structural",
            "note": (
                "needs no ground truth, so it is the mechanism that still works for a "
                "dataset nobody has labelled"
            ),
            "mutation_mode": (
                "NOT_IMPLEMENTED: would corrupt a copy, recompile each rule through the "
                "product's own compiler and check whether it fires -- the only way to "
                "separate a dead rule from a satisfied guard for the unjudged types"
            ),
            "worst_type": worst_type,
            "systemic_types": systemic,
        },
    )
