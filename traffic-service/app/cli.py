"""CLI entrypoints: run batch ingestion and export CSV for Looker Studio."""

from __future__ import annotations

import argparse
import logging
import sys
import time

from app.config import get_settings
from app.main import process_reading
from app.sources import CsvSource, Group1ApiError, Group1ApiSource
from app.storage import Storage

logger = logging.getLogger(__name__)


def _load_points(args: argparse.Namespace) -> list[tuple[float, float]]:
    settings = get_settings()
    if args.from_g1:
        if not settings.group1_api_url or not settings.group1_api_token:
            raise ValueError(
                "GROUP1_API_URL and GROUP1_API_TOKEN must be set in .env for --from-g1"
            )
        with Group1ApiSource(
            settings.group1_api_url,
            settings.group1_api_token,
        ) as source:
            return source.get_points()
    return CsvSource(args.csv).get_points()


def cmd_run(args: argparse.Namespace) -> int:
    settings = get_settings()
    try:
        points = _load_points(args)
    except (Group1ApiError, ValueError, FileNotFoundError) as exc:
        logger.error("%s", exc)
        return 1

    if not points:
        logger.error("No points found from selected source")
        return 1

    storage = Storage(settings.db_path, settings.database_url)
    iteration = 0
    while True:
        iteration += 1
        logger.info("Run iteration=%s points=%s", iteration, len(points))
        for lat, lon in points:
            result = process_reading(lat, lon, settings, storage=storage)
            logger.info(
                "point=(%s,%s) status=%s error=%s",
                lat,
                lon,
                result.status,
                result.error,
            )
        if not args.loop:
            break
        if args.from_g1:
            # Refresh GPS feed each loop iteration
            try:
                points = _load_points(args)
            except (Group1ApiError, ValueError) as exc:
                logger.error("Failed to refresh G1 points: %s", exc)
                return 1
            if not points:
                logger.error("No points found from G1 on refresh")
                return 1
        logger.info("Sleeping %s seconds", args.interval)
        time.sleep(args.interval)
    return 0


def cmd_export_csv(args: argparse.Namespace) -> int:
    settings = get_settings()
    storage = Storage(settings.db_path, settings.database_url)
    count = storage.export_csv(args.out)
    print(f"Exported {count} rows to {args.out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="Traffic ingestion CLI (Grupo 2)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Ingest points from CSV or Group 1 API")
    source = run_p.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--csv",
        help="Path to CSV with lat,lon columns",
    )
    source.add_argument(
        "--from-g1",
        action="store_true",
        help="Load GPS points from Group 1 records API (GROUP1_API_URL + GROUP1_API_TOKEN)",
    )
    run_p.add_argument("--loop", action="store_true", help="Repeat forever")
    run_p.add_argument(
        "--interval",
        type=int,
        default=300,
        help="Seconds between loop iterations (default 300)",
    )
    run_p.set_defaults(func=cmd_run)

    export_p = sub.add_parser("export-csv", help="Export readings_raw to CSV")
    export_p.add_argument("--out", required=True, help="Output CSV path")
    export_p.set_defaults(func=cmd_export_csv)

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
