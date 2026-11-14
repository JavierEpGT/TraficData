"""Geodesic helpers for synthesizing a destination from a single GPS point."""

from __future__ import annotations

import math

# WGS84 mean Earth radius (meters)
_EARTH_RADIUS_M = 6_371_000.0


def destination_point(
    latitude: float,
    longitude: float,
    distance_m: float,
    bearing_deg: float,
) -> tuple[float, float]:
    """
    Direct geodesic formula: point at distance_m along bearing_deg from origin.

    Returns (dest_latitude, dest_longitude) in decimal degrees.
    """
    lat1 = math.radians(latitude)
    lon1 = math.radians(longitude)
    bearing = math.radians(bearing_deg)
    angular = distance_m / _EARTH_RADIUS_M

    sin_lat1 = math.sin(lat1)
    cos_lat1 = math.cos(lat1)
    sin_ang = math.sin(angular)
    cos_ang = math.cos(angular)

    lat2 = math.asin(sin_lat1 * cos_ang + cos_lat1 * sin_ang * math.cos(bearing))
    lon2 = lon1 + math.atan2(
        math.sin(bearing) * sin_ang * cos_lat1,
        cos_ang - sin_lat1 * math.sin(lat2),
    )

    dest_lat = math.degrees(lat2)
    dest_lon = ((math.degrees(lon2) + 540.0) % 360.0) - 180.0
    return dest_lat, dest_lon


def candidate_bearings(preferred_deg: float, step_deg: float = 45.0) -> list[float]:
    """Preferred bearing first, then the rest of the circle at ``step_deg``."""
    if step_deg <= 0 or step_deg > 360:
        raise ValueError(f"step_deg must be in (0, 360], got {step_deg}")
    steps = int(round(360.0 / step_deg))
    bearings: list[float] = []
    for offset in range(steps):
        bearing = round((preferred_deg + offset * step_deg) % 360.0, 6) % 360.0
        if bearing not in bearings:
            bearings.append(bearing)
    return bearings


def haversine_m(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    """Great-circle distance in meters between two WGS84 points."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    )
    return 2.0 * _EARTH_RADIUS_M * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
