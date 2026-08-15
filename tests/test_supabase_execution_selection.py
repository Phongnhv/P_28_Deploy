from types import SimpleNamespace

import pytest

from src.services import job_runner


def _settings(backend: str, database_url: str, supabase_database_url: str | None = None):
    return SimpleNamespace(
        dq_execution_backend=backend,
        database_url=database_url,
        supabase_database_url=supabase_database_url,
    )


def test_auto_uses_postgres_as_canonical_supabase_source(monkeypatch):
    monkeypatch.setattr(
        job_runner,
        "get_settings",
        lambda: _settings("auto", "postgresql://example.invalid/ridepulse"),
    )
    assert job_runner._supabase_source_url() == "postgresql://example.invalid/ridepulse"


def test_auto_uses_explicit_supabase_source_alongside_sqlite_metadata(monkeypatch):
    monkeypatch.setattr(
        job_runner,
        "get_settings",
        lambda: _settings("auto", "sqlite:///ui_local_mvp.db", "postgresql://example.invalid/ridepulse"),
    )
    assert job_runner._supabase_source_url() == "postgresql://example.invalid/ridepulse"


def test_local_mode_keeps_sqlite_fallback(monkeypatch):
    monkeypatch.setattr(
        job_runner,
        "get_settings",
        lambda: _settings("local", "postgresql://example.invalid/ridepulse"),
    )
    assert job_runner._supabase_source_url() is None


def test_forced_supabase_rejects_non_postgres_url(monkeypatch):
    monkeypatch.setattr(
        job_runner,
        "get_settings",
        lambda: _settings("supabase", "sqlite:///ui_local_mvp.db"),
    )
    with pytest.raises(ValueError, match="Supabase execution requires"):
        job_runner._supabase_source_url()
