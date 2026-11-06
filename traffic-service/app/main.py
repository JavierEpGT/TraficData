"""Pipeline orchestration and FastAPI application."""

from __future__ import annotations

import json
import logging
import secrets
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.congestion import CongestionError, congestion_index
from app.contract import build_payload, payload_to_dict, utc_timestamp_z
from app.dw_client import DwClient, DwClientError
from app.geo import candidate_bearings, destination_point
from app.mapbox_client import (
    PROFILE_FREE,
    PROFILE_TRAFFIC,
    DualRouteResult,
    MapboxClient,
    MapboxError,
    MapboxRouteError,
    RouteMetrics,
)
from app.models import FeedQuery, FeedResponse, ReadingRequest, ReadingResponse
from app.storage import RawReading, Storage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class ProcessResult:
    """Outcome of processing a single GPS point."""

    status: str
    payload: dict[str, Any] | None = None
    error: str | None = None
    reading_id: str | None = None


# Eight departure headings. The configured bearing is tried first.
BEARING_STEP_DEG = 45.0
# Mapbox may only snap each endpoint this far onto the road network.
SNAP_RADIUS_M = 50.0
# Allowed deviation from the probed heading when snapping.
BEARING_TOLERANCE_DEG = 45.0


def _relative_diff(a: float, b: float) -> float:
    denom = max(a, b)
    if denom <= 0:
        return 1.0
    return abs(a - b) / denom


def _distance_in_segment(distance_m: float, segment_length_m: float) -> bool:
    return 0.5 * segment_length_m <= distance_m <= 3.0 * segment_length_m


def validate_routes(
    dual: DualRouteResult,
    segment_length_m: float,
) -> str | None:
    """
    Return an invalidation reason, or None if the reading is valid.

    - ROUTE_MISMATCH: traffic vs free distances differ by more than 10%
    - SEGMENT_MISMATCH: route distance outside [0.5x, 3x] of SEGMENT_LENGTH_M
    - durations <= 0
    """
    t = dual.traffic
    f = dual.free

    if t.duration_s <= 0 or f.duration_s <= 0:
        return "NON_POSITIVE_DURATION"

    if _relative_diff(t.distance_m, f.distance_m) > 0.10:
        return "ROUTE_MISMATCH"

    for dist in (t.distance_m, f.distance_m):
        if not _distance_in_segment(dist, segment_length_m):
            return "SEGMENT_MISMATCH"

    return None


@dataclass
class SegmentMatch:
    """Best Mapbox segment found for one GPS point, or the closest rejection."""

    dest_lat: float
    dest_lon: float
    traffic: RouteMetrics | None = None
    free: RouteMetrics | None = None
    error: str | None = None


