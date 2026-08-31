"""Does the agent learn its allow-list from policy, or from the dirty data?

An ACCEPTED_VALUES rule whose enum is derived from the values observed in the very
column it validates can never fail: every bad value present at profiling time is
admitted into its own allow-list.  The rule then reports zero violations, and the
report reads as a clean result rather than as a rule that cannot fire.

This project makes the failure measurable for free.  ``docs/SUPABASE_DATASET_CONTRACT.md``
publishes the governed ``payment_type`` domain and states that one literal sits
outside it *deliberately*, "so the agent and runner can surface the four known
invalid sample rows".  That is a labelled ground truth, written down before any
evaluation existed, and completely independent of SDIH's synthetic injection.

Two independent ground truths agreeing on a defect is a much stronger claim than
either alone, which is why this evaluator exists next to the replay scorer rather
than instead of it.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from evalgate.core.context import EvalRunContext
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
POLICY_JSON = PROJECT_ROOT / "src" / "resources" / "rule_policies.json"
CONTRACT_DOC = PROJECT_ROOT / "docs" / "SUPABASE_DATASET_CONTRACT.md"
PROPOSAL_DIRS = (
    PROJECT_ROOT / "output" / "hitl",
    PROJECT_ROOT / "output" / "rule_proposer",
)
REPORTS_DIR = PROJECT_ROOT / "output" / "reports"
EVIDENCE_DIR = PROJECT_ROOT / "evalgate" / "evidence" / "gate1"

GATE = "ai_quality"
EVALUATOR = "governed_enum_conformance_v1"

#: The system builds ACCEPTED_VALUES through two different paths, and they behave in
#: opposite ways. Saying which one a finding is about is the difference between a
#: precise result and an overstated one.
#:
#:   dashboard  dashboard_agent_workflow.py:571-584 reads policy.governed_value_sets
#:              -> policy-driven, correct
#:   agent      rule_proposer_node.py:127 reads the observed column values
#:              -> learned from the data it validates, tautological
#:
#: The archived artefacts under output/ come from the agent path only.
_PATH_SCOPE_NOTE = (
    "Scope: the agent path (Run 1, rule_proposer_node). The dashboard path builds the "
    "same rule type from policy.governed_value_sets and is not implicated."
)

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


@dataclass
class GovernedDomain:
    column: str
    allowed: list[str]
    excluded: list[str]
    expected_defects: int
    source: str


@dataclass
class RuleOutcome:
    artifact: str
    column: str
    proposed: list[str]
    unauthorised: list[str]
    admitted_excluded: list[str]
    missing_from_policy: list[str]


def load_governed_domains() -> list[GovernedDomain]:
    """Every governed column, preferring the policy file over the contract document.

    Reading *all* governed columns matters: an evaluator hard-coded to one column
    reports a floor rather than a count, and a floor invites the reader to treat it
    as the total.

    The document fallback is deliberate rather than lazy. ``src/resources/rule_policies.json``
    is currently missing, and a governance evaluator that goes silent exactly when a
    governance asset disappears would be useless at the only moment it is needed.
    The source actually used is recorded in the result so the reading is never
    ambiguous.
    """
    if POLICY_JSON.exists():
        try:
            document = json.loads(POLICY_JSON.read_text(encoding="utf-8"))
            domains = [
                GovernedDomain(
                    column=column,
                    allowed=list(values),
                    excluded=[],
                    expected_defects=0,
                    source="src/resources/rule_policies.json",
                )
                for dataset in document.get("datasets", {}).values()
                for column, values in (dataset.get("governed_value_sets") or {}).items()
            ]
            if domains:
                # The policy file is authoritative for `allowed`, but it carries no
                # notion of a value left out on purpose. `excluded` and the planted
                # defect count live only in the contract prose, and dropping them
                # silently disables planted_defect_recall -- which is exactly what
                # happened the moment the policy file was restored on 2026-08-22.
                # Merge rather than choose: each source supplies what only it knows.
                from_contract = {d.column: d for d in _domains_from_contract()}
                merged: list[GovernedDomain] = []
                for domain in domains:
                    prose = from_contract.get(domain.column)
                    merged.append(
                        GovernedDomain(
                            column=domain.column,
                            allowed=domain.allowed,
                            excluded=list(prose.excluded) if prose else [],
                            expected_defects=prose.expected_defects if prose else 0,
                            source=(
                                "src/resources/rule_policies.json + "
                                + CONTRACT_DOC.name
                                if prose
                                else "src/resources/rule_policies.json"
                            ),
                        )
                    )
                return merged
        except (OSError, ValueError, AttributeError):
            pass

    return _domains_from_contract()


def _domains_from_contract() -> list[GovernedDomain]:
    """Governed columns as documented in the contract prose.

    The contract is the only place that records which values are *deliberately*
    excluded, and how many planted defects that implies. The policy JSON lists what
    is allowed but says nothing about what was left out on purpose.
    """
    if not CONTRACT_DOC.exists():
        return []
    text = CONTRACT_DOC.read_text(encoding="utf-8")
    section = re.search(
        r"##\s*Representation policy(.*?)(?=\n##\s|\Z)", text, re.DOTALL
    )
    if not section:
        return []
    body = section.group(1)
    allowed = re.findall(r"^\s*-\s*`([^`]+)`\s*$", body, re.MULTILINE)
    excluded = re.findall(r"`([^`]+)`\s+is deliberately outside", body)
    count_match = re.search(r"the\s+(\w+)\s+known invalid sample rows", body)
    expected = 0
    if count_match:
        token = count_match.group(1).lower()
        expected = _NUMBER_WORDS.get(token, int(token) if token.isdigit() else 0)
    if not allowed:
        return []
    # The prose section documents exactly one governed column today.
    column = re.search(r"accepts these\s+`([a-z_]+)`\s+values", body)
    return [
        GovernedDomain(
            column=column.group(1) if column else "payment_type",
            allowed=allowed,
            excluded=excluded,
            expected_defects=expected,
            source="docs/SUPABASE_DATASET_CONTRACT.md#representation-policy",
        )
    ]


def _load_proposals(context: EvalRunContext | None = None) -> list[tuple[str, list[dict]]]:
    artifacts: list[tuple[str, list[dict]]] = []
    if context is not None:
        for record in context.records("proposals"):
            payload = json.loads(context.path_for(record).read_text(encoding="utf-8"))
            rules = payload.get("proposed_rules") if isinstance(payload, dict) else payload
            if isinstance(rules, list) and rules:
                artifacts.append((record.relative_path, rules))
    return artifacts


def score_proposals(domain: GovernedDomain, context: EvalRunContext | None = None) -> list[RuleOutcome]:
    outcomes: list[RuleOutcome] = []
    allowed = set(domain.allowed)
    excluded = set(domain.excluded)
    for artifact, rules in _load_proposals(context):
        for rule in rules:
            if rule.get("rule_type") != "ACCEPTED_VALUES":
                continue
            if rule.get("column") != domain.column:
                continue
            params = rule.get("effective_parameters") or rule.get("parameters") or {}
            proposed = [str(v) for v in (params.get("accepted_values") or [])]
            outcomes.append(
                RuleOutcome(
                    artifact=artifact,
                    column=domain.column,
                    proposed=proposed,
                    unauthorised=sorted(set(proposed) - allowed),
                    admitted_excluded=sorted(set(proposed) & excluded),
                    missing_from_policy=sorted(allowed - set(proposed)),
                )
            )
    return outcomes


def measure_planted_recall(domain: GovernedDomain, context: EvalRunContext | None = None) -> tuple[int | None, list[str]]:
    """How many of the deliberately planted invalid rows did execution actually flag?"""
    if context is None:
        return None, []
    best: int | None = None
    seen: list[str] = []
    for record in context.records("execution-results"):
        payload = json.loads(context.path_for(record).read_text(encoding="utf-8"))
        for entry in payload.get("test_results", []):
            rule_id = str(entry.get("rule_id", ""))
            if domain.column not in rule_id or "ACCEPTED_VALUES" not in rule_id:
                continue
            flagged = int(
                entry.get("failed_count") or entry.get("violation_count") or 0
            )
            seen.append(f"{record.name}: {rule_id} flagged {flagged}")
            best = flagged if best is None else max(best, flagged)
    return best, seen


def count_unbacked_enums(governed_columns: set[str], context: EvalRunContext | None = None) -> list[str]:
    """ACCEPTED_VALUES rules on columns no policy governs.

    These are tautological by construction: with no external domain to check
    against, the allow-list can only have come from the data being validated. The
    count is reported rather than gated, because a column may legitimately have no
    policy yet -- but it is the clearest single signal of enum-learning behaviour.
    """
    unbacked: list[str] = []
    for artifact, rules in _load_proposals(context):
        for rule in rules:
            if rule.get("rule_type") != "ACCEPTED_VALUES":
                continue
            column = rule.get("column")
            if column and column not in governed_columns:
                unbacked.append(f"{artifact}::{rule.get('table_name', '?')}.{column}")
    return sorted(set(unbacked))


def evaluate(*, write_evidence: bool = True, context: EvalRunContext | None = None) -> EvalResult:
    domains = load_governed_domains()
    if not domains:
        return EvalResult(
            gate=GATE,
            evaluator=EVALUATOR,
            status=EvalStatus.BLOCKED_MISSING_GROUND_TRUTH,
            metadata={
                "reason": "no governed value set found in the policy file or the contract document"
            },
        )

    per_domain: list[dict] = []
    breakdown: list[DatasetBreakdown] = []
    findings: list[Finding] = []
    all_tautological = 0
    conformances: list[float] = []
    recalls: list[float] = []
    uncovered_columns: list[str] = []

    for domain in domains:
        outcomes = score_proposals(domain, context)
        if not outcomes:
            # Reported as a gap in the product, not as a gap in the measurement.
            # See the governed_column_coverage metric below for why the difference
            # decides whether this evaluator can be evaded.
            uncovered_columns.append(domain.column)
            breakdown.append(
                DatasetBreakdown(
                    dataset_id=domain.column,
                    status=EvalStatus.FAIL,
                    score=0.0,
                    reason="no ACCEPTED_VALUES rule was proposed for this governed column",
                )
            )
            per_domain.append({"domain": asdict(domain), "outcomes": [], "flagged": None})
            continue

        tautological = [o for o in outcomes if o.admitted_excluded]
        all_tautological += len(tautological)
        conformance = min(
            len(set(o.proposed) & set(domain.allowed)) / len(domain.allowed)
            for o in outcomes
        )
        conformances.append(conformance)

        flagged, flagged_detail = measure_planted_recall(domain, context)
        planted_recall = (
            min(1.0, flagged / domain.expected_defects)
            if domain.expected_defects and flagged is not None
            else None
        )
        if planted_recall is not None:
            recalls.append(planted_recall)

        breakdown.append(
            DatasetBreakdown(
                dataset_id=domain.column,
                status=EvalStatus.FAIL if tautological else EvalStatus.PASS,
                score=norm.ratio(conformance),
                reason=f"{len(tautological)}/{len(outcomes)} proposal(s) admit an excluded value",
                metrics={
                    "conformance": round(conformance, 4),
                    "planted_recall": planted_recall if planted_recall is not None else -1.0,
                },
            )
        )
        per_domain.append(
            {
                "domain": asdict(domain),
                "outcomes": [asdict(o) for o in outcomes],
                "flagged": flagged,
                "flagged_detail": flagged_detail,
            }
        )

        if tautological:
            findings.append(
                Finding(
                    id="HG-A3",
                    severity=Severity.CRITICAL,
                    title=f"ACCEPTED_VALUES on {domain.column} admits the value it must reject",
                    detail=(
                        f"{len(tautological)} archived proposal(s) put "
                        f"{tautological[0].admitted_excluded} inside the allow-list. "
                        f"{domain.source} states that literal is deliberately outside the "
                        f"governed set so the agent can surface the "
                        f"{domain.expected_defects} known invalid rows. "
                        f"{_PATH_SCOPE_NOTE}"
                    ),
                    root_cause_hint=(
                        "rule_proposer_node builds the enum from the values observed in the "
                        "column being validated, so every defect present at profiling time "
                        "is admitted into its own allow-list and the rule can never fire"
                    ),
                    evidence_ref="evalgate/evidence/gate1/governed_enum_conformance.json",
                    blocks_release=True,
                )
            )
        if planted_recall is not None and planted_recall <= 0.0:
            findings.append(
                Finding(
                    id="HG-A1",
                    severity=Severity.CRITICAL,
                    title=f"None of the {domain.expected_defects} planted invalid rows were flagged",
                    detail=(
                        f"Execution flagged {flagged} violation(s) for {domain.column} "
                        f"ACCEPTED_VALUES against {domain.expected_defects} documented "
                        f"defects. {_PATH_SCOPE_NOTE}"
                    ),
                    root_cause_hint="a tautological allow-list cannot report a violation",
                    evidence_ref="evalgate/evidence/gate1/governed_enum_conformance.json",
                    blocks_release=True,
                )
            )

    unbacked = count_unbacked_enums({d.column for d in domains}, context)

    # Coverage closes an evasion this evaluator was otherwise open to.
    #
    # Every other check here inspects an ACCEPTED_VALUES rule. When the agent
    # proposed none at all, there was nothing to inspect and the evaluator returned
    # NOT_MEASURED -- which drops out of the aggregate and leaves HG-A3 reporting
    # NOT_EVALUATED. Proposing a tautological rule was therefore penalised while
    # proposing no rule for a governed column was silent, and silence scored better.
    #
    # A column the governance policy names is a column the product has committed to
    # constraining, so the absence of a rule on it is a finding in its own right.
    coverage = (len(domains) - len(uncovered_columns)) / len(domains) if domains else 1.0
    if uncovered_columns:
        findings.append(
            Finding(
                id="HG-A8",
                severity=Severity.CRITICAL,
                title=(
                    f"{len(uncovered_columns)} governed column(s) received no "
                    "ACCEPTED_VALUES rule"
                ),
                detail=(
                    f"{sorted(uncovered_columns)} are declared in {domains[0].source} "
                    "with a governed value set, but the agent proposed no allow-list "
                    "rule for them. No enum conformance can be measured for a column "
                    "that has no enum rule, so the absence is scored rather than "
                    "reported as an unmeasured gap. " + _PATH_SCOPE_NOTE
                ),
                root_cause_hint=(
                    "the agent only emits ACCEPTED_VALUES when it classifies the column "
                    "as categorical; a governed column that misses that classification "
                    "gets no rule and therefore no check"
                ),
                evidence_ref="evalgate/evidence/gate1/governed_enum_conformance.json",
                blocks_release=True,
            )
        )

    evidence: list[Evidence] = []
    if write_evidence:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        target = EVIDENCE_DIR / "governed_enum_conformance.json"
        target.write_text(
            json.dumps(
                {
                    "scope": _PATH_SCOPE_NOTE,
                    "governed_columns": [d.column for d in domains],
                    "uncovered_governed_columns": sorted(uncovered_columns),
                    "governed_column_coverage": round(coverage, 4),
                    "per_domain": per_domain,
                    "unbacked_enum_rules": unbacked,
                },
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        evidence.append(Evidence(type="file", path=str(target.relative_to(PROJECT_ROOT))))

    coverage_metric = MetricValue(
        raw=round(coverage, 4),
        unit="ratio",
        normalized=norm.ratio(coverage),
        note=(
            f"{len(domains) - len(uncovered_columns)}/{len(domains)} governed column(s) "
            "carry an ACCEPTED_VALUES rule"
        ),
    )

    if not conformances:
        # Every governed column is uncovered. This is a measured FAIL, not an
        # unmeasured gap: the evaluator looked, and found the product had proposed
        # nothing to check. Returning NOT_MEASURED here dropped the evaluator out of
        # the aggregate entirely and rewarded the worse behaviour.
        return EvalResult(
            gate=GATE,
            evaluator=EVALUATOR,
            status=EvalStatus.FAIL,
            score=0.0,
            metrics={"governed_column_coverage": coverage_metric},
            per_dataset_breakdown=breakdown,
            thresholds={"governed_column_coverage": Threshold(**{"pass": 100.0, "warn": 100.0})},
            evidence=evidence,
            critical_findings=findings,
            metadata={
                "reason": "no ACCEPTED_VALUES proposal for any governed column",
                "policy_source": domains[0].source,
                "uncovered_governed_columns": sorted(uncovered_columns),
            },
        )

    # MIN, not mean: one governed column whose enum is tautological is a failure of
    # the mechanism, and averaging it against healthy columns would hide that.
    conformance = min(conformances)
    planted_recall = min(recalls) if recalls else None

    score_components = [norm.ratio(conformance)]
    if planted_recall is not None:
        score_components.append(norm.ratio(planted_recall))
    score = sum(score_components) / len(score_components)

    metrics = {
        "governed_enum_conformance": MetricValue(
            raw=round(conformance, 4), unit="ratio", normalized=norm.ratio(conformance)
        ),
        "tautological_enum_count": MetricValue(
            raw=all_tautological, unit="count",
            normalized=norm.zero_tolerance(all_tautological),
        ),
        "unbacked_enum_rules": MetricValue(
            raw=len(unbacked), unit="count", normalized=None,
            note="ACCEPTED_VALUES on columns no policy governs; tautological by construction",
        ),
        "governed_column_coverage": coverage_metric,
    }
    if planted_recall is not None:
        metrics["planted_defect_recall"] = MetricValue(
            raw=round(planted_recall, 4), unit="ratio",
            normalized=norm.ratio(planted_recall),
        )

    return EvalResult(
        gate=GATE,
        evaluator=EVALUATOR,
        status=EvalStatus.FAIL if findings else EvalStatus.PASS,
        score=score,
        metrics=metrics,
        per_dataset_breakdown=breakdown,
        thresholds={
            "governed_enum_conformance": Threshold(**{"pass": 100.0, "warn": 90.0}),
            "tautological_enum_count": Threshold(**{"pass": 0.0, "warn": 0.0}),
        },
        evidence=evidence,
        critical_findings=findings,
        metadata={
            "scope": _PATH_SCOPE_NOTE,
            "policy_source": domains[0].source,
            "governed_columns": [d.column for d in domains],
            "unbacked_enum_examples": unbacked[:5],
        },
    )
