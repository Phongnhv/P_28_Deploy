"""Integration tests cho HITL REST API — /dq/runs/{run_id}/rules/*

Dùng fixture steward_client (ASGITransport, không boot lifespan → gọi init_db() thủ công).
Seed dữ liệu trực tiếp qua service layer, không mock toàn bộ stack.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine

from src.main import app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_engine(tmp_path):
    """Thay thế DB engine bằng SQLite file tạm — dùng file để cross-thread share được."""
    import src.services.rule_store as rs
    from src.services.rule_store import Base

    db_file = tmp_path / "test_api.db"
    test_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(test_engine)

    # create_all() builds the tables but seeds nothing, so the steward account the
    # client fixture signs in with would not exist and every request would stop at
    # 401 before reaching the route under test.
    from sqlalchemy.orm import Session as SASession

    from src.services.session_service import ensure_default_users

    with SASession(test_engine) as seed_session:
        ensure_default_users(seed_session)

    # Set _engine trực tiếp — thread pool workers cũng thấy engine đúng
    original = rs._engine
    rs._engine = test_engine

    # create_all() dựng bảng nhưng không seed tài khoản. Trước đây không sao vì
    # dq_router không yêu cầu đăng nhập; giờ nó có, nên fixture client cần một
    # tài khoản thật để đăng nhập.
    from sqlalchemy.orm import Session as _Session

    from src.services.session_service import ensure_default_users

    with _Session(test_engine) as _seed_session:
        ensure_default_users(_seed_session)

    yield
    rs._engine = original
    test_engine.dispose()


@pytest_asyncio.fixture
async def client():
    """Authenticated client.

    Every ``dq_router`` endpoint now requires a session: the router is mounted with
    a role dependency in ``src/main.py``. Before that, publish, review and
    bulk-review were reachable with no credentials at all, which made the
    human-in-the-loop control unenforceable on a public deployment.

    These tests exercise the endpoints' behaviour, not their authentication, so the
    fixture signs in once. Authentication itself is covered in ``test_session.py``,
    which deliberately keeps an anonymous client.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post(
            "/api/v1/session",
            json={"username": "steward", "password": "steward"},
        )
        assert response.status_code == 200, (
            f"fixture could not sign in: {response.status_code} {response.text}"
        )
        # get_session verifies CSRF on every authenticated request, so the token has
        # to ride along or every mutating call comes back 422 instead of its real
        # status. Setting it on the client applies it to all subsequent requests.
        ac.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        yield ac


def _grant_steward_access(dataset_id: str, username: str = "steward") -> None:
    """Give the signed-in steward MANAGE on the dataset, as an import would.

    Both import endpoints write a MANAGE DatasetAccessModel row for the uploader
    (routes.py:793 and routes.py:1035), so a steward who owns a dataset always has
    one in production. These tests seed runs straight through the service layer and
    skip that step, which was invisible while the /dq run endpoints had no tenancy
    check and became a blanket 403 once they did.
    """
    import uuid as _uuid

    from sqlalchemy.orm import Session as SASession

    from src.models.database import DatasetAccessModel
    from src.services.rule_store import get_engine

    with SASession(get_engine()) as session:
        exists = (
            session.query(DatasetAccessModel)
            .filter(
                DatasetAccessModel.dataset_id == dataset_id,
                DatasetAccessModel.username == username,
            )
            .first()
        )
        if not exists:
            session.add(
                DatasetAccessModel(
                    id=str(_uuid.uuid4()),
                    dataset_id=dataset_id,
                    username=username,
                    access_level="MANAGE",
                    granted_by=username,
                )
            )
            session.commit()


def _seed_run(run_id: str, dataset_id: str = "yellow_tripdata") -> None:
    from sqlalchemy.orm import Session

    import src.services.rule_store as rs
    from src.models.database import DatasetAccessModel
    from src.services.rule_store import create_run, update_run_status

    create_run(run_id, dataset_id)
    update_run_status(run_id, "DONE")
    _grant_steward_access(dataset_id)

    with Session(rs._engine) as db_session:
        existing = db_session.query(DatasetAccessModel).filter_by(
            dataset_id=dataset_id,
            username="steward",
        ).first()
        if not existing:
            db_session.add(
                DatasetAccessModel(
                    id=f"access-{uuid.uuid4().hex[:12]}",
                    dataset_id=dataset_id,
                    username="steward",
                    access_level="MANAGE",
                    granted_by="system",
                )
            )
            db_session.commit()


