"""Shared fixtures. Mapbox/DW are mocked only in tests."""

from __future__ import annotations

import os

import pytest

# Ensure required settings exist before any Settings() instantiation
os.environ.setdefault("MAPBOX_ACCESS_TOKEN", "test-mapbox-token")
os.environ.setdefault("STUDENT_ID", "g2-test-student")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("DRY_RUN", "true")
os.environ.setdefault("SEGMENT_LENGTH_M", "400")
os.environ.setdefault("SEGMENT_BEARING_DEG", "0")
# Never let the suite hit Neon / production Postgres
os.environ["DATABASE_URL"] = ""

from app.config import Settings, get_settings
from app.mapbox_client import PROFILE_FREE, PROFILE_TRAFFIC


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings(tmp_path) -> Settings:
    get_settings.cache_clear()
    return Settings(
        MAPBOX_ACCESS_TOKEN="test-mapbox-token",
        STUDENT_ID="g2-test-student",
        SECRET_KEY="test-secret-key",
        DRY_RUN=True,
        SEGMENT_LENGTH_M=400,
        SEGMENT_BEARING_DEG=0,
        DB_PATH=str(tmp_path / "test.db"),
        DATABASE_URL=None,
        DW_ENDPOINT_URL=None,
        DW_AUTH_TOKEN=None,
    )


def traffic_ok_body(
    *,
    duration: float = 80.0,
    distance: float = 400.0,
    congestion: list[float | None] | None = None,
) -> dict:
    cong = congestion if congestion is not None else [10, 20, None, 30]
    return {
        "code": "Ok",
        "routes": [
            {
                "duration": duration,
                "distance": distance,
                "legs": [
                    {
                        "annotation": {
                            "congestion_numeric": cong,
                            "duration": [1, 2, 3, 4],
                            "distance": [100, 100, 100, 100],
                        }
                    }
                ],
            }
        ],
    }


def free_ok_body(*, duration: float = 60.0, distance: float = 400.0) -> dict:
    return {
        "code": "Ok",
        "routes": [{"duration": duration, "distance": distance, "legs": [{}]}],
    }


def mapbox_url(profile: str, lon1: float, lat1: float, lon2: float, lat2: float) -> str:
    return (
        f"https://api.mapbox.com/directions/v5/{profile}/"
        f"{lon1},{lat1};{lon2},{lat2}"
    )


__all__ = [
    "PROFILE_FREE",
    "PROFILE_TRAFFIC",
    "free_ok_body",
    "mapbox_url",
    "settings",
    "traffic_ok_body",
]
