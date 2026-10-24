"""Settings validation (Render requires Neon)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings


def _base_kwargs(tmp_path, **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "MAPBOX_ACCESS_TOKEN": "test-mapbox-token",
        "STUDENT_ID": "g2-test-student",
        "SECRET_KEY": "test-secret-key",
        "DRY_RUN": True,
        "DB_PATH": str(tmp_path / "test.db"),
        "DATABASE_URL": None,
    }
    values.update(overrides)
    return values


def test_local_sqlite_allowed_without_database_url(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("RENDER", raising=False)
    settings = Settings(**_base_kwargs(tmp_path))
    assert settings.database_url is None


def test_render_requires_database_url(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RENDER", "true")
    with pytest.raises(ValidationError, match="DATABASE_URL is required on Render"):
        Settings(**_base_kwargs(tmp_path))


def test_render_accepts_neon_database_url(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RENDER", "true")
    settings = Settings(
        **_base_kwargs(
            tmp_path,
            DATABASE_URL="postgresql://user:pass@host/db?sslmode=require",
        )
    )
    assert settings.database_url is not None
