"""Gate 1F: does every rule the agent proposed actually compile to safe SQL?

A rule that cannot be compiled never runs, and a rule that never runs is indistinguishable
from a passing one in the execution report -- it simply is not there. So the question is
not "can the compiler handle the five rule types we wrote a fixture for", it is "did the
thirty-one rules *this run produced* all reach executable SQL, with their identifiers
quoted and their values bound".

Both halves matter and they fail differently. An uncompilable rule is a silent coverage
hole. An unbound value is an injection surface: the agent chooses column names and
literals from data it read, so a rule body is untrusted input to the compiler.

The rules come from the bundle's ``proposals`` artifact, so this grades the run being
evaluated. The previous version compiled five hand-written rules and returned 100.0
whatever the agent had done -- it could not fail on the artefact it was scoring. The
hand-written cases are kept, as tests, in ``tests/test_sql_compilation.py``.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from evalgate.core.context import EvalRunContext
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
EVIDENCE_DIR = PROJECT_ROOT / "evalgate" / "evidence" / "gate1"

GATE = "ai_quality"
EVALUATOR = "sql_compilation_probe_v1"

#: Dialects a compiled predicate must survive. A rule that only compiles on SQLite is a
#: rule that stops working the day the runner points at Postgres.
DIALECTS = ("sqlite", "postgresql")

#: Rule types the product's compiler is expected to handle. Anything outside this set is
#: reported as unsupported rather than as a compilation failure: "the agent invented a
#: rule type nobody implemented" and "the compiler is broken" are different defects.
COMPILABLE = {
    "NOT_NULL", "RANGE", "ACCEPTED_VALUES", "REGEX_FORMAT", "CROSS_FIELD_COMPARISON",
}


@dataclass
class CompileOutcome:
    rule_id: str
    rule_type: str
    column: str | None
    dialect: str
    compiled: bool
    identifier_quoted: bool
    values_bound: bool
    detail: str


def _params(rule: dict[str, Any]) -> dict[str, Any]:
    return rule.get("effective_parameters") or rule.get("parameters") or {}


def _load_rules(context: EvalRunContext) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    for record in context.records("proposals"):
        payload = json.loads(context.path_for(record).read_text(encoding="utf-8"))
        batch = payload.get("proposed_rules") if isinstance(payload, dict) else payload
        if isinstance(batch, list):
            rules.extend(r for r in batch if isinstance(r, dict))
    return rules


def compile_rule(rule: dict[str, Any], index: int, dialect: str) -> CompileOutcome:
    """Push one proposed rule through the product's own predicate builder."""
    from src.agents.nodes import test_generator_node as tgn

    rule_type = str(rule.get("rule_type") or "")
    column = rule.get("column")
    rule_id = str(rule.get("rule_id") or rule.get("id") or f"rule-{index}")
    base = dict(rule_id=rule_id, rule_type=rule_type, column=column, dialect=dialect)

    if rule_type not in COMPILABLE:
        return CompileOutcome(
            **base, compiled=False, identifier_quoted=False, values_bound=False,
            detail=f"rule type {rule_type!r} has no compiler branch",
        )

    spec = {"rule_type": rule_type, "column": column, "parameters": _params(rule)}
    try:
        predicate, binds = tgn._build_row_predicate(spec, index, dialect)
    except Exception as exc:  # noqa: BLE001 - a compiler crash is the observation
        return CompileOutcome(
            **base, compiled=False, identifier_quoted=False, values_bound=False,
            detail=f"{type(exc).__name__}: {exc}",
        )

    if not predicate:
        return CompileOutcome(
            **base, compiled=False, identifier_quoted=False, values_bound=False,
            detail="compiler returned an empty predicate",
        )

    # The identifier must appear quoted, never bare: the column name is chosen by the
    # agent from data it read, so it is untrusted input to string concatenation.
    quoted = True
    if column:
        expected = tgn._quote_ident(str(column), dialect)
        quoted = expected in predicate

    # Every literal the rule carries must arrive as a bind parameter. NOT_NULL carries
    # none, so an empty bind set is only suspicious when the rule has parameters.
    literals = [
        value for key, value in _params(rule).items()
        if key not in {"columns", "target_column"} and value not in (None, [], {})
    ]
    bound = bool(binds) or not literals

    return CompileOutcome(
        **base, compiled=True, identifier_quoted=quoted, values_bound=bound,
        detail=f"{len(binds)} bind parameter(s)",
    )