def resolve_segment(
    mapbox: MapboxClient,
    latitude: float,
    longitude: float,
    segment_length_m: float,
    preferred_bearing_deg: float,
) -> SegmentMatch:
    """
    Probe headings until one route stays inside the segment window.

    Traffic is queried first. Free-flow is requested only for a heading whose
    traffic distance already fits ``[0.5×, 3×]`` of ``segment_length_m``.
    The configured bearing is tried first and the search stops at the first
    pair that passes ``validate_routes``.

    Raises MapboxHttpError on auth, timeout, or server failures.
    Raises MapboxRouteError when every heading comes back without a route.
    """
    closest_out: SegmentMatch | None = None
    closest_gap = float("inf")
    pair_failure: SegmentMatch | None = None
    last_route_error: MapboxRouteError | None = None
    snap = {
        "bearing_tolerance_deg": BEARING_TOLERANCE_DEG,
        "radius_m": SNAP_RADIUS_M,
    }

    for bearing in candidate_bearings(preferred_bearing_deg, BEARING_STEP_DEG):
        dest_lat, dest_lon = destination_point(
            latitude, longitude, segment_length_m, bearing
        )
        try:
            traffic = mapbox.fetch_route(
                PROFILE_TRAFFIC,
                latitude,
                longitude,
                dest_lat,
                dest_lon,
                bearing_deg=bearing,
                **snap,
            )
        except MapboxRouteError as exc:
            last_route_error = exc
            logger.info("bearing %.1f skipped: %s", bearing, exc)
            continue

        gap = abs(traffic.distance_m - segment_length_m)
        if traffic.duration_s <= 0 or not _distance_in_segment(
            traffic.distance_m, segment_length_m
        ):
            reason = (
                "NON_POSITIVE_DURATION"
                if traffic.duration_s <= 0
                else "SEGMENT_MISMATCH"
            )
            logger.info(
                "bearing %.1f dist_m=%.1f rejected (%s)",
                bearing,
                traffic.distance_m,
                reason,
            )
            if gap < closest_gap:
                closest_gap = gap
                closest_out = SegmentMatch(
                    dest_lat=dest_lat,
                    dest_lon=dest_lon,
                    traffic=traffic,
                    error=reason,
                )
            continue

        try:
            free = mapbox.fetch_route(
                PROFILE_FREE,
                latitude,
                longitude,
                dest_lat,
                dest_lon,
                bearing_deg=bearing,
                **snap,
            )
        except MapboxRouteError as exc:
            last_route_error = exc
            logger.info("bearing %.1f free-flow skipped: %s", bearing, exc)
            continue

        dual = DualRouteResult(traffic=traffic, free=free)
        reason = validate_routes(dual, segment_length_m)
        if reason is None:
            logger.info(
                "segment matched bearing=%.1f dist_traffic_m=%.1f",
                bearing,
                traffic.distance_m,
            )
            return SegmentMatch(
                dest_lat=dest_lat,
                dest_lon=dest_lon,
                traffic=traffic,
                free=free,
            )

        logger.info("bearing %.1f rejected: %s", bearing, reason)
        if pair_failure is None:
            pair_failure = SegmentMatch(
                dest_lat=dest_lat,
                dest_lon=dest_lon,
                traffic=traffic,
                free=free,
                error=reason,
            )

    if pair_failure is not None:
        return pair_failure
    if closest_out is not None:
        return closest_out
    raise last_route_error or MapboxRouteError("Mapbox code='NoRoute'")


def _feed_token_ok(expected: str | None, provided: str | None) -> bool:
    """Constant-time compare; missing tokens never authorize."""
    if not expected or not provided:
        return False
    if len(expected) != len(provided):
        return False
    return secrets.compare_digest(expected, provided)


