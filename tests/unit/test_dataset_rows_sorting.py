"""Sorting the dataset preview must never depend on the dataset being taxi data.

``/datasets/{id}/rows`` accepts any ``sort_by`` string. The legacy branch looked
the column up in a four-entry taxi map with an unguarded subscript, so any other
column -- including the empty default -- raised KeyError and the endpoint
answered 500. The Data Explorer offers whatever columns the dataset actually
has, so this was reachable from the UI.
"""

import inspect

import pytest

from src.api import routes
from src.models.database import SourceRowModel


def sort_column_for(sort_by: str):
    """Reproduce the endpoint's column choice in isolation."""
    sort_columns = {
        "pickup_at": SourceRowModel.pickup_at,
        "trip_distance": SourceRowModel.trip_distance,
        "fare_amount": SourceRowModel.fare_amount,
        "total_amount": SourceRowModel.total_amount,
    }
    return sort_columns.get(sort_by, SourceRowModel.source_row_id)


@pytest.mark.parametrize("sort_by", ["pickup_at", "trip_distance", "fare_amount", "total_amount"])
def test_known_taxi_columns_still_sort_by_themselves(sort_by):
    assert sort_column_for(sort_by).key == sort_by


@pytest.mark.parametrize("sort_by", ["", "passenger_count", "vendor_id", "PassengerId", "primaryTitle"])
def test_unknown_columns_fall_back_instead_of_raising(sort_by):
    # source_row_id is present for every dataset, so it is always orderable.
    assert sort_column_for(sort_by).key == "source_row_id"


def test_the_endpoint_no_longer_defaults_to_a_taxi_column():
    parameter = inspect.signature(routes.query_dataset_rows).parameters["sort_by"]
    assert parameter.default.default == "", "a generic endpoint must not default to a taxi column"
