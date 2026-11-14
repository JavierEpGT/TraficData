"""Mapbox Directions v5 client (real traffic + free-flow dual query)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

MAPBOX_DIRECTIONS_BASE = "https://api.mapbox.com/directions/v5"
PROFILE_TRAFFIC = "mapbox/driving-traffic"
PROFILE_FREE = "mapbox/driving"
DEFAULT_TIMEOUT_S = 10.0
MAX_ATTEMPTS = 3


class MapboxError(Exception):
    """Base error for Mapbox client failures."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class MapboxRouteError(MapboxError):
    """Mapbox returned a non-Ok code or unusable route."""


class MapboxHttpError(MapboxError):
    """HTTP-level Mapbox failure."""


@dataclass(frozen=True)
class RouteMetrics:
    """Metrics extracted from a Directions response."""

    duration_s: float
    distance_m: float
    congestion_numeric_avg: float | None = None


@dataclass(frozen=True)
class DualRouteResult:
    """Paired traffic and free-flow metrics for the same segment."""

    traffic: RouteMetrics
    free: RouteMetrics


def _avg_congestion_numeric(body: dict[str, Any]) -> float | None:
    values: list[float] = []
    routes = body.get("routes") or []
    if not routes:
        return None
    for leg in routes[0].get("legs") or []:
        annotation = leg.get("annotation") or {}
        for item in annotation.get("congestion_numeric") or []:
            if item is not None:
                values.append(float(item))
    if not values:
        return None
    return sum(values) / len(values)


def _parse_route(body: dict[str, Any], *, with_congestion: bool) -> RouteMetrics:
    code = body.get("code")
    if code != "Ok":
        raise MapboxRouteError(f"Mapbox code={code!r}")

    routes = body.get("routes") or []
    if not routes:
        raise MapboxRouteError("Mapbox response has no routes")

    route = routes[0]
    duration = route.get("duration")
    distance = route.get("distance")
    if duration is None or distance is None:
        raise MapboxRouteError("Mapbox route missing duration or distance")

    congestion_avg = _avg_congestion_numeric(body) if with_congestion else None
    return RouteMetrics(
        duration_s=float(duration),
        distance_m=float(distance),
        congestion_numeric_avg=congestion_avg,
    )


def _should_retry_status(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500


def _format_bearings(bearing_deg: float, tolerance_deg: float) -> str:
    """Mapbox ``bearings`` for origin and destination, same direction of travel."""
    angle = bearing_deg % 360.0
    pair = f"{angle:.4f},{tolerance_deg:.4f}"
    return f"{pair};{pair}"


def _format_radiuses(radius_m: float) -> str:
    """Mapbox ``radiuses`` snap limit, in meters, for both coordinates."""
    text = f"{radius_m:.1f}".rstrip("0").rstrip(".")
    return f"{text};{text}"


class MapboxClient:
    """HTTP client for Mapbox Directions with exponential backoff retries."""

    def __init__(
        self,
        access_token: str,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_attempts: int = MAX_ATTEMPTS,
        client: httpx.Client | None = None,
    ) -> None:
        self._token = access_token
        self._timeout_s = timeout_s
        self._max_attempts = max_attempts
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout_s)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> MapboxClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _request(
        self,
        profile: str,
        lon1: float,
        lat1: float,
        lon2: float,
        lat2: float,
        *,
        bearing_deg: float | None = None,
        bearing_tolerance_deg: float | None = None,
        radius_m: float | None = None,
    ) -> dict[str, Any]:
        coords = f"{lon1},{lat1};{lon2},{lat2}"
        url = f"{MAPBOX_DIRECTIONS_BASE}/{profile}/{coords}"
        params: dict[str, str] = {
            "access_token": self._token,
            "alternatives": "false",
        }
        if bearing_deg is not None and bearing_tolerance_deg is not None:
            params["bearings"] = _format_bearings(bearing_deg, bearing_tolerance_deg)
        if radius_m is not None:
            params["radiuses"] = _format_radiuses(radius_m)
        # annotations require overview=full per Mapbox Directions v5 docs
        if profile == PROFILE_TRAFFIC:
            params["overview"] = "full"
            params["annotations"] = "duration,distance,congestion_numeric"
        else:
            params["overview"] = "false"

        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._client.get(url, params=params, timeout=self._timeout_s)
            except httpx.TimeoutException as exc:
                last_error = MapboxHttpError(
                    f"Mapbox timeout on attempt {attempt}",
                    retryable=True,
                )
                logger.warning("Mapbox timeout profile=%s attempt=%s", profile, attempt)
                if attempt < self._max_attempts:
                    time.sleep(2 ** (attempt - 1))
                    continue
                raise last_error from exc
            except httpx.HTTPError as exc:
                last_error = MapboxHttpError(f"Mapbox HTTP error: {exc}", retryable=False)
                raise last_error from exc

            status = response.status_code
            if status in (401, 422):
                raise MapboxHttpError(f"Mapbox HTTP {status}: {response.text[:200]}")

            if _should_retry_status(status):
                last_error = MapboxHttpError(
                    f"Mapbox HTTP {status}",
                    retryable=True,
                )
                logger.warning(
                    "Mapbox retryable status=%s profile=%s attempt=%s",
                    status,
                    profile,
                    attempt,
                )
                if attempt < self._max_attempts:
                    time.sleep(2 ** (attempt - 1))
                    continue
                raise last_error

            if status >= 400:
                raise MapboxHttpError(f"Mapbox HTTP {status}: {response.text[:200]}")

            return response.json()

        raise last_error or MapboxHttpError("Mapbox request failed")

    def fetch_route(
        self,
        profile: str,
        lat1: float,
        lon1: float,
        lat2: float,
        lon2: float,
        *,
        bearing_deg: float | None = None,
        bearing_tolerance_deg: float | None = None,
        radius_m: float | None = None,
    ) -> RouteMetrics:
        """Query one Directions profile. Coordinate order for Mapbox is lon, lat."""
        body = self._request(
            profile,
            lon1,
            lat1,
            lon2,
            lat2,
            bearing_deg=bearing_deg,
            bearing_tolerance_deg=bearing_tolerance_deg,
            radius_m=radius_m,
        )
        return _parse_route(body, with_congestion=profile == PROFILE_TRAFFIC)

    def fetch_dual_route(
        self,
        lat1: float,
        lon1: float,
        lat2: float,
        lon2: float,
        *,
        bearing_deg: float | None = None,
        bearing_tolerance_deg: float | None = None,
        radius_m: float | None = None,
    ) -> DualRouteResult:
        """
        Query the same segment with driving-traffic and driving profiles.

        Coordinate order for Mapbox is longitude, latitude.
        """
        snap = {
            "bearing_deg": bearing_deg,
            "bearing_tolerance_deg": bearing_tolerance_deg,
            "radius_m": radius_m,
        }
        traffic = self.fetch_route(PROFILE_TRAFFIC, lat1, lon1, lat2, lon2, **snap)
        free = self.fetch_route(PROFILE_FREE, lat1, lon1, lat2, lon2, **snap)
        return DualRouteResult(traffic=traffic, free=free)
