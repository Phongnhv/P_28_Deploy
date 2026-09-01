"""Ground truth must describe the frame the product actually ingested.

Recall is ``|predicted ∩ truth| / |truth|``. If truth is recovered from a different
frame than the one the agent saw, the denominator counts defects that were never in
the input and the row ids cannot match anything -- the score then measures the gap
between two datasets rather than the agent's ability.

That was live: ``product_run.create_bundle`` ingests 5,000 rows while the truth
builder regenerated the archetype's full 50,000. These tests bind the two together.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pandas as pd
import pytest

from evalgate.core.context import EvalRunContext
from evalgate.run import _nyc_ground_truth
from evalgate.schemas.artifact_manifest import (
    ArtifactManifestV2,
    ArtifactRecord,
    ModelIdentity,
)

HEX64 = "a" * 64
RUN_ID = "product-test"
DATASET_ID = "dataset-test"


def _taxi_frame(rows: int, *, first_id: int = 0) -> pd.DataFrame:
    """A taxi-shaped frame carrying one recoverable defect of each seeded class."""
    ids = [f"row-{i:06d}" for i in range(first_id, first_id + rows)]
    frame = pd.DataFrame(
        {
            "source_row_id": ids,
            "vendor_id": ["VTS"] * rows,
            "fare_amount": [10.0] * rows,
            "trip_distance": [2.0] * rows,
            "payment_type": ["Cash"] * rows,
            "pickup_at": ["2026-01-01T00:00:00"] * rows,
            "dropoff_at": ["2026-01-01T00:10:00"] * rows,
        }
    )
    # One defect per seeded class, always inside the first five rows so a truncated
    # frame still carries them.
    frame.loc[0, "vendor_id"] = None                 # MISSING_VALUE
    frame.loc[1, "fare_amount"] = -10.0              # SIGN_FLIP
    frame.loc[2, "trip_distance"] = -2.0             # SIGN_FLIP
    frame.loc[3, "payment_type"] = "Invalid Payment (Dispute/Test)"  # INVALID_CATEGORY
    frame.loc[4, "vendor_id"] = "VTS"                # DUPLICATE_ROW fingerprint
    return frame


def _context(tmp_path, frame: pd.DataFrame) -> EvalRunContext:
    tmp_path.mkdir(parents=True, exist_ok=True)
    dataset = tmp_path / "dataset.csv"
    frame.to_csv(dataset, index=False)
    digest = hashlib.sha256(dataset.read_bytes()).hexdigest()
    now = datetime.now(UTC)
    record = ArtifactRecord(
        name="dataset.csv",
        type="input-dataset",
        relative_path="dataset.csv",
        sha256=digest,
        media_type="text/csv",
        producer="test",
        run_id=RUN_ID,
        dataset_id=DATASET_ID,
        created_at=now,
    )
    manifest = ArtifactManifestV2(
        schema_version="2.0",
        finalized=True,
        run_id=RUN_ID,
        git_sha="a" * 40,
        workspace_dirty=False,
        created_at=now,
        dataset_id=DATASET_ID,
        dataset_fingerprint=digest,
        schema_fingerprint=HEX64,
        model=ModelIdentity(provider="evalgate", name="fake", mode="deterministic-test"),
        prompt_hash=HEX64,
        policy_hash=HEX64,
        config_hash=HEX64,
        workflow="test",
        product_version="1.0.0",
        artifacts=(record,),
    )
    return EvalRunContext(
        run_id=RUN_ID,
        git_sha=manifest.git_sha,
        dataset_id=DATASET_ID,
        dataset_fingerprint=digest,
        model=manifest.model,
        prompt_hash=HEX64,
        artifact_root=tmp_path,
        manifest=manifest,
        profile="ci",
    )


def _truth_ids(truth) -> set[str]:
    return {
        row_id
        for columns in truth.values()
        for row_ids in columns.values()
        for row_id in row_ids
    }


def test_truth_is_recovered_from_the_ingested_artifact(tmp_path) -> None:
    frame = _taxi_frame(50)
    truth = _nyc_ground_truth(_context(tmp_path, frame))

    assert truth, "the seeded frame must yield recoverable labels"
    assert _truth_ids(truth) <= set(frame["source_row_id"]), (
        "every labelled row id must exist in the frame the product ingested"
    )


def test_truth_does_not_leak_rows_the_agent_never_saw(tmp_path) -> None:
    """The exact defect the fix closes.

    A bundle carrying the first 5 rows must not be scored against truth recovered
    from 500. Before the fix the truth builder regenerated the archetype and every
    id beyond the ingested slice became an undetectable defect.
    """
    ingested = _taxi_frame(5)
    truth = _nyc_ground_truth(_context(tmp_path, ingested))

    wider = set(_taxi_frame(500)["source_row_id"])
    beyond = _truth_ids(truth) - set(ingested["source_row_id"])

    assert beyond == set(), f"truth references {len(beyond)} unseen rows: {sorted(beyond)[:5]}"
    assert _truth_ids(truth) < wider, "sanity: the wider frame is a strict superset"


def test_truth_denominator_tracks_the_ingested_row_count(tmp_path) -> None:
    """Doubling the ingested rows must not leave the defect count unchanged.

    A denominator that ignores the bundle is the signature of truth being
    regenerated from a fixed archetype instead of read from the artifact.
    """
    small = _truth_ids(_nyc_ground_truth(_context(tmp_path / "small", _taxi_frame(20))))

    wide = _taxi_frame(40)
    # A second defect of the same class, only present in the larger frame.
    wide.loc[30, "fare_amount"] = -99.0
    large = _truth_ids(_nyc_ground_truth(_context(tmp_path / "large", wide)))

    assert len(large) > len(small)


@pytest.mark.parametrize("rows", [5, 25, 100])
def test_every_labelled_row_is_addressable(tmp_path, rows: int) -> None:
    frame = _taxi_frame(rows)
    truth = _nyc_ground_truth(_context(tmp_path / str(rows), frame))
    available = set(frame["source_row_id"])
    for defect, columns in truth.items():
        for column, row_ids in columns.items():
            missing = set(row_ids) - available
            assert not missing, f"{defect}/{column} references unseen rows: {sorted(missing)[:3]}"
