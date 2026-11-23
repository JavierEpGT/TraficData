"""One-shot: keep one reading per (lat, lon, UTC day) in readings_raw.

Preference order within each group:
1. Valid rows (DRY_RUN / SENT with traffic_congestion_index)
2. Newest timestamp, then created_at, then reading_id

Usage (from traffic-service/):
  python scripts/dedupe_readings_raw.py --dry-run
  python scripts/dedupe_readings_raw.py --apply
Requires DATABASE_URL in the environment or .env.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Allow `python scripts/dedupe_readings_raw.py` from traffic-service/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logger = logging.getLogger(__name__)

COUNT_DUPES_SQL = """
WITH ranked AS (
    SELECT
        reading_id,
        ROW_NUMBER() OVER (
            PARTITION BY
                ROUND(lat::numeric, 5),
                ROUND(lon::numeric, 5),
                (timestamp::timestamptz AT TIME ZONE 'UTC')::date
            ORDER BY
                CASE
                    WHEN status IN ('DRY_RUN', 'SENT')
                     AND traffic_congestion_index IS NOT NULL
                    THEN 0
                    ELSE 1
                END,
                timestamp DESC,
                created_at DESC,
                reading_id DESC
        ) AS rn
    FROM readings_raw
)
SELECT COUNT(*) AS duplicate_rows FROM ranked WHERE rn > 1
"""

DELETE_DUPES_SQL = """
WITH ranked AS (
    SELECT
        reading_id,
        ROW_NUMBER() OVER (
            PARTITION BY
                ROUND(lat::numeric, 5),
                ROUND(lon::numeric, 5),
                (timestamp::timestamptz AT TIME ZONE 'UTC')::date
            ORDER BY
                CASE
                    WHEN status IN ('DRY_RUN', 'SENT')
                     AND traffic_congestion_index IS NOT NULL
                    THEN 0
                    ELSE 1
                END,
                timestamp DESC,
                created_at DESC,
                reading_id DESC
        ) AS rn
    FROM readings_raw
)
DELETE FROM readings_raw
WHERE reading_id IN (SELECT reading_id FROM ranked WHERE rn > 1)
"""


def _load_database_url() -> str:
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if url:
        return url
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == "DATABASE_URL":
                return value.strip()
    raise SystemExit("DATABASE_URL is required (env or traffic-service/.env)")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Count duplicate rows that would be deleted",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Delete duplicate rows (keep one per coord+UTC day)",
    )
    args = parser.parse_args(argv)

    database_url = _load_database_url()
    import psycopg

    with psycopg.connect(database_url, connect_timeout=30) as conn:
        with conn.cursor() as cur:
            cur.execute(COUNT_DUPES_SQL)
            row = cur.fetchone()
            dupes = int(row[0]) if row else 0
            logger.info("duplicate_rows=%s", dupes)
            if args.dry_run:
                print(f"Would delete {dupes} duplicate rows")
                return 0
            if dupes == 0:
                print("Nothing to delete")
                return 0
            cur.execute(DELETE_DUPES_SQL)
            deleted = cur.rowcount
            conn.commit()
            logger.info("deleted_rows=%s", deleted)
            print(f"Deleted {deleted} duplicate rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