def _seed_rules(run_id: str, rules_specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from src.services.rule_store import save_proposed_rules

    rules = []
    for spec in rules_specs:
        rule_id = spec["rule_id"]
        rule = {
            "rule_id": rule_id,
            "run_id": run_id,
            "table_name": spec.get("table_name", "yellow_tripdata"),
            "column": spec.get("column", "vendor_id"),
            "rule_type": spec.get("rule_type", "NOT_NULL"),
            "parameters": spec.get("parameters", {}),
            "confidence_score": spec.get("confidence_score", 1.0),
            "severity": spec.get("severity", "CRITICAL"),
            "dimension": spec.get("dimension", "COMPLETENESS"),
            "rule_description": spec.get("rule_description", "Cột không được null."),
            "ai_reasoning": spec.get("ai_reasoning", "null_pct = 0.0 trên 50000 dòng."),
            "status": spec.get("status", "PENDING"),
            "edited_parameters": spec.get("edited_parameters"),
            "reviewer": spec.get("reviewer"),
            "review_note": spec.get("review_note"),
            "reviewed_at": spec.get("reviewed_at"),
            "created_at": spec.get("created_at"),
        }
        rules.append(rule)
    save_proposed_rules(run_id, "yellow_tripdata", rules)
    return rules


def _seed_rule(run_id: str, rule_id: str, **kwargs) -> dict[str, Any]:
    kwargs["rule_id"] = rule_id
    return _seed_rules(run_id, [kwargs])[0]


# ---------------------------------------------------------------------------
# GET /dq/runs/{run_id}/rules
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_rules_unknown_run_returns_404(steward_client):
    r = await steward_client.get("/api/v1/dq/runs/nonexistent_run/rules")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_rules_returns_empty_when_running(steward_client):
    """Run còn RUNNING → trả [] không phải 404."""
    from sqlalchemy.orm import Session

    import src.services.rule_store as rs
    from src.models.database import DatasetAccessModel
    from src.services.rule_store import create_run

    run_id = uuid.uuid4().hex
    create_run(run_id, "yellow_tripdata")  # status=QUEUED, không có rules
    _grant_steward_access("yellow_tripdata")

    with Session(rs._engine) as db_session:
        db_session.add(
            DatasetAccessModel(
                id=f"access-{uuid.uuid4().hex[:12]}",
                dataset_id="yellow_tripdata",
                username="steward",
                access_level="MANAGE",
                granted_by="system",
            )
        )
        db_session.commit()

    r = await steward_client.get(f"/api/v1/dq/runs/{run_id}/rules")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_list_rules_returns_rules_with_all_fields(steward_client):
    run_id = uuid.uuid4().hex
    _seed_run(run_id)
    _seed_rule(run_id, "t.vendor_id.NOT_NULL", dimension="COMPLETENESS")

    r = await steward_client.get(f"/api/v1/dq/runs/{run_id}/rules")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    row = data[0]
    assert row["rule_id"] == "t.vendor_id.NOT_NULL"
    assert row["dimension"] == "COMPLETENESS"
    assert "rule_description" in row
    assert "ai_reasoning" in row


@pytest.mark.asyncio
async def test_list_rules_filter_by_status(steward_client):
    run_id = uuid.uuid4().hex
    _seed_run(run_id)
    _seed_rules(
        run_id,
        [
            {"rule_id": "t.col_a.NOT_NULL", "status": "PENDING"},
            {"rule_id": "t.col_b.NOT_NULL", "column": "col_b", "status": "APPROVED"},
        ],
    )

    r = await steward_client.get(f"/api/v1/dq/runs/{run_id}/rules?status=PENDING")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["rule_id"] == "t.col_a.NOT_NULL"


@pytest.mark.asyncio
async def test_list_rules_filter_by_dimension(steward_client):
    run_id = uuid.uuid4().hex
    _seed_run(run_id)
    _seed_rules(
        run_id,
        [
            {"rule_id": "t.col_a.NOT_NULL", "dimension": "COMPLETENESS"},
            {"rule_id": "t.col_b.RANGE", "column": "col_b", "rule_type": "RANGE", "dimension": "VALIDITY"},
        ],
    )

    r = await steward_client.get(f"/api/v1/dq/runs/{run_id}/rules?dimension=COMPLETENESS")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["dimension"] == "COMPLETENESS"


# ---------------------------------------------------------------------------
# PATCH /dq/runs/{run_id}/rules/{rule_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_rule_unknown_run_returns_404(steward_client):
    r = await steward_client.patch(
        "/api/v1/dq/runs/unknown_run/rules/some.rule.NOT_NULL",
        json={"status": "APPROVED"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_patch_rule_unknown_rule_returns_404(steward_client):
    run_id = uuid.uuid4().hex
    _seed_run(run_id)

    r = await steward_client.patch(
        f"/api/v1/dq/runs/{run_id}/rules/nonexistent.rule.NOT_NULL",
        json={"status": "APPROVED"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_patch_rule_invalid_status_returns_422(steward_client):
    run_id = uuid.uuid4().hex
    _seed_run(run_id)
    _seed_rule(run_id, "t.col_a.NOT_NULL")

    r = await steward_client.patch(
        f"/api/v1/dq/runs/{run_id}/rules/t.col_a.NOT_NULL",
        json={"status": "MAYBE"},  # không hợp lệ
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_patch_rule_rejected_without_note_returns_422(steward_client):
    run_id = uuid.uuid4().hex
    _seed_run(run_id)
    _seed_rule(run_id, "t.col_a.NOT_NULL")

    r = await steward_client.patch(
        f"/api/v1/dq/runs/{run_id}/rules/t.col_a.NOT_NULL",
        json={"status": "REJECTED"},  # thiếu review_note
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_patch_rule_approve_success(steward_client):
    run_id = uuid.uuid4().hex
    _seed_run(run_id)
    _seed_rule(run_id, "t.col_a.NOT_NULL")

    r = await steward_client.patch(
        f"/api/v1/dq/runs/{run_id}/rules/t.col_a.NOT_NULL",
        # The body still names a reviewer, deliberately: the point of the assertion
        # below is that the server ignores it.
        json={"status": "APPROVED", "reviewer": "steward@ridepulse.vn"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "APPROVED"
    assert data["reviewer"] == "steward"
    assert data["reviewed_at"] is not None


@pytest.mark.asyncio
async def test_patch_rule_status_persists_after_get(steward_client):
    """PATCH approve → GET rules xác nhận status đổi."""
    run_id = uuid.uuid4().hex
    _seed_run(run_id)
    _seed_rule(run_id, "t.col_a.NOT_NULL")

    await steward_client.patch(
        f"/api/v1/dq/runs/{run_id}/rules/t.col_a.NOT_NULL",
        json={"status": "APPROVED", "reviewer": "s@t.vn"},
    )

    r = await steward_client.get(f"/api/v1/dq/runs/{run_id}/rules?status=APPROVED")
    assert r.status_code == 200
    assert len(r.json()) == 1


# ---------------------------------------------------------------------------
# POST /dq/runs/{run_id}/rules/bulk-review
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_review_unknown_run_returns_404(steward_client):
    r = await steward_client.post(
        "/api/v1/dq/runs/nonexistent/rules/bulk-review",
        json={"decisions": [{"rule_id": "x", "status": "APPROVED"}]},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_bulk_review_with_bad_id_returns_not_found_list(steward_client):
    run_id = uuid.uuid4().hex
    _seed_run(run_id)
    _seed_rule(run_id, "t.col_a.NOT_NULL")

    r = await steward_client.post(
        f"/api/v1/dq/runs/{run_id}/rules/bulk-review",
        json={
            "decisions": [
                {"rule_id": "t.col_a.NOT_NULL", "status": "APPROVED"},
                {"rule_id": "nonexistent.rule", "status": "APPROVED"},
            ]
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["updated_count"] == 1
    assert "nonexistent.rule" in data["not_found"]


# ---------------------------------------------------------------------------
# GET /dq/runs/{run_id}/review-summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_summary_unknown_run_returns_404(steward_client):
    r = await steward_client.get("/api/v1/dq/runs/nonexistent/review-summary")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_review_summary_counts_match(steward_client):
    run_id = uuid.uuid4().hex
    _seed_run(run_id)
    _seed_rules(
        run_id,
        [
            {"rule_id": "t.col_a.NOT_NULL", "dimension": "COMPLETENESS"},
            {"rule_id": "t.col_b.NOT_NULL", "column": "col_b", "dimension": "COMPLETENESS"},
        ],
    )

    # Approve 1
    await steward_client.patch(
        f"/api/v1/dq/runs/{run_id}/rules/t.col_a.NOT_NULL",
        json={"status": "APPROVED"},
    )

    r = await steward_client.get(f"/api/v1/dq/runs/{run_id}/review-summary")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 2
    assert data["approved"] == 1
    assert data["pending"] == 1
    assert data["is_complete"] is False


# ---------------------------------------------------------------------------
# GET /dq/runs/{run_id}/approved-rules
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approved_rules_only_returns_approved(steward_client):
    run_id = uuid.uuid4().hex
    _seed_run(run_id)
    _seed_rules(
        run_id,
        [
            {"rule_id": "t.col_a.NOT_NULL"},
            {"rule_id": "t.col_b.NOT_NULL", "column": "col_b"},
        ],
    )

    # Approve 1
    await steward_client.patch(
        f"/api/v1/dq/runs/{run_id}/rules/t.col_a.NOT_NULL",
        json={"status": "APPROVED"},
    )

    r = await steward_client.get(f"/api/v1/dq/runs/{run_id}/approved-rules")
    assert r.status_code == 200
    data = r.json()
    assert data["count"] == 1
    assert data["rules"][0]["rule_id"] == "t.col_a.NOT_NULL"
    assert data["rules"][0]["status"] == "APPROVED"
