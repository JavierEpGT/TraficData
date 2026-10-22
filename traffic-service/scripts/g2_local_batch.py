#!/usr/bin/env python3
"""
G2 local batch (no DW / no other groups required).

1) Coverage check → append coverage_log.csv
2) Ingest all CSV points → SQLite/Postgres (DRY_RUN by default)
3) Export readings.csv for Looker Studio

Usage (from traffic-service/):
  python scripts/g2_local_batch.py --csv data/points.csv
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.main import process_reading
from app.sources import CsvSource
from app.storage import Storage

logger = logging.getLogger(__name__)


def run_coverage(csv_path: str, log_path: str) -> int:
    script = ROOT / "scripts" / "coverage_check.py"
    cmd = [
        sys.executable,
        str(script),
        "--csv",
        csv_path,
        "--log",
        log_path,
    ]
    logger.info("Running coverage: %s", " ".join(cmd))
    completed = subprocess.run(cmd, cwd=str(ROOT), check=False)
    return int(completed.returncode)


def run_ingest(csv_path: str) -> Counter[str]:
    get_settings.cache_clear()
    settings = get_settings()
    points = CsvSource(csv_path).get_points()
    storage = Storage(settings.db_path, settings.database_url)
    counts: Counter[str] = Counter()

    logger.info("Ingesting %s points (dry_run=%s)", len(points), settings.dry_run)
    for lat, lon in points:
        result = process_reading(lat, lon, settings, storage=storage)
        counts[result.status] += 1
        logger.info(
            "point=(%.5f,%.5f) status=%s error=%s",
            lat,
            lon,
            result.status,
            result.error,
        )
    return counts


def run_export(out_path: str) -> int:
    get_settings.cache_clear()
    settings = get_settings()
    count = Storage(settings.db_path, settings.database_url).export_csv(out_path)
    logger.info("Exported %s rows to %s", count, out_path)
    return count


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    parser = argparse.ArgumentParser(description="G2 local batch: coverage + ingest + export")
    parser.add_argument("--csv", default="data/points.csv", help="CSV with lat,lon")
    parser.add_argument(
        "--coverage-log",
        default="coverage_log.csv",
        help="Append-only coverage evidence log",
    )
    parser.add_argument(
        "--export-out",
        default="readings.csv",
        help="Looker Studio CSV export path",
    )
    parser.add_argument(
        "--skip-coverage",
        action="store_true",
        help="Skip Mapbox coverage check step",
    )
    args = parser.parse_args()

    csv_path = str(Path(args.csv))
    if not Path(csv_path).is_absolute():
        # Resolve relative to traffic-service root when run from elsewhere
        candidate = ROOT / csv_path
        if candidate.exists():
            csv_path = str(candidate)

    if not args.skip_coverage:
        code = run_coverage(csv_path, args.coverage_log)
        if code != 0:
            logger.error("Coverage step failed with code %s", code)
            return code

    counts = run_ingest(csv_path)
    exported = run_export(args.export_out)

    print("\n=== G2 local batch summary ===")
    print(f"statuses: {dict(counts)}")
    print(f"exported_rows: {exported}")
    print(f"coverage_log: {args.coverage_log}")
    print(f"export_csv: {args.export_out}")
    print("Re-run at peak and off-peak hours to build informe evidence.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
