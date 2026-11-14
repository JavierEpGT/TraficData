"""Local storage for raw traffic metrics (Looker Studio source).

SQLite is the default when DATABASE_URL is empty. Postgres (Neon) is used
when a connection URL is provided.
"""

from __future__ import annotations

import csv
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS readings_raw (
    reading_id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    dest_lat REAL,
    dest_lon REAL,
    t_traffic_s REAL,
    t_free_s REAL,
    dist_traffic_m REAL,
    dist_free_m REAL,
    congestion_numeric_avg REAL,
    traffic_congestion_index REAL,
    status TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL
)
"""

CREATE_TABLE_SQL_PG = """
CREATE TABLE IF NOT EXISTS readings_raw (
    reading_id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    lat DOUBLE PRECISION NOT NULL,
    lon DOUBLE PRECISION NOT NULL,
    dest_lat DOUBLE PRECISION,
    dest_lon DOUBLE PRECISION,
    t_traffic_s DOUBLE PRECISION,
    t_free_s DOUBLE PRECISION,
    dist_traffic_m DOUBLE PRECISION,
    dist_free_m DOUBLE PRECISION,
    congestion_numeric_avg DOUBLE PRECISION,
    traffic_congestion_index DOUBLE PRECISION,
    status TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL
)
"""

INSERT_SQL_SQLITE = """
INSERT INTO readings_raw (
    reading_id, timestamp, lat, lon, dest_lat, dest_lon,
    t_traffic_s, t_free_s, dist_traffic_m, dist_free_m,
    congestion_numeric_avg, traffic_congestion_index,
    status, error, created_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

INSERT_SQL_PG = """
INSERT INTO readings_raw (
    reading_id, timestamp, lat, lon, dest_lat, dest_lon,
    t_traffic_s, t_free_s, dist_traffic_m, dist_free_m,
    congestion_numeric_avg, traffic_congestion_index,
    status, error, created_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


@dataclass
class RawReading:
    """Row written to readings_raw."""

    reading_id: str
    timestamp: str
    lat: float
    lon: float
    dest_lat: float | None = None
    dest_lon: float | None = None
    t_traffic_s: float | None = None
    t_free_s: float | None = None
    dist_traffic_m: float | None = None
    dist_free_m: float | None = None
    congestion_numeric_avg: float | None = None
    traffic_congestion_index: float | None = None
    status: str = "FAILED"
    error: str | None = None
    created_at: str | None = None


class Storage:
    """Persistence for raw readings (SQLite or Postgres)."""

    def __init__(self, db_path: str, database_url: str | None = None) -> None:
        self.db_path = db_path
        self.database_url = (database_url or "").strip() or None
        if self.database_url:
            self._init_db_pg()
        else:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _connect_pg(self):
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(
            self.database_url,
            row_factory=dict_row,
            connect_timeout=15,
        )

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(CREATE_TABLE_SQL)
            conn.commit()

    def _init_db_pg(self) -> None:
        with self._connect_pg() as conn:
            conn.execute(CREATE_TABLE_SQL_PG)
            conn.commit()

    def insert_reading(self, reading: RawReading) -> None:
        if self.database_url:
            self._insert_reading_pg(reading)
        else:
            self._insert_reading_sqlite(reading)

    def _insert_reading_sqlite(self, reading: RawReading) -> None:
        created_at = reading.created_at or datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        with self._connect() as conn:
            conn.execute(
                INSERT_SQL_SQLITE,
                (
                    reading.reading_id,
                    reading.timestamp,
                    reading.lat,
                    reading.lon,
                    reading.dest_lat,
                    reading.dest_lon,
                    reading.t_traffic_s,
                    reading.t_free_s,
                    reading.dist_traffic_m,
                    reading.dist_free_m,
                    reading.congestion_numeric_avg,
                    reading.traffic_congestion_index,
                    reading.status,
                    reading.error,
                    created_at,
                ),
            )
            conn.commit()
        logger.info("Stored reading_id=%s status=%s", reading.reading_id, reading.status)

    def _insert_reading_pg(self, reading: RawReading) -> None:
        created_at = reading.created_at or datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        with self._connect_pg() as conn:
            conn.execute(
                INSERT_SQL_PG,
                (
                    reading.reading_id,
                    reading.timestamp,
                    reading.lat,
                    reading.lon,
                    reading.dest_lat,
                    reading.dest_lon,
                    reading.t_traffic_s,
                    reading.t_free_s,
                    reading.dist_traffic_m,
                    reading.dist_free_m,
                    reading.congestion_numeric_avg,
                    reading.traffic_congestion_index,
                    reading.status,
                    reading.error,
                    created_at,
                ),
            )
            conn.commit()
        logger.info("Stored reading_id=%s status=%s", reading.reading_id, reading.status)

    def fetch_all(self) -> list[dict[str, Any]]:
        if self.database_url:
            return self._fetch_all_pg()
        return self._fetch_all_sqlite()

    def _fetch_all_sqlite(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM readings_raw ORDER BY created_at ASC"
            ).fetchall()
        return [dict(row) for row in rows]

    def _fetch_all_pg(self) -> list[dict[str, Any]]:
        with self._connect_pg() as conn:
            rows = conn.execute(
                "SELECT * FROM readings_raw ORDER BY created_at ASC"
            ).fetchall()
        return [dict(row) for row in rows]

    def fetch_valid_readings(
        self,
        *,
        since: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Valid readings for the G4 Prefect feed (DRY_RUN / SENT with index)."""
        if self.database_url:
            return self._fetch_valid_readings_pg(since=since, limit=limit)
        return self._fetch_valid_readings_sqlite(since=since, limit=limit)

    def _fetch_valid_readings_sqlite(
        self,
        *,
        since: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        sql = """
            SELECT * FROM readings_raw
            WHERE status IN ('DRY_RUN', 'SENT')
              AND traffic_congestion_index IS NOT NULL
        """
        params: list[Any] = []
        if since is not None:
            sql += " AND timestamp >= ?"
            params.append(since)
        sql += " ORDER BY timestamp ASC, reading_id ASC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def _fetch_valid_readings_pg(
        self,
        *,
        since: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        sql = """
            SELECT * FROM readings_raw
            WHERE status IN ('DRY_RUN', 'SENT')
              AND traffic_congestion_index IS NOT NULL
        """
        params: list[Any] = []
        if since is not None:
            sql += " AND timestamp >= %s"
            params.append(since)
        sql += " ORDER BY timestamp ASC, reading_id ASC LIMIT %s"
        params.append(limit)
        with self._connect_pg() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def export_csv(self, out_path: str) -> int:
        rows = self.fetch_all()
        fieldnames = [
            "reading_id",
            "timestamp",
            "lat",
            "lon",
            "dest_lat",
            "dest_lon",
            "t_traffic_s",
            "t_free_s",
            "dist_traffic_m",
            "dist_free_m",
            "congestion_numeric_avg",
            "traffic_congestion_index",
            "status",
            "error",
            "created_at",
        ]
        path = Path(out_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row.get(k) for k in fieldnames})
        return len(rows)


def iter_points_from_rows(rows: Iterable[dict[str, Any]]) -> list[tuple[float, float]]:
    """Helper for tests / tooling."""
    return [(float(r["lat"]), float(r["lon"])) for r in rows]
