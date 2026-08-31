"""Freeze the SDIH ground truth into files that can be reviewed and compared.

SDIH regenerates its labels from a seed on every run, which makes them reproducible
but not *inspectable*: nobody can open a generator and see what the agent was
expected to find, and no reviewer can diff last month's expectations against this
month's.

Freezing turns the generator's output into an artefact with a checksum. Two things
follow from that:

  a reviewer can read the labels and disagree with them
  a change in the labels becomes visible in a diff instead of silently moving the
  baseline under a comparison that is still called "the same test"

The snapshot is not a second source of truth. ``verify`` re-derives the labels and
compares fingerprints, so a stale or hand-edited snapshot is detected rather than
trusted.

    python -m evalgate.golden.freeze            # write snapshots + manifest
    python -m evalgate.golden.freeze --verify   # check them without writing
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

GOLDEN_ROOT = Path(__file__).resolve().parent
TIER1 = GOLDEN_ROOT / "tier1_sdih"
MANIFEST = GOLDEN_ROOT / "manifest.yaml"

SEED = 20260819

#: Row cap for the snapshot. The real NYC fixture is kept whole because it is the
#: reality anchor the replay scorer also uses; synthetic archetypes are capped so the
#: committed files stay reviewable rather than becoming binary blobs in prose form.
MAX_ROWS = 20_000
NYC = "corpus-nyc-taxi-50k"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def build_labels(dataset_id: str):
    """Return (LabelStore, plan, verification report) for one archetype.

    The report is produced here, not left to the caller, because a label set that
    has not been checked against the data it describes is not ground truth -- it is
    an assertion about ground truth.
    """
    from evalgate.corpus.generator import ARCHETYPES, generate
    from evalgate.corpus.nyc_preexisting import columns_to_skip, recover_labels
    from evalgate.sdih.injector import build_plan, inject
    from evalgate.sdih.profiler import profile_dataframe
    from evalgate.sdih.verifier import verify as verify_labels

    archetype = ARCHETYPES[dataset_id]
    rows = None if dataset_id == NYC else min(archetype.rows, MAX_ROWS)
    frame = generate(dataset_id, seed=SEED, rows=rows)

    preexisting = []
    skip: dict[str, set[str]] = {}
    if dataset_id == NYC:
        # The shipped fixture already carries defects. They are true positives, not
        # agent mistakes, so they are merged in rather than injected over.
        preexisting, _ = recover_labels(frame)
        skip = columns_to_skip(preexisting)

    profile = profile_dataframe(frame)
    plan = build_plan(
        frame, dataset_id=dataset_id, seed=SEED, profile=profile, skip_columns=skip
    )
    dirty, store = inject(
        frame, plan, id_column=archetype.id_column, preexisting_labels=preexisting
    )
    report = verify_labels(dirty, store, id_column=archetype.id_column)
    return store, plan, report


def freeze(*, write: bool = True) -> dict[str, dict]:
    from evalgate.corpus.generator import ARCHETYPES

    entries: dict[str, dict] = {}
    for dataset_id in sorted(ARCHETYPES):
        try:
            store, plan, report = build_labels(dataset_id)
        except Exception as exc:  # noqa: BLE001 - a missing fixture must not abort the rest
            entries[dataset_id] = {"status": "UNAVAILABLE", "reason": f"{type(exc).__name__}: {exc}"}
            continue

        if not report.passed:
            # Refusing to freeze is the whole point. A reference set that contains
            # labels the data does not support will penalise the agent for missing
            # defects that are not there, and every score derived from it inherits
            # the error silently.
            entries[dataset_id] = {
                "status": "REJECTED",
                "reason": (
                    f"{len(report.failures)} label(s) do not match the data they "
                    f"describe; {report.count_mismatches or 'counts consistent'}"
                ),
                "failures": report.failures[:5],
            }
            continue

        target = TIER1 / f"{dataset_id}.labels.json"
        if write:
            store.save(target)
        entries[dataset_id] = {
            "status": "FROZEN" if write else "COMPUTED",
            "file": f"tier1_sdih/{dataset_id}.labels.json",
            "fingerprint": store.fingerprint(),
            "sha256": _sha256(target) if write and target.exists() else None,
            "total_labels": len(store.labels),
            "verified_labels": report.checked,
            "counts_by_class": store.counts_by_class(),
            "applicable_classes": sorted(store.applicable_classes),
            "not_applicable_classes": sorted(store.not_applicable_classes),
            "measurable": plan.is_measurable,
            "plan_warnings": plan.warnings,
        }
    return entries


def verify() -> tuple[bool, list[str]]:
    """Check the snapshots two different ways. Returns (ok, problems).

    Integrity and correctness are separate questions and both have to be asked:

      integrity   does the file still hold what was frozen?      (sha256 + fingerprint)
      correctness do the labels match the data they describe?    (semantic verify)

    Comparing fingerprints alone answers only the first. A label set can be
    perfectly intact and still assert a duplicate that is not there -- which is
    exactly the defect this check was extended to catch.
    """
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else None
    if not manifest:
        return False, ["manifest.yaml is missing; run `python -m evalgate.golden.freeze`"]

    problems: list[str] = []
    recomputed = freeze(write=False)
    for dataset_id, recorded in (manifest.get("datasets") or {}).items():
        current = recomputed.get(dataset_id)
        if current is None:
            problems.append(f"{dataset_id}: in the manifest but no longer an archetype")
            continue
        if current.get("status") == "UNAVAILABLE":
            problems.append(f"{dataset_id}: cannot be regenerated ({current.get('reason')})")
            continue
        if current.get("status") == "REJECTED":
            problems.append(f"{dataset_id}: labels no longer match the data ({current.get('reason')})")
            continue
        if recorded.get("fingerprint") != current.get("fingerprint"):
            problems.append(
                f"{dataset_id}: fingerprint drifted. The labels changed without the "
                f"manifest being updated, so any baseline comparing against them is invalid."
            )
        path = GOLDEN_ROOT / str(recorded.get("file", ""))
        if not path.exists():
            problems.append(f"{dataset_id}: snapshot file {recorded.get('file')} is missing")
        elif recorded.get("sha256") and _sha256(path) != recorded["sha256"]:
            problems.append(f"{dataset_id}: snapshot file was edited after it was frozen")
    return (not problems), problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="check, do not write")
    args = parser.parse_args(argv)

    if args.verify:
        ok, problems = verify()
        for problem in problems:
            print(f"  {problem}")
        print("golden tier 1: OK" if ok else f"golden tier 1: {len(problems)} problem(s)")
        return 0 if ok else 1

    TIER1.mkdir(parents=True, exist_ok=True)
    entries = freeze(write=True)
    MANIFEST.write_text(
        yaml.safe_dump(
            {
                "version": "1.0",
                "sdih_seed": SEED,
                "max_rows": MAX_ROWS,
                "frozen_at": datetime.now(UTC).isoformat(),
                "note": (
                    "Regenerate with `python -m evalgate.golden.freeze`. Changing these "
                    "labels invalidates every stored baseline that was scored against them."
                ),
                "datasets": entries,
            },
            sort_keys=False, allow_unicode=True,
        ),
        encoding="utf-8",
    )
    frozen = sum(1 for e in entries.values() if e.get("status") == "FROZEN")
    rejected = [d for d, e in entries.items() if e.get("status") == "REJECTED"]
    print(f"froze {frozen}/{len(entries)} archetype(s) -> {TIER1.relative_to(GOLDEN_ROOT.parent)}")
    for dataset_id, entry in entries.items():
        if entry.get("status") != "FROZEN":
            print(f"  {entry.get('status','?').lower()} {dataset_id}: {entry.get('reason')}")
        else:
            note = f", {len(entry['plan_warnings'])} plan warning(s)" if entry.get("plan_warnings") else ""
            print(
                f"  {dataset_id}: {entry['total_labels']} labels "
                f"({entry['verified_labels']} verified), {entry['fingerprint'][:12]}{note}"
            )
    # A rejected archetype is a hard failure: freezing partially would leave the
    # manifest describing a corpus that no longer exists in full.
    return 1 if rejected else 0


if __name__ == "__main__":
    sys.exit(main())
