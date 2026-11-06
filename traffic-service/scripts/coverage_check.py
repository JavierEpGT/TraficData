#!/usr/bin/env python3
"""
Coverage check: query Mapbox driving-traffic and driving for each CSV point.

Purpose: run at peak and off-peak hours to verify live traffic durations vary,
and append evidence rows to coverage_log.csv for the project report.

Usage (from traffic-service/):
  python scripts/coverage_check.py --csv points.csv [--log coverage_log.csv]
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running as a script from traffic-service/
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.congestion import CongestionError, congestion_index
from app.main import resolve_segment
from app.mapbox_client import MapboxClient, MapboxError
from app.sources import CsvSource


def main() -> int:
    parser = argparse.ArgumentParser(description="Mapbox traffic coverage check")
    parser.add_argument("--csv", required=True, help="CSV with real lat,lon points")
    parser.add_argument(
        "--log",
        default="coverage_log.csv",
        help="Append-only log CSV (default: coverage_log.csv)",
    )
    args = parser.parse_args()

    settings = get_settings()
    points = CsvSource(args.csv).get_points()
    if not points:
        print("No points in CSV", file=sys.stderr)
        return 1

    run_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    log_path = Path(args.log)
    write_header = not log_path.exists()
    fieldnames = [
        "run_timestamp",
        "lat",
        "lon",
        "t_traffic_s",
        "t_free_s",
        "dist_traffic_m",
        "dist_free_m",
        "congestion_numeric_avg",
        "traffic_congestion_index",
        "error",
    ]

    print(
        f"{'lat':>10} {'lon':>10} {'t_traf':>8} {'t_free':>8} "
        f"{'dist_m':>8} {'index':>8} {'cong_avg':>8} error"
    )
    print("-" * 80)

    with MapboxClient(settings.mapbox_access_token) as client, log_path.open(
        "a", newline="", encoding="utf-8"
    ) as log_fh:
        writer = csv.DictWriter(log_fh, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()

        for lat, lon in points:
            row = {
                "run_timestamp": run_ts,
                "lat": lat,
                "lon": lon,
                "t_traffic_s": "",
                "t_free_s": "",
                "dist_traffic_m": "",
                "dist_free_m": "",
                "congestion_numeric_avg": "",
                "traffic_congestion_index": "",
                "error": "",
            }
            try:
                match = resolve_segment(
                    client,
                    lat,
                    lon,
                    settings.segment_length_m,
                    settings.segment_bearing_deg,
                )
                idx: float | str = ""
                if match.traffic is not None and match.free is not None and not match.error:
                    try:
                        idx = congestion_index(
                            match.traffic.duration_s, match.free.duration_s
                        )
                    except CongestionError as exc:
                        idx = ""
                        row["error"] = str(exc)
                elif match.error:
                    row["error"] = match.error

                row.update(
                    {
                        "t_traffic_s": ""
                        if match.traffic is None
                        else match.traffic.duration_s,
                        "t_free_s": "" if match.free is None else match.free.duration_s,
                        "dist_traffic_m": ""
                        if match.traffic is None
                        else match.traffic.distance_m,
                        "dist_free_m": "" if match.free is None else match.free.distance_m,
                        "congestion_numeric_avg": ""
                        if match.traffic is None
                        or match.traffic.congestion_numeric_avg is None
                        else match.traffic.congestion_numeric_avg,
                        "traffic_congestion_index": idx,
                    }
                )
                t_traf = match.traffic.duration_s if match.traffic else float("nan")
                t_free = match.free.duration_s if match.free else float("nan")
                dist_m = match.traffic.distance_m if match.traffic else float("nan")
                cong = (
                    match.traffic.congestion_numeric_avg
                    if match.traffic and match.traffic.congestion_numeric_avg is not None
                    else float("nan")
                )
                print(
                    f"{lat:10.5f} {lon:10.5f} "
                    f"{t_traf:8.1f} {t_free:8.1f} "
                    f"{dist_m:8.1f} "
                    f"{(idx if idx != '' else float('nan')):8} "
                    f"{cong:8} "
                    f"{row['error']}"
                )
            except MapboxError as exc:
                row["error"] = str(exc)
                print(
                    f"{lat:10.5f} {lon:10.5f} "
                    f"{'':>8} {'':>8} {'':>8} {'':>8} {'':>8} {exc}"
                )

            writer.writerow(row)

    print(f"\nAppended {len(points)} rows to {log_path} (run_timestamp={run_ts})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
