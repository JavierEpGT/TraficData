"""Dedup helpers: one valid reading per coordinate per UTC day."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.storage import RawReading, Storage


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


def test_exists_valid_reading_today_true_for_dry_run(tmp_path) -> None:
    storage = Storage(str(tmp_path / "dedup.db"))
    storage.insert_reading(_reading(timestamp="2099-06-15T10:00:00Z"))
    now = datetime(2099, 6, 15, 18, 0, 0, tzinfo=timezone.utc)
    assert storage.exists_valid_reading_today(14.6349, -90.5069, now=now) is True


def test_exists_valid_reading_today_false_for_failed_only(tmp_path) -> None:
    storage = Storage(str(tmp_path / "dedup.db"))
    storage.insert_reading(
        _reading(timestamp="2099-06-15T10:00:00Z", status="FAILED", index=None)
    )
    now = datetime(2099, 6, 15, 18, 0, 0, tzinfo=timezone.utc)
    assert storage.exists_valid_reading_today(14.6349, -90.5069, now=now) is False


def test_exists_valid_reading_today_false_for_invalid_only(tmp_path) -> None:
    storage = Storage(str(tmp_path / "dedup.db"))
    storage.insert_reading(
        _reading(timestamp="2099-06-15T10:00:00Z", status="INVALID", index=0.1)
    )
    now = datetime(2099, 6, 15, 18, 0, 0, tzinfo=timezone.utc)
    assert storage.exists_valid_reading_today(14.6349, -90.5069, now=now) is False


def test_exists_valid_reading_today_false_other_day(tmp_path) -> None:
    storage = Storage(str(tmp_path / "dedup.db"))
    storage.insert_reading(_reading(timestamp="2099-06-14T23:59:00Z"))
    now = datetime(2099, 6, 15, 0, 30, 0, tzinfo=timezone.utc)
    assert storage.exists_valid_reading_today(14.6349, -90.5069, now=now) is False


def test_exists_valid_reading_today_rounds_coordinates(tmp_path) -> None:
    storage = Storage(str(tmp_path / "dedup.db"))
    storage.insert_reading(_reading(timestamp="2099-06-15T10:00:00Z", lat=14.63491, lon=-90.50689))
    now = datetime(2099, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    assert storage.exists_valid_reading_today(14.634912, -90.506891, now=now) is True


def test_fetch_latest_valid_per_coord_keeps_newest(tmp_path) -> None:
    storage = Storage(str(tmp_path / "latest.db"))
    older = _reading(timestamp="2099-06-14T10:00:00Z", index=0.1)
    newer = _reading(timestamp="2099-06-15T12:00:00Z", index=0.9)
    other = _reading(
        timestamp="2099-06-15T11:00:00Z",
        lat=14.7,
        lon=-90.6,
        index=0.5,
    )
    failed = _reading(
        timestamp="2099-06-16T10:00:00Z",
        status="FAILED",
        index=None,
        lat=14.6349,
        lon=-90.5069,
    )
    storage.insert_reading(older)
    storage.insert_reading(newer)
    storage.insert_reading(other)
    storage.insert_reading(failed)

    rows = storage.fetch_latest_valid_per_coord()
    assert len(rows) == 2
    by_id = {r["reading_id"]: r for r in rows}
    assert newer.reading_id in by_id
    assert other.reading_id in by_id
    assert older.reading_id not in by_id
    assert failed.reading_id not in by_id
    assert by_id[newer.reading_id]["traffic_congestion_index"] == 0.9


def test_export_csv_uses_latest_valid_per_coord(tmp_path) -> None:
    storage = Storage(str(tmp_path / "export.db"))
    storage.insert_reading(_reading(timestamp="2099-06-14T10:00:00Z", index=0.1))
    latest = _reading(timestamp="2099-06-15T12:00:00Z", index=0.9)
    storage.insert_reading(latest)
    storage.insert_reading(
        _reading(timestamp="2099-06-15T11:00:00Z", lat=14.7, lon=-90.6, index=0.5)
    )
    out = tmp_path / "readings.csv"
    count = storage.export_csv(str(out))
    assert count == 2
    text = out.read_text(encoding="utf-8")
    assert latest.reading_id in text
    assert text.count("\n") == 3  # header + 2 data rows
