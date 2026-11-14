"""Tests for the G4 Prefect feed (GET /traffic/readings)."""

from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from app.contract import compute_auth_hash
from app.main import create_app
from app.storage import RawReading, Storage


FEED_TOKEN = "g4-test-feed-token"


def _client(settings) -> TestClient:
    settings.g4_feed_token = FEED_TOKEN
    return TestClient(create_app(settings))


def _insert(
    storage: Storage,
    *,
    timestamp: str,
    status: str,
    index: float | None,
    lat: float = 14.6349,
    lon: float = -90.5069,
) -> str:
    reading_id = str(uuid4())
    storage.insert_reading(
        RawReading(
            reading_id=reading_id,
            timestamp=timestamp,
            lat=lat,
            lon=lon,
            traffic_congestion_index=index,
            status=status,
        )
    )
    return reading_id


def test_feed_unauthorized_without_header(settings) -> None:
    client = _client(settings)
    response = client.get("/traffic/readings")
    assert response.status_code == 401


def test_feed_unauthorized_bad_token(settings) -> None:
    client = _client(settings)
    response = client.get(
        "/traffic/readings",
        headers={"x-api-key": "wrong-token-value-x"},
    )
    assert response.status_code == 401


def test_feed_unauthorized_when_token_not_configured(settings) -> None:
    settings.g4_feed_token = None
    client = TestClient(create_app(settings))
    response = client.get(
        "/traffic/readings",
        headers={"x-api-key": FEED_TOKEN},
    )
    assert response.status_code == 401


def test_feed_empty_ok(settings) -> None:
    client = _client(settings)
    response = client.get(
        "/traffic/readings",
        headers={"x-api-key": FEED_TOKEN},
    )
    assert response.status_code == 200
    body = response.json()
    assert body == {"count": 0, "items": []}


def test_feed_excludes_invalid_and_failed(settings) -> None:
    storage = Storage(settings.db_path)
    kept_id = _insert(
        storage,
        timestamp="2026-10-10T22:30:00Z",
        status="DRY_RUN",
        index=0.4321,
    )
    _insert(
        storage,
        timestamp="2026-10-10T22:31:00Z",
        status="INVALID",
        index=0.1,
    )
    _insert(
        storage,
        timestamp="2026-10-10T22:32:00Z",
        status="FAILED",
        index=None,
    )
    _insert(
        storage,
        timestamp="2026-10-10T22:33:00Z",
        status="SENT",
        index=None,
    )

    client = _client(settings)
    response = client.get(
        "/traffic/readings",
        headers={"x-api-key": FEED_TOKEN},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["items"][0]["reading_id"] == kept_id


def test_feed_since_filter_and_contract_shape(settings) -> None:
    storage = Storage(settings.db_path)
    old_id = _insert(
        storage,
        timestamp="2026-10-10T20:00:00Z",
        status="SENT",
        index=0.1111,
    )
    new_id = _insert(
        storage,
        timestamp="2026-10-10T22:30:00Z",
        status="DRY_RUN",
        index=0.4321,
    )

    client = _client(settings)
    response = client.get(
        "/traffic/readings",
        params={"since": "2026-10-10T21:00:00Z"},
        headers={"x-api-key": FEED_TOKEN},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    item = body["items"][0]
    assert item["reading_id"] == new_id
    assert item["reading_id"] != old_id
    assert item["timestamp"] == "2026-10-10T22:30:00Z"
    assert item["latitude"] == 14.6349
    assert item["longitude"] == -90.5069
    assert item["student_id"] == settings.student_id
    assert item["auth_hash"] == compute_auth_hash(
        settings.student_id,
        "2026-10-10T22:30:00Z",
        settings.secret_key,
    )
    assert item["domain_data"] == {
        "noise_db": None,
        "traffic_congestion_index": 0.4321,
        "air_quality_aqi": None,
    }
    assert set(item.keys()) == {
        "reading_id",
        "timestamp",
        "latitude",
        "longitude",
        "student_id",
        "auth_hash",
        "domain_data",
    }


def test_feed_rejects_bad_since(settings) -> None:
    client = _client(settings)
    response = client.get(
        "/traffic/readings",
        params={"since": "2026-10-10 22:30:00"},
        headers={"x-api-key": FEED_TOKEN},
    )
    assert response.status_code == 422
