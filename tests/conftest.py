from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

import src.services.rule_store as rule_store
from src.config import get_settings
from src.main import app
from src.services.rule_store import init_db


@pytest.fixture(autouse=True)
def test_db():
    """
    Creates an isolated, temporary in-memory SQLite database for each test.
    """
    db_url = "sqlite://"

    # Override settings
    settings = get_settings()
    original_db_url = settings.database_url
    settings.database_url = db_url

    # Create engine with StaticPool to share connection across threads/sessions
    engine = create_engine(
        db_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    original_engine = rule_store._engine
    rule_store._engine = engine

    # Create all tables & seed default data
    init_db()

    yield engine

    # Restore original settings and engine
    rule_store._engine = original_engine
    settings.database_url = original_db_url
    engine.dispose()

@pytest_asyncio.fixture
async def client():
    """Async HTTP client for testing API endpoints."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

@pytest.fixture
def mock_llm():
    mock = AsyncMock()
    mock.ainvoke.return_value = AsyncMock(content="Mocked LLM response")
    return mock


@pytest.fixture(autouse=True)
def isolate_output_dir(monkeypatch):
    """Tự động chuyển hướng output_dir và results_dir vào thư mục output_pytest/ trong repo khi chạy pytest.

    Giúp thư mục output/ trên workspace luôn sạch sẽ (chỉ chứa file chạy thật), còn file do pytest sinh ra gom vào output_pytest/.
    """
    from pathlib import Path

    from src.config import get_settings

    repo_root = Path(__file__).resolve().parent.parent
    test_output = repo_root / "output_pytest"
    test_output.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("OUTPUT_DIR", str(test_output))
    monkeypatch.setenv("RESULTS_DIR", str(test_output))

    settings = get_settings()
    monkeypatch.setattr(settings, "output_dir", str(test_output))
    monkeypatch.setattr(settings, "results_dir", str(test_output))

    yield test_output
