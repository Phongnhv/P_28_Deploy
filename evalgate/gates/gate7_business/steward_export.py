"""Producer for Gate 7: turn the product's review history into an aggregate document.

``steward_outcome.evaluate`` reads a JSON export named by ``EVALGATE_STEWARD_EVENTS``.
Nothing wrote that file, so the evaluator reported NOT_MEASURED on every run and the
business gate has never carried a number. This is the missing producer.

Three disciplines, and each one changes the answer:

*Aggregate only.* The document carries counts, never a row, never a rule body, never a
reviewer's note. Gate 2 forbids raw rows crossing a boundary and the instrument must not
be the thing that breaks that rule. The database is opened read-only.

*A proposal nobody looked at is not a rejection.* ``steward_acceptance_rate`` is
``accepted / proposal_count``. Counting the 331 proposals still sitting at PENDING in the
denominator would report an acceptance rate of 35% for a queue where every single decision
that was actually made was an approval. "Not yet reviewed" and "reviewed and refused" are
different facts and only the second belongs in an acceptance rate, so the denominator is
proposals that carry a review outcome.

*An edit has to be an actual edit.* ``proposed_rules.edited_parameters`` is populated on
every approval whether or not the steward changed anything: all 176 populated rows on this
database hold JSON identical to ``parameters``. A null-check would report a 100% edit rate
for a queue with zero edits, so the two documents are compared after parsing.

Run:

    python -m evalgate.gates.gate7_business.steward_export --db data/gate2_mvp.db \
        --out output/steward-events.json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]

#: Statuses that mean a human reached a decision. PENDING is deliberately absent.
REVIEWED_STATUSES = ("APPROVED", "REJECTED", "EDITED", "DISMISSED")


@dataclass
class StewardExport:
    """The aggregate document. Only the first four keys are read by the evaluator."""

    dataset_count: int
    proposal_count: int
    accepted_count: int
    edited_count: int
    #: Everything below is diagnostic: it explains how the four numbers were derived
    #: and is ignored by the evaluator.
    diagnostics: dict[str, Any] = field(default_factory=dict)


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _json_equal(left: str | None, right: str | None) -> bool:
    """True when two parameter documents mean the same thing.

    Falls back to a string comparison when either side is not valid JSON: an
    unparseable document is not evidence that an edit happened.
    """
    if left is None or right is None:
        return left == right
    try:
        return json.loads(left) == json.loads(right)
    except (TypeError, ValueError):
        return left.strip() == right.strip()


def collect(db_path: Path) -> StewardExport:
    uri = f"file:{db_path.as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        if not _table_exists(connection, "proposed_rules"):
            raise SystemExit(f"{db_path} has no proposed_rules table; wrong database?")

        placeholders = ",".join("?" for _ in REVIEWED_STATUSES)
        rows = connection.execute(
            f"""
            SELECT dataset_id, status, reviewer, reviewed_at, parameters, edited_parameters
            FROM proposed_rules
            WHERE status IN ({placeholders})
            """,
            REVIEWED_STATUSES,
        ).fetchall()

        reviewed_datasets: set[str] = set()
        accepted = 0
        edited = 0
        without_reviewer = 0
        by_status: dict[str, int] = {}
        by_reviewer: dict[str, int] = {}
        for dataset_id, status, reviewer, reviewed_at, params, edited_params in rows:
            reviewed_datasets.add(str(dataset_id))
            by_status[str(status)] = by_status.get(str(status), 0) + 1
            if status == "APPROVED":
                accepted += 1
            if edited_params and not _json_equal(params, edited_params):
                edited += 1
            named = (reviewer or "").strip()
            if named and reviewed_at is not None:
                by_reviewer[named] = by_reviewer.get(named, 0) + 1
            else:
                without_reviewer += 1

        total_proposals = connection.execute(
            "SELECT COUNT(*) FROM proposed_rules"
        ).fetchone()[0]
        pending = connection.execute(
            "SELECT COUNT(*) FROM proposed_rules WHERE status = 'PENDING'"
        ).fetchone()[0]
        datasets_with_any_proposal = connection.execute(
            "SELECT COUNT(DISTINCT dataset_id) FROM proposed_rules"
        ).fetchone()[0]
        datasets_registered = (
            connection.execute("SELECT COUNT(*) FROM datasets").fetchone()[0]
            if _table_exists(connection, "datasets")
            else None
        )
        edited_column_populated = connection.execute(
            "SELECT COUNT(*) FROM proposed_rules "
            "WHERE edited_parameters IS NOT NULL AND edited_parameters <> ''"
        ).fetchone()[0]

        return StewardExport(
            dataset_count=len(reviewed_datasets),
            proposal_count=len(rows),
            accepted_count=accepted,
            edited_count=edited,
            diagnostics={
                "source_database": str(db_path),
                "reviewed_statuses": list(REVIEWED_STATUSES),
                "reviewed_by_status": by_status,
                "reviewed_by_reviewer": by_reviewer,
                "reviewed_without_named_reviewer": without_reviewer,
                "rejections_recorded": by_status.get("REJECTED", 0),
                "proposals_total_including_pending": total_proposals,
                "proposals_pending": pending,
                "datasets_with_any_proposal": datasets_with_any_proposal,
                "datasets_registered": datasets_registered,
                "edited_parameters_column_populated": edited_column_populated,
                "edited_parameters_semantically_different": edited,
                "note": (
                    "dataset_count and proposal_count are scoped to proposals that carry a "
                    "review outcome; edited_count compares the two parameter documents "
                    "rather than testing the column for null"
                ),
            },
        )
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/gate2_mvp.db",
                        help="product database to read (opened read-only)")
    parser.add_argument("--out", default="output/steward-events.json")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path
    if not db_path.exists():
        raise SystemExit(f"database not found: {db_path}")

    export = collect(db_path)
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = PROJECT_ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(asdict(export), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
