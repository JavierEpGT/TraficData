"""Postgres backend tests. Skipped unless DATABASE_URL_TEST is set."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

from app.storage import RawReading, Storage

DATABASE_URL_TEST = os.environ.get("DATABASE_URL_TEST", "").strip()

pytestmark = pytest.mark.skipif(
    not DATABASE_URL_TEST,
    reason="DATABASE_URL_TEST not set",
)


@pytest.fixture
def pg_storage() -> Storage:
    assert DATABASE_URL_TEST
    storage = Storage(db_path="unused.db", database_url=DATABASE_URL_TEST)
    inserted: list[str] = []
    original_insert = storage.insert_reading

    def _tracking_insert(reading: RawReading) -> None:
        original_insert(reading)
        inserted.append(reading.reading_id)

    storage.insert_reading = _tracking_insert  # type: ignore[method-assign]
    yield storage
    _cleanup(inserted)


def _cleanup(reading_ids: list[str]) -> None:
    if not reading_ids or not DATABASE_URL_TEST:
        return
    import psycopg

    with psycopg.connect(DATABASE_URL_TEST, connect_timeout=15) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM readings_raw WHERE reading_id = ANY(%s)",
                (reading_ids,),
            )
        conn.commit()


def _reading(
    *,
    timestamp: str,
    status: str = "DRY_RUN",
    index: float | None = 0.4321,
    lat: float = 14.6349,
    lon: float = -90.5069,
) -> RawReading:
    return RawReading(
        reading_id=str(uuid4()),
        timestamp=timestamp,
        lat=lat,
        lon=lon,
        dest_lat=14.6385,
        dest_lon=-90.5069,
        t_traffic_s=80.0,
        t_free_s=60.0,
        dist_traffic_m=420.0,
        dist_free_m=410.0,
        congestion_numeric_avg=20.0,
        traffic_congestion_index=index,
        status=status,
    )


def test_pg_insert_and_fetch_all(pg_storage: Storage) -> None:
    reading = _reading(timestamp="2099-01-01T22:30:00Z")
    pg_storage.insert_reading(reading)
    rows = pg_storage.fetch_all()
    match = next(r for r in rows if r["reading_id"] == reading.reading_id)
    assert match["status"] == "DRY_RUN"
    assert match["lat"] == pytest.approx(14.6349)
    assert match["lon"] == pytest.approx(-90.5069)
    assert match["t_traffic_s"] == pytest.approx(80.0)
    assert match["traffic_congestion_index"] == pytest.approx(0.4321)


def test_pg_fetch_valid_readings_since_inclusive(pg_storage: Storage) -> None:
    boundary = _reading(timestamp="2099-01-01T21:00:00Z", status="SENT", index=0.1111)
    later = _reading(timestamp="2099-01-01T22:30:00Z", status="DRY_RUN", index=0.4321)
    earlier = _reading(timestamp="2099-01-01T20:00:00Z", status="SENT", index=0.2222)
    invalid = _reading(timestamp="2099-01-01T22:31:00Z", status="INVALID", index=0.1)
    pg_storage.insert_reading(earlier)
    pg_storage.insert_reading(boundary)
    pg_storage.insert_reading(later)
    pg_storage.insert_reading(invalid)

    rows = pg_storage.fetch_valid_readings(since="2099-01-01T21:00:00Z", limit=100)
    ids = {r["reading_id"] for r in rows}
    assert boundary.reading_id in ids
    assert later.reading_id in ids
    assert earlier.reading_id not in ids
    assert invalid.reading_id not in ids


def test_pg_export_csv(pg_storage: Storage, tmp_path) -> None:
    reading = _reading(timestamp="2099-01-01T22:30:00Z")
    pg_storage.insert_reading(reading)
    out = tmp_path / "readings.csv"
    # fetch_all includes pre-existing rows; export must succeed and contain ours
    count = pg_storage.export_csv(str(out))
    assert count >= 1
    text = out.read_text(encoding="utf-8")
    assert "reading_id" in text
    assert reading.reading_id in text
