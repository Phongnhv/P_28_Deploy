"""Integration tests cho HITL REST API — /dq/runs/{run_id}/rules/*

Dùng fixture client (ASGITransport, không boot lifespan → gọi init_db() thủ công).
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

    # Set _engine trực tiếp — thread pool workers cũng thấy engine đúng
    original = rs._engine
    rs._engine = test_engine
    yield
    rs._engine = original
    test_engine.dispose()


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _seed_run(run_id: str, dataset_id: str = "yellow_tripdata") -> None:
    from src.services.rule_store import create_run, update_run_status

    create_run(run_id, dataset_id)
    update_run_status(run_id, "DONE")


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
async def test_list_rules_unknown_run_returns_404(client):
    r = await client.get("/api/v1/dq/runs/nonexistent_run/rules")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_rules_returns_empty_when_running(client):
    """Run còn RUNNING → trả [] không phải 404."""
    from src.services.rule_store import create_run

    run_id = uuid.uuid4().hex
    create_run(run_id, "yellow_tripdata")  # status=QUEUED, không có rules

    r = await client.get(f"/api/v1/dq/runs/{run_id}/rules")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_list_rules_returns_rules_with_all_fields(client):
    run_id = uuid.uuid4().hex
    _seed_run(run_id)
    _seed_rule(run_id, "t.vendor_id.NOT_NULL", dimension="COMPLETENESS")

    r = await client.get(f"/api/v1/dq/runs/{run_id}/rules")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    row = data[0]
    assert row["rule_id"] == "t.vendor_id.NOT_NULL"
    assert row["dimension"] == "COMPLETENESS"
    assert "rule_description" in row
    assert "ai_reasoning" in row


@pytest.mark.asyncio
async def test_list_rules_filter_by_status(client):
    run_id = uuid.uuid4().hex
    _seed_run(run_id)
    _seed_rules(
        run_id,
        [
            {"rule_id": "t.col_a.NOT_NULL", "status": "PENDING"},
            {"rule_id": "t.col_b.NOT_NULL", "column": "col_b", "status": "APPROVED"},
        ],
    )

    r = await client.get(f"/api/v1/dq/runs/{run_id}/rules?status=PENDING")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["rule_id"] == "t.col_a.NOT_NULL"


@pytest.mark.asyncio
async def test_list_rules_filter_by_dimension(client):
    run_id = uuid.uuid4().hex
    _seed_run(run_id)
    _seed_rules(
        run_id,
        [
            {"rule_id": "t.col_a.NOT_NULL", "dimension": "COMPLETENESS"},
            {"rule_id": "t.col_b.RANGE", "column": "col_b", "rule_type": "RANGE", "dimension": "VALIDITY"},
        ],
    )

    r = await client.get(f"/api/v1/dq/runs/{run_id}/rules?dimension=COMPLETENESS")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["dimension"] == "COMPLETENESS"


# ---------------------------------------------------------------------------
# PATCH /dq/runs/{run_id}/rules/{rule_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_rule_unknown_run_returns_404(client):
    r = await client.patch(
        "/api/v1/dq/runs/unknown_run/rules/some.rule.NOT_NULL",
        json={"status": "APPROVED"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_patch_rule_unknown_rule_returns_404(client):
    run_id = uuid.uuid4().hex
    _seed_run(run_id)

    r = await client.patch(
        f"/api/v1/dq/runs/{run_id}/rules/nonexistent.rule.NOT_NULL",
        json={"status": "APPROVED"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_patch_rule_invalid_status_returns_422(client):
    run_id = uuid.uuid4().hex
    _seed_run(run_id)
    _seed_rule(run_id, "t.col_a.NOT_NULL")

    r = await client.patch(
        f"/api/v1/dq/runs/{run_id}/rules/t.col_a.NOT_NULL",
        json={"status": "MAYBE"},  # không hợp lệ
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_patch_rule_rejected_without_note_returns_422(client):
    run_id = uuid.uuid4().hex
    _seed_run(run_id)
    _seed_rule(run_id, "t.col_a.NOT_NULL")

    r = await client.patch(
        f"/api/v1/dq/runs/{run_id}/rules/t.col_a.NOT_NULL",
        json={"status": "REJECTED"},  # thiếu review_note
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_patch_rule_approve_success(client):
    run_id = uuid.uuid4().hex
    _seed_run(run_id)
    _seed_rule(run_id, "t.col_a.NOT_NULL")

    r = await client.patch(
        f"/api/v1/dq/runs/{run_id}/rules/t.col_a.NOT_NULL",
        json={"status": "APPROVED", "reviewer": "steward@ridepulse.vn"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "APPROVED"
    assert data["reviewer"] == "steward@ridepulse.vn"
    assert data["reviewed_at"] is not None


@pytest.mark.asyncio
async def test_patch_rule_status_persists_after_get(client):
    """PATCH approve → GET rules xác nhận status đổi."""
    run_id = uuid.uuid4().hex
    _seed_run(run_id)
    _seed_rule(run_id, "t.col_a.NOT_NULL")

    await client.patch(
        f"/api/v1/dq/runs/{run_id}/rules/t.col_a.NOT_NULL",
        json={"status": "APPROVED", "reviewer": "s@t.vn"},
    )

    r = await client.get(f"/api/v1/dq/runs/{run_id}/rules?status=APPROVED")
    assert r.status_code == 200
    assert len(r.json()) == 1


# ---------------------------------------------------------------------------
# POST /dq/runs/{run_id}/rules/bulk-review
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_review_unknown_run_returns_404(client):
    r = await client.post(
        "/api/v1/dq/runs/nonexistent/rules/bulk-review",
        json={"decisions": [{"rule_id": "x", "status": "APPROVED"}]},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_bulk_review_with_bad_id_returns_not_found_list(client):
    run_id = uuid.uuid4().hex
    _seed_run(run_id)
    _seed_rule(run_id, "t.col_a.NOT_NULL")

    r = await client.post(
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
async def test_review_summary_unknown_run_returns_404(client):
    r = await client.get("/api/v1/dq/runs/nonexistent/review-summary")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_review_summary_counts_match(client):
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
    await client.patch(
        f"/api/v1/dq/runs/{run_id}/rules/t.col_a.NOT_NULL",
        json={"status": "APPROVED"},
    )

    r = await client.get(f"/api/v1/dq/runs/{run_id}/review-summary")
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
async def test_approved_rules_only_returns_approved(client):
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
    await client.patch(
        f"/api/v1/dq/runs/{run_id}/rules/t.col_a.NOT_NULL",
        json={"status": "APPROVED"},
    )

    r = await client.get(f"/api/v1/dq/runs/{run_id}/approved-rules")
    assert r.status_code == 200
    data = r.json()
    assert data["count"] == 1
    assert data["rules"][0]["rule_id"] == "t.col_a.NOT_NULL"
    assert data["rules"][0]["status"] == "APPROVED"
