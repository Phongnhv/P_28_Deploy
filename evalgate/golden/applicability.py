"""Which columns of *this* dataset a golden case is a statement about.

A case that names ``fare_amount`` is a statement about one taxi dataset. A case that
names ``semantic_type: currency`` is a statement about every dataset that has a
monetary column, and resolves to different columns in each. The second is what a
product advertised as working on arbitrary uploads needs its ground truth to look
like.

Resolution reads the bundle's own artifacts -- the semantic contract the agent
produced and the profile it was built from -- so nothing here is hard-coded to a
schema. When a selector matches nothing, the case is NOT_APPLICABLE: it is not a
failure to have no currency column, and scoring it as one is how a clinical dataset
came to be penalised for not being a taxi dataset.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from evalgate.core.context import EvalRunContext
from evalgate.golden.schema import Applicability, GoldenCase


@dataclass(frozen=True)
class SemanticColumn:
    name: str
    semantic_type: str
    business_role: str
    nullable_expected: bool | None


@dataclass
class DatasetContext:
    """Everything a selector can be resolved against, for one bundle."""

    dataset_id: str
    #: Stable name of the corpus this bundle was generated from. Ground truth binds
    #: to this, never to ``dataset_id``: the product mints a new
    #: ``dataset-import-<uuid>`` per upload, so a case keyed on the runtime id
    #: matches nothing on every run. Observed directly -- all nine cases resolved to
    #: NOT_APPLICABLE against a real bundle before this was separated out.
    corpus_id: str | None = None
    #: Physical columns, from the profile. The authority on what exists.
    columns: tuple[str, ...] = ()
    #: Agent-assigned meaning, from the semantic contract. May be absent: the
    #: contract is an agent output, so a run that failed before producing one has
    #: no interpretation to resolve against.
    semantic: tuple[SemanticColumn, ...] = ()
    #: Declared column relationships, e.g. an ordered timestamp pair.
    relationships: tuple[dict[str, Any], ...] = ()
    #: Controlled vocabulary of citable evidence references, from the profile.
    evidence_keys: frozenset[str] = frozenset()
    #: Per-column profile metrics, for evidence and parameter assertions.
    profile_columns: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: Dataset-level profile metrics, e.g. ``row_count``, ``duplicate_rate``.
    profile_top_level: frozenset[str] = frozenset()

    @property
    def has_semantic_contract(self) -> bool:
        return bool(self.semantic)

    @property
    def identifiers(self) -> frozenset[str]:
        """Every name this bundle answers to, for a dataset-bound selector."""
        return frozenset(value for value in (self.corpus_id, self.dataset_id) if value)

    def semantic_for(self, column: str) -> SemanticColumn | None:
        return next((item for item in self.semantic if item.name == column), None)


def _payload(document: Any) -> dict[str, Any]:
    """Unwrap the artifact envelope the workflow API returns.

    The semantic contract reaches the bundle as a governed artifact record whose
    ``payload`` holds the contract itself; the profile arrives bare. Accepting both
    keeps this readable against either shape rather than encoding which endpoint
    produced it.
    """
    if isinstance(document, dict) and isinstance(document.get("payload"), dict):
        return document["payload"]
    return document if isinstance(document, dict) else {}


def build_dataset_context(context: EvalRunContext | None) -> DatasetContext | None:
    """Assemble a DatasetContext from the bundle, or None when it cannot be built."""
    if context is None:
        return None

    profile: dict[str, Any] = {}
    if context.records("dataset-profile"):
        profile = _payload(
            json.loads(context.path_for(context.records("dataset-profile")[0]).read_text(encoding="utf-8"))
        )

    semantic: dict[str, Any] = {}
    if context.records("semantic-contract"):
        semantic = _payload(
            json.loads(context.path_for(context.records("semantic-contract")[0]).read_text(encoding="utf-8"))
        )

    profile_columns = {
        str(item.get("name")): item
        for item in profile.get("columns", [])
        if isinstance(item, dict) and item.get("name")
    }

    semantic_columns = tuple(
        SemanticColumn(
            name=str(item.get("name")),
            semantic_type=str(item.get("semantic_type") or ""),
            business_role=str(item.get("business_role") or ""),
            nullable_expected=item.get("nullable_expected"),
        )
        for item in semantic.get("columns", [])
        if isinstance(item, dict) and item.get("name")
    )

    return DatasetContext(
        dataset_id=str(profile.get("dataset_id") or context.dataset_id),
        # Absent on manifests written before corpus_id existed. Left as None rather
        # than guessed: a wrong corpus name would silently rebind ground truth.
        corpus_id=getattr(context.manifest, "corpus_id", None),
        columns=tuple(profile_columns),
        semantic=semantic_columns,
        relationships=tuple(
            item for item in semantic.get("relationships", []) if isinstance(item, dict)
        ),
        evidence_keys=frozenset(str(key) for key in profile.get("evidence_keys", [])),
        profile_columns=profile_columns,
        profile_top_level=frozenset(
            key for key, value in profile.items() if not isinstance(value, (list, dict))
        ),
    )


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class Scope:
    """The concrete columns a case applies to in this dataset.

    ``applicable`` is stored rather than derived from ``reason``. Deriving it
    conflated two states that need to stay apart: a selector that matched nothing
    (the dataset has no currency column) and a selector that could not be resolved
    at all (the agent produced no semantic contract to resolve against). The second
    is a product failure worth naming, but neither may run assertions over an empty
    column set and call the result a pass.
    """

    columns: tuple[str, ...]
    reason: str
    applicable: bool = True


def _skip(reason: str = NOT_APPLICABLE) -> Scope:
    return Scope(columns=(), reason=reason, applicable=False)


def resolve(case: GoldenCase, dataset: DatasetContext) -> Scope:
    """Which columns of ``dataset`` this case is about, or an inapplicable Scope.

    Order matters. ``always`` wins outright because a platform invariant is not a
    statement about data at all. A dataset binding is checked next and is
    disqualifying on its own: a case written for one dataset's governed enum says
    nothing about any other dataset, whatever its columns are called.
    """
    selector: Applicability = case.applies_to

    if selector.always:
        return Scope(columns=dataset.columns, reason="platform invariant")

    if selector.dataset_id and selector.dataset_id not in dataset.identifiers:
        return _skip()

    if selector.is_unscoped:
        # No selector at all. Treated as always-on so pre-v2 suites keep running,
        # but reported so the gap is visible rather than assumed to be deliberate.
        return Scope(columns=dataset.columns, reason="unscoped (legacy case)")

    # Literal columns: platform-owned names the product creates on every dataset.
    if selector.columns:
        present = tuple(c for c in selector.columns if c in dataset.columns)
        if not present:
            return _skip()
        return Scope(columns=present, reason=f"named columns present: {list(present)}")

    if selector.relationship:
        matched = tuple(
            str(rel.get("left_column"))
            for rel in dataset.relationships
            if rel.get("left_column") and rel.get("right_column")
        )
        if not matched:
            return _skip()
        return Scope(columns=matched, reason=f"{len(matched)} declared relationship(s)")

    # Semantic selectors need an interpretation to resolve against. Without one the
    # case is unresolvable rather than inapplicable, and the caller reports the
    # missing contract instead of silently skipping every semantic case.
    if selector.semantic_type or selector.business_role:
        if not dataset.has_semantic_contract:
            return _skip("no semantic contract in this bundle")
        matched = tuple(
            item.name
            for item in dataset.semantic
            if (not selector.semantic_type or item.semantic_type == selector.semantic_type)
            and (not selector.business_role or item.business_role == selector.business_role)
        )
        if not matched:
            return _skip()
        criteria = ", ".join(
            part
            for part in (
                f"semantic_type={selector.semantic_type}" if selector.semantic_type else "",
                f"business_role={selector.business_role}" if selector.business_role else "",
            )
            if part
        )
        return Scope(columns=matched, reason=f"{criteria} -> {list(matched)}")

    if selector.dataset_id:
        return Scope(columns=dataset.columns, reason=f"dataset {selector.dataset_id}")

    return Scope(columns=dataset.columns, reason="unscoped")


def resolve_evidence_ref(ref: str, dataset: DatasetContext) -> bool:
    """Does this citation name a figure the profile actually contains?

    Resolved structurally against the profile rather than by membership in
    ``evidence_keys``. The published key list turned out to enumerate only
    ``null_rate`` per column, while proposals legitimately cite ``min_value``,
    ``negative_rate`` and quantiles -- all of which are present in the profile.
    Checking membership alone reported 36 of 55 real citations as dangling, which
    would have made the gate fire on the vocabulary being under-published rather
    than on anything the agent did wrong.

    ``evidence_keys`` is still accepted as an alternative, so a reference the
    product publishes but does not store structurally still resolves.
    """
    if ref in dataset.evidence_keys:
        return True
    parts = ref.split(".")
    if len(parts) >= 4 and parts[0] == "profile" and parts[1] == "column":
        column = parts[2]
        metric = parts[3]
        record = dataset.profile_columns.get(column)
        if record is None:
            return False
        if metric == "quantile" and len(parts) >= 5:
            quantiles = record.get("quantiles")
            return isinstance(quantiles, dict) and parts[4] in quantiles
        return metric in record
    if len(parts) == 2 and parts[0] == "profile":
        return parts[1] in dataset.profile_top_level
    return False


def semantic_vocabulary(dataset: DatasetContext) -> dict[str, int]:
    """Observed semantic types and how many columns carry each.

    Reported alongside applicability because a selector that matches nothing is
    ambiguous on its own: the dataset may genuinely have no currency column, or the
    interpreter may be emitting a vocabulary the golden set does not speak. The
    distribution tells those apart at a glance.
    """
    counts: dict[str, int] = {}
    for item in dataset.semantic:
        counts[item.semantic_type] = counts.get(item.semantic_type, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
