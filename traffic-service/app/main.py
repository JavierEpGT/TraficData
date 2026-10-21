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
from app.geo import destination_point
from app.mapbox_client import DualRouteResult, MapboxClient, MapboxError
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


def _relative_diff(a: float, b: float) -> float:
    denom = max(a, b)
    if denom <= 0:
        return 1.0
    return abs(a - b) / denom


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
        if dist < 0.5 * segment_length_m or dist > 3.0 * segment_length_m:
            return "SEGMENT_MISMATCH"

    return None


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
    End-to-end: geo → Mapbox → validate → index → contract → DW/DRY_RUN → SQLite.

    Never invents values: Mapbox failures and invalid readings are discarded.
    """
    owns_mapbox = mapbox is None
    owns_storage = storage is None
    mapbox = mapbox or MapboxClient(settings.mapbox_access_token)
    storage = storage or Storage(settings.db_path)

    reading_id = str(uuid4())
    timestamp = utc_timestamp_z()
    dest_lat, dest_lon = destination_point(
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
        dest_lat=dest_lat,
        dest_lon=dest_lon,
        status="FAILED",
    )

    try:
        try:
            dual = mapbox.fetch_dual_route(latitude, longitude, dest_lat, dest_lon)
        except MapboxError as exc:
            logger.error("Mapbox failure for reading_id=%s: %s", reading_id, exc)
            raw.error = str(exc)
            raw.status = "FAILED"
            storage.insert_reading(raw)
            return ProcessResult(status="FAILED", error=str(exc), reading_id=reading_id)

        raw.t_traffic_s = dual.traffic.duration_s
        raw.t_free_s = dual.free.duration_s
        raw.dist_traffic_m = dual.traffic.distance_m
        raw.dist_free_m = dual.free.distance_m
        raw.congestion_numeric_avg = dual.traffic.congestion_numeric_avg

        reason = validate_routes(dual, settings.segment_length_m)
        if reason:
            logger.warning("Invalid reading_id=%s reason=%s", reading_id, reason)
            raw.status = "INVALID"
            raw.error = reason
            storage.insert_reading(raw)
            return ProcessResult(status="INVALID", error=reason, reading_id=reading_id)

        try:
            index = congestion_index(dual.traffic.duration_s, dual.free.duration_s)
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

        storage = Storage(cfg.db_path)
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
