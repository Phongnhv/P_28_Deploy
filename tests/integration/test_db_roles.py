import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

# To run this test properly, the runner role password must be passed via environment variables
RUNNER_DB_URL = os.getenv("RUNNER_DATABASE_URL", "postgresql+psycopg2://ridepulse_runner:test_password@localhost/ridepulse")

@pytest.mark.skipif(
    not os.getenv("RUNNER_DATABASE_URL") or "postgresql" not in os.getenv("RUNNER_DATABASE_URL"),
    reason="Requires PostgreSQL RUNNER_DATABASE_URL to check role permissions"
)
def test_runner_role_is_readonly():
    """
    Test to verify that the runner database role has ONLY SELECT permissions
    and cannot perform INSERT/UPDATE/DELETE.
    """
    engine = create_engine(RUNNER_DB_URL)

    with engine.connect() as conn:
        # SELECT should pass successfully if the table exists (requires trips_raw to be migrated)
        try:
            conn.execute(text("SELECT 1 FROM trips_raw LIMIT 1"))
        except Exception as e:
            pytest.fail(f"Runner role failed to execute SELECT: {e}")

        # INSERT should fail with ProgrammingError due to lack of permissions
        with pytest.raises(ProgrammingError) as exc_info:
            conn.execute(text("INSERT INTO trips_raw (source_row_id) VALUES ('test_row_123')"))

        # Ensure the error is related to permission denial
        assert "permission denied" in str(exc_info.value).lower()
