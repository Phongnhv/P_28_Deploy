"""Utility functions for parsing, formatting, and normalizing evidence path references."""

from __future__ import annotations


def normalize_evidence_ref(ref: str, default_column: str = "_table") -> str:
    """Standardizes evidence refs to dotted format: profile.<table>.<col>.<metric>

    Example input: schema:semantic_contract:nullable_expected_false
    Example output: schema._table.semantic_contract.nullable_expected_false
    """
    if not ref:
        return ref

    if ":" in ref and "." not in ref:
        parts = ref.split(":")
        source = parts[0]
        if len(parts) == 2:
            return f"{source}.{default_column}.{parts[1]}"
        elif len(parts) >= 3:
            col = parts[1] if parts[1] else default_column
            metric = ".".join(parts[2:])
            return f"{source}.{col}.{metric}"

    return ref


def parse_dotted_evidence_ref(ref: str) -> dict[str, str]:
    """Parses a dotted evidence reference into components.

    Returns dict with keys: source_type, target, metric
    """
    parts = ref.split(".")
    if len(parts) >= 3:
        return {
            "source_type": parts[0],
            "target": parts[1],
            "metric": ".".join(parts[2:]),
        }
    return {
        "source_type": parts[0] if parts else "unknown",
        "target": parts[1] if len(parts) > 1 else "_table",
        "metric": parts[-1] if parts else "",
    }
