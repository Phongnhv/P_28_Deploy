"""The local SQLite migrations that repair a database created by an older model.

``Base.metadata.create_all`` only creates missing tables; it never alters one
that already exists. Two model changes therefore left working databases with a
stale definition, and both broke a button in the UI:

* ``dq_results.id`` stayed INTEGER while the model started writing UUIDs, so
  every Graph 2 run died on "datatype mismatch".
* ``source_rows`` kept ``source_row_id`` as its whole primary key, so the second
  dataset ever ingested collided on ``row-00001``.

These also guard the trap that broke the first attempt: SQLite keeps an index
attached to a renamed table under its original name, so the rebuild must drop it
before creating the new table's index.
"""

import sqlite3

import pytest
from sqlalchemy import create_engine, inspect

from src.services.rule_store import _migrate_local_dq_result_ids, _migrate_local_source_row_key

SOURCE_ROWS_OLD = """
CREATE TABLE source_rows (
    source_row_id VARCHAR(256) NOT NULL,
    dataset_id VARCHAR(256) NOT NULL,
    vendor_id VARCHAR(64),
    fare_amount FLOAT,
    PRIMARY KEY (source_row_id),
    FOREIGN KEY(dataset_id) REFERENCES datasets (id)
)
"""

DQ_RESULTS_OLD = """
CREATE TABLE dq_results (
    id INTEGER NOT NULL,
    run_id VARCHAR(64) NOT NULL,
    rule_id VARCHAR(64) NOT NULL,
    rule_title VARCHAR(256) NOT NULL,
    status VARCHAR(32) NOT NULL,
    checked_count INTEGER NOT NULL,
    failed_count INTEGER NOT NULL,
    failed_row_ids TEXT NOT NULL,
    violation_rate FLOAT,
    duration_ms FLOAT,
    dbt_status VARCHAR(32),
    metrics_status VARCHAR(32),
    error_message TEXT,
    PRIMARY KEY (id),
    FOREIGN KEY(run_id) REFERENCES dq_runs (id)
)
"""


@pytest.fixture()
def old_database(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE datasets (id VARCHAR(256) PRIMARY KEY)")
    db.execute("CREATE TABLE dq_runs (id VARCHAR(64) PRIMARY KEY)")
    db.execute(SOURCE_ROWS_OLD)
    # The index that made the first rebuild attempt fail.
    db.execute("CREATE INDEX ix_source_rows_dataset_id ON source_rows (dataset_id)")
    db.execute(DQ_RESULTS_OLD)
    db.execute("INSERT INTO datasets VALUES ('ds-a')")
    db.execute("INSERT INTO dq_runs VALUES ('run-1')")
    for i in range(1, 6):
        db.execute(
            "INSERT INTO source_rows (source_row_id, dataset_id, fare_amount) VALUES (?,?,?)",
            (f"row-{i:05d}", "ds-a", i * 1.5),
        )
        db.execute(
            "INSERT INTO dq_results (id, run_id, rule_id, rule_title, status,"
            " checked_count, failed_count, failed_row_ids) VALUES (?,?,?,?,?,?,?,?)",
            (i, "run-1", f"rv_{i}", f"Rule {i}", "PASS", 5, 0, "[]"),
        )
    db.commit()
    db.close()
    return create_engine(f"sqlite:///{path}")


def test_source_rows_gains_the_dataset_id_in_its_primary_key(old_database):
    assert inspect(old_database).get_pk_constraint("source_rows")["constrained_columns"] == ["source_row_id"]

    _migrate_local_source_row_key(old_database)

    assert set(inspect(old_database).get_pk_constraint("source_rows")["constrained_columns"]) == {
        "source_row_id",
        "dataset_id",
    }


def test_source_rows_rebuild_keeps_every_row_and_value(old_database):
    _migrate_local_source_row_key(old_database)

    with old_database.begin() as connection:
        assert connection.exec_driver_sql("select count(*) from source_rows").scalar() == 5
        assert connection.exec_driver_sql(
            "select fare_amount from source_rows where source_row_id='row-00003'"
        ).scalar() == 4.5
        # A rebuild that leaves its scratch table behind stranded the real rows
        # once already; the table must be gone.
        leftovers = [
            row[0]
            for row in connection.exec_driver_sql(
                "select name from sqlite_master where name like '%legacy%'"
            )
        ]
        assert leftovers == []


def test_two_datasets_can_reuse_the_same_row_id_after_the_rebuild(old_database):
    _migrate_local_source_row_key(old_database)

    with old_database.begin() as connection:
        connection.exec_driver_sql("INSERT INTO datasets VALUES ('ds-b')")
        # This is the exact insert that used to raise UNIQUE constraint failed.
        connection.exec_driver_sql(
            "INSERT INTO source_rows (source_row_id, dataset_id) VALUES ('row-00001','ds-b')"
        )
        assert connection.exec_driver_sql(
            "select count(*) from source_rows where source_row_id='row-00001'"
        ).scalar() == 2


def test_source_rows_index_survives_the_rebuild(old_database):
    _migrate_local_source_row_key(old_database)

    names = {index["name"] for index in inspect(old_database).get_indexes("source_rows")}
    assert "ix_source_rows_dataset_id" in names


def test_dq_results_id_becomes_a_string_key_without_losing_history(old_database):
    assert "INT" in str(inspect(old_database).get_columns("dq_results")[0]["type"]).upper()

    _migrate_local_dq_result_ids(old_database)

    assert "CHAR" in str(inspect(old_database).get_columns("dq_results")[0]["type"]).upper()
    with old_database.begin() as connection:
        assert connection.exec_driver_sql("select count(*) from dq_results").scalar() == 5
        assert connection.exec_driver_sql(
            "select rule_title from dq_results where id='3'"
        ).scalar() == "Rule 3"


def test_both_migrations_are_safe_to_run_again(old_database):
    _migrate_local_source_row_key(old_database)
    _migrate_local_dq_result_ids(old_database)
    # A second startup must be a no-op, not another rebuild.
    _migrate_local_source_row_key(old_database)
    _migrate_local_dq_result_ids(old_database)

    with old_database.begin() as connection:
        assert connection.exec_driver_sql("select count(*) from source_rows").scalar() == 5
        assert connection.exec_driver_sql("select count(*) from dq_results").scalar() == 5