def evaluate(
    *, write_evidence: bool = True, context: EvalRunContext | None = None
) -> EvalResult:
    if context is None or not context.records("proposals"):
        return EvalResult(
            gate=GATE, evaluator=EVALUATOR, status=EvalStatus.NOT_MEASURED,
            metadata={
                "reason": (
                    "compilation is measured against the rules this run proposed; "
                    "no proposals artifact is available"
                )
            },
        )

    rules = _load_rules(context)
    if not rules:
        return EvalResult(
            gate=GATE, evaluator=EVALUATOR, status=EvalStatus.NOT_MEASURED,
            metadata={"reason": "the proposals artifact contains no rules"},
        )

    outcomes = [
        compile_rule(rule, index, dialect)
        for index, rule in enumerate(rules, start=1)
        for dialect in DIALECTS
    ]

    failed = [o for o in outcomes if not o.compiled]
    unquoted = [o for o in outcomes if o.compiled and not o.identifier_quoted]
    unbound = [o for o in outcomes if o.compiled and not o.values_bound]
    unsupported_types = sorted(
        {o.rule_type for o in failed if o.rule_type not in COMPILABLE}
    )

    compile_rate = (len(outcomes) - len(failed)) / len(outcomes)
    compiled = [o for o in outcomes if o.compiled]
    quote_rate = (
        (len(compiled) - len(unquoted)) / len(compiled) if compiled else 0.0
    )
    bind_rate = (len(compiled) - len(unbound)) / len(compiled) if compiled else 0.0

    evidence: list[Evidence] = []
    if write_evidence:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        target = EVIDENCE_DIR / "sql_compilation_probe.json"
        target.write_text(
            json.dumps(
                {
                    "rules_compiled": len(rules),
                    "dialects": list(DIALECTS),
                    "attempts": len(outcomes),
                    "unsupported_rule_types": unsupported_types,
                    "failed": [asdict(o) for o in failed],
                    "identifier_not_quoted": [asdict(o) for o in unquoted],
                    "values_not_bound": [asdict(o) for o in unbound],
                },
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        evidence.append(Evidence(type="file", path=str(target.relative_to(PROJECT_ROOT))))

    findings: list[Finding] = []
    if unquoted:
        findings.append(
            Finding(
                id="SQL-IDENT-UNQUOTED",
                severity=Severity.CRITICAL,
                title=f"{len(unquoted)} compiled predicate(s) embed an unquoted identifier",
                detail=(
                    "The column name is chosen by the agent from data it read, so an "
                    "unquoted identifier is concatenated untrusted input. Examples: "
                    + "; ".join(f"{o.rule_id}/{o.dialect}" for o in unquoted[:5])
                ),
                root_cause_hint="_build_row_predicate did not route this branch through _quote_ident",
                evidence_ref="evalgate/evidence/gate1/sql_compilation_probe.json",
                blocks_release=True,
            )
        )
    if unbound:
        findings.append(
            Finding(
                id="SQL-VALUE-UNBOUND",
                severity=Severity.CRITICAL,
                title=f"{len(unbound)} compiled predicate(s) inline a literal instead of binding it",
                detail="; ".join(f"{o.rule_id}/{o.dialect}: {o.detail}" for o in unbound[:5]),
                root_cause_hint="the rule's parameters were interpolated into the SQL string",
                evidence_ref="evalgate/evidence/gate1/sql_compilation_probe.json",
                blocks_release=True,
            )
        )
    if failed:
        findings.append(
            Finding(
                id="SQL-UNCOMPILABLE",
                severity=Severity.HIGH,
                title=f"{len(failed)} proposed rule/dialect pair(s) do not compile",
                detail=(
                    f"Unsupported rule types: {unsupported_types or 'none'}. "
                    + "; ".join(f"{o.rule_id}/{o.dialect}: {o.detail}" for o in failed[:5])
                    + ". A rule that never compiles never runs, and an absent rule looks "
                    "identical to a passing one in the execution report."
                ),
                root_cause_hint=(
                    "the agent proposed a rule type the compiler has no branch for, or "
                    "the parameters do not match the shape that branch expects"
                ),
                evidence_ref="evalgate/evidence/gate1/sql_compilation_probe.json",
                blocks_release=False,
            )
        )

    score = norm.ratio(min(compile_rate, quote_rate, bind_rate))
    return EvalResult(
        gate=GATE,
        evaluator=EVALUATOR,
        status=EvalStatus.FAIL if findings else EvalStatus.PASS,
        score=score,
        metrics={
            "rule_compile_rate": MetricValue(
                raw=round(compile_rate, 4), unit="ratio", normalized=norm.ratio(compile_rate),
                note=f"{len(rules)} proposed rule(s) × {len(DIALECTS)} dialect(s)",
            ),
            "identifier_quoting_safety": MetricValue(
                raw=len(unquoted) == 0, unit="boolean", normalized=norm.boolean(not unquoted),
                note=f"{len(unquoted)} predicate(s) embed a bare identifier",
            ),
            "value_binding_safety": MetricValue(
                raw=len(unbound) == 0, unit="boolean", normalized=norm.boolean(not unbound),
                note=f"{len(unbound)} predicate(s) inline a literal",
            ),
            "uncompilable_rule_count": MetricValue(
                raw=len({o.rule_id for o in failed}), unit="count",
                normalized=norm.zero_tolerance(len({o.rule_id for o in failed})),
            ),
            "rules_compiled": MetricValue(raw=len(rules), unit="count", normalized=None),
        },
        thresholds={
            "rule_compile_rate": Threshold(**{"pass": 100.0, "warn": 100.0}),
            "identifier_quoting_safety": Threshold(**{"pass": 100.0, "warn": 100.0}),
        },
        evidence=evidence,
        critical_findings=findings,
        metadata={
            "mode": "compiled from the bundle's own proposals",
            "dialects": list(DIALECTS),
            "unsupported_rule_types": unsupported_types,
        },
    )
