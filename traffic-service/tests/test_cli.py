"""CLI guards: --from-g1 must persist to Neon."""

from __future__ import annotations

from argparse import Namespace

from app.cli import _storage_backend, cmd_run


def test_storage_backend_labels() -> None:
    assert _storage_backend(None) == "sqlite"
    assert _storage_backend("") == "sqlite"
    assert _storage_backend("postgresql://user:pass@host/db") == "postgres"


def test_from_g1_requires_database_url(settings, caplog) -> None:
    settings.database_url = None
    from app.config import get_settings

    get_settings.cache_clear()

    import app.cli as cli

    original = cli.get_settings
    cli.get_settings = lambda: settings
    try:
        code = cmd_run(Namespace(from_g1=True, csv=None, loop=False, interval=300))
    finally:
        cli.get_settings = original

    assert code == 1
    assert any("DATABASE_URL" in rec.message for rec in caplog.records)
