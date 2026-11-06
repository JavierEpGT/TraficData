"""Mapbox client tests with respx mocks only."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.geo import destination_point
from app.mapbox_client import (
    PROFILE_FREE,
    PROFILE_TRAFFIC,
    MapboxClient,
    MapboxHttpError,
    MapboxRouteError,
)
from tests.conftest import free_ok_body, traffic_ok_body


LAT, LON = 14.6349, -90.5069
DEST_LAT, DEST_LON = destination_point(LAT, LON, 400.0, 0.0)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.mapbox_client.time.sleep", lambda *_a, **_k: None)


@respx.mock
def test_fetch_dual_route_success() -> None:
    respx.get(url__regex=rf".*/{PROFILE_TRAFFIC}/.*").mock(
        return_value=httpx.Response(200, json=traffic_ok_body())
    )
    respx.get(url__regex=rf".*/{PROFILE_FREE}/.*").mock(
        return_value=httpx.Response(200, json=free_ok_body())
    )

    with MapboxClient("test-token", max_attempts=1) as client:
        dual = client.fetch_dual_route(LAT, LON, DEST_LAT, DEST_LON)

    assert dual.traffic.duration_s == 80.0
    assert dual.free.duration_s == 60.0
    assert dual.traffic.distance_m == 400.0
    assert dual.traffic.congestion_numeric_avg == 20.0


@respx.mock
def test_fetch_route_sends_snap_constraints() -> None:
    route = respx.get(url__regex=rf".*/{PROFILE_TRAFFIC}/.*").mock(
        return_value=httpx.Response(200, json=traffic_ok_body())
    )

    with MapboxClient("test-token", max_attempts=1) as client:
        client.fetch_route(
            PROFILE_TRAFFIC,
            LAT,
            LON,
            DEST_LAT,
            DEST_LON,
            bearing_deg=90.0,
            bearing_tolerance_deg=45.0,
            radius_m=50.0,
        )

    params = route.calls[0].request.url.params
    assert params["radiuses"] == "50;50"
    assert params["bearings"] == "90.0000,45.0000;90.0000,45.0000"


@respx.mock
def test_no_route() -> None:
    respx.get(url__regex=rf".*/{PROFILE_TRAFFIC}/.*").mock(
        return_value=httpx.Response(200, json={"code": "NoRoute", "routes": []})
    )

    with MapboxClient("test-token", max_attempts=1) as client:
        with pytest.raises(MapboxRouteError, match="NoRoute"):
            client.fetch_dual_route(LAT, LON, DEST_LAT, DEST_LON)


@respx.mock
def test_retry_on_429_then_success() -> None:
    route = respx.get(url__regex=rf".*/{PROFILE_TRAFFIC}/.*")
    route.side_effect = [
        httpx.Response(429, json={"message": "rate limit"}),
        httpx.Response(200, json=traffic_ok_body()),
    ]
    respx.get(url__regex=rf".*/{PROFILE_FREE}/.*").mock(
        return_value=httpx.Response(200, json=free_ok_body())
    )

    with MapboxClient("test-token", max_attempts=3) as client:
        dual = client.fetch_dual_route(LAT, LON, DEST_LAT, DEST_LON)

    assert dual.traffic.duration_s == 80.0
    assert route.call_count == 2


@respx.mock
def test_timeout_exhausted() -> None:
    respx.get(url__regex=rf".*/{PROFILE_TRAFFIC}/.*").mock(
        side_effect=httpx.TimeoutException("timeout")
    )

    with MapboxClient("test-token", max_attempts=3) as client:
        with pytest.raises(MapboxHttpError, match="timeout"):
            client.fetch_dual_route(LAT, LON, DEST_LAT, DEST_LON)