def process_reading(
    latitude: float,
    longitude: float,
    settings: Settings,
    *,
    mapbox: MapboxClient | None = None,
    storage: Storage | None = None,
    dw: DwClient | None = None,
) -> ProcessResult:
    """
    End-to-end: geo → Mapbox (heading search) → validate → index → contract → DW/DRY_RUN → storage.

    Never invents values: Mapbox failures and invalid readings are discarded.
    """
    owns_mapbox = mapbox is None
    owns_storage = storage is None
    mapbox = mapbox or MapboxClient(settings.mapbox_access_token)
    storage = storage or Storage(settings.db_path, settings.database_url)

    reading_id = str(uuid4())
    timestamp = utc_timestamp_z()
    fallback_lat, fallback_lon = destination_point(
        latitude,
        longitude,
        settings.segment_length_m,
        settings.segment_bearing_deg,
    )

    raw = RawReading(
        reading_id=reading_id,
        timestamp=timestamp,
        lat=latitude,
        lon=longitude,
        dest_lat=fallback_lat,
        dest_lon=fallback_lon,
        status="FAILED",
    )

    try:
        try:
            match = resolve_segment(
                mapbox,
                latitude,
                longitude,
                settings.segment_length_m,
                settings.segment_bearing_deg,
            )
        except MapboxError as exc:
            logger.error("Mapbox failure for reading_id=%s: %s", reading_id, exc)
            raw.error = str(exc)
            raw.status = "FAILED"
            storage.insert_reading(raw)
            return ProcessResult(status="FAILED", error=str(exc), reading_id=reading_id)

        raw.dest_lat = match.dest_lat
        raw.dest_lon = match.dest_lon
        if match.traffic is not None:
            raw.t_traffic_s = match.traffic.duration_s
            raw.dist_traffic_m = match.traffic.distance_m
            raw.congestion_numeric_avg = match.traffic.congestion_numeric_avg
        if match.free is not None:
            raw.t_free_s = match.free.duration_s
            raw.dist_free_m = match.free.distance_m

        traffic = match.traffic
        free = match.free
        if match.error or traffic is None or free is None:
            reason = match.error or "SEGMENT_MISMATCH"
            logger.warning("Invalid reading_id=%s reason=%s", reading_id, reason)
            raw.status = "INVALID"
            raw.error = reason
            storage.insert_reading(raw)
            return ProcessResult(status="INVALID", error=reason, reading_id=reading_id)

        try:
            index = congestion_index(traffic.duration_s, free.duration_s)
        except CongestionError as exc:
            raw.status = "INVALID"
            raw.error = str(exc)
            storage.insert_reading(raw)
            return ProcessResult(status="INVALID", error=str(exc), reading_id=reading_id)

        raw.traffic_congestion_index = index

        payload_model = build_payload(
            latitude=latitude,
            longitude=longitude,
            student_id=settings.student_id,
            secret_key=settings.secret_key,
            traffic_congestion_index=index,
            timestamp=timestamp,
            reading_id=reading_id,
        )
        payload = payload_to_dict(payload_model)

        if not settings.should_send_to_dw:
            # DRY_RUN or missing DW_ENDPOINT_URL: print payload, do not send
            print(json.dumps(payload, indent=2))
            raw.status = "DRY_RUN"
            storage.insert_reading(raw)
            return ProcessResult(
                status="DRY_RUN",
                payload=payload,
                reading_id=reading_id,
            )

        owns_dw = dw is None
        dw_client = dw or DwClient(
            settings.dw_endpoint_url,  # type: ignore[arg-type]
            auth_token=settings.dw_auth_token,
        )
        try:
            dw_client.send(payload)
            raw.status = "SENT"
            storage.insert_reading(raw)
            return ProcessResult(status="SENT", payload=payload, reading_id=reading_id)
        except DwClientError as exc:
            logger.error("DW send failed reading_id=%s: %s", reading_id, exc)
            raw.status = "FAILED"
            raw.error = str(exc)
            storage.insert_reading(raw)
            return ProcessResult(
                status="FAILED",
                payload=payload,
                error=str(exc),
                reading_id=reading_id,
            )
        finally:
            if owns_dw:
                dw_client.close()
    finally:
        if owns_mapbox:
            mapbox.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    """FastAPI factory."""
    app = FastAPI(
        title="Traffic Ingestion Service (G2)",
        version="0.1.0",
        description="Real Mapbox traffic readings for Monitoreo Urbano",
    )
    app_settings = settings

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/traffic/reading", response_model=ReadingResponse)
    def traffic_reading(body: ReadingRequest) -> ReadingResponse:
        cfg = app_settings or get_settings()
        result = process_reading(body.latitude, body.longitude, cfg)
        return ReadingResponse(
            status=result.status,
            payload=result.payload,
            error=result.error,
        )

    @app.get("/traffic/readings", response_model=FeedResponse)
    def traffic_readings(
        since: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
        x_api_key: str | None = Header(default=None),
    ) -> FeedResponse:
        cfg = app_settings or get_settings()
        if not _feed_token_ok(cfg.g4_feed_token, x_api_key):
            raise HTTPException(status_code=401, detail="unauthorized")

        try:
            query = FeedQuery.model_validate({"since": since, "limit": limit})
        except ValidationError:
            raise HTTPException(
                status_code=422,
                detail="since must be ISO-8601 UTC to seconds with Z suffix",
            )

        storage = Storage(cfg.db_path, cfg.database_url)
        rows = storage.fetch_valid_readings(since=query.since, limit=query.limit)
        items = [
            build_payload(
                latitude=float(row["lat"]),
                longitude=float(row["lon"]),
                student_id=cfg.student_id,
                secret_key=cfg.secret_key,
                traffic_congestion_index=float(row["traffic_congestion_index"]),
                timestamp=str(row["timestamp"]),
                reading_id=row["reading_id"],
            )
            for row in rows
        ]
        return FeedResponse(count=len(items), items=items)

    return app


app = create_app()
