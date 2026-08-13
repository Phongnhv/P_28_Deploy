from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine

import src.services.rule_store as rule_store
from src.config import get_settings
from src.main import app
from src.services.rule_store import init_db


@pytest.fixture(autouse=True)
def test_db(tmp_path):
    """
    Creates an isolated, temporary SQLite database for each test.
    """
    db_file = tmp_path / "test_steward.db"
    db_url = f"sqlite:///{db_file.as_posix()}"

    # Override settings
    settings = get_settings()
    original_db_url = settings.database_url
    settings.database_url = db_url

    # Create engine and monkey-patch the cached engine in rule_store
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
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
