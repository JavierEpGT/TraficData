"""End-to-end pipeline tests with mocked Mapbox/DW."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.config import Settings
from app.dw_client import DwClient
from app.geo import destination_point
from app.main import process_reading, validate_routes
from app.mapbox_client import (
    PROFILE_FREE,
    PROFILE_TRAFFIC,
    DualRouteResult,
    MapboxClient,
    RouteMetrics,
)
from app.storage import Storage
from tests.conftest import free_ok_body, traffic_ok_body


LAT, LON = 14.6349, -90.5069


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.mapbox_client.time.sleep", lambda *_a, **_k: None)
    monkeypatch.setattr("app.dw_client.time.sleep", lambda *_a, **_k: None)


def test_route_mismatch_validation() -> None:
    dual = DualRouteResult(
        traffic=RouteMetrics(80.0, 500.0, 10.0),
        free=RouteMetrics(60.0, 400.0, None),
    )
    assert validate_routes(dual, 400.0) == "ROUTE_MISMATCH"


def test_segment_mismatch_validation() -> None:
    dual = DualRouteResult(
        traffic=RouteMetrics(80.0, 50.0, 10.0),
        free=RouteMetrics(60.0, 50.0, None),
    )
    assert validate_routes(dual, 400.0) == "SEGMENT_MISMATCH"


def test_valid_routes() -> None:
    dual = DualRouteResult(
        traffic=RouteMetrics(80.0, 420.0, 10.0),
        free=RouteMetrics(60.0, 410.0, None),
    )
    assert validate_routes(dual, 400.0) is None


@respx.mock
def test_pipeline_route_mismatch_invalid(settings: Settings, tmp_path) -> None:
    respx.get(url__regex=rf".*/{PROFILE_TRAFFIC}/.*").mock(
        return_value=httpx.Response(
            200, json=traffic_ok_body(duration=80.0, distance=500.0)
        )
    )
    respx.get(url__regex=rf".*/{PROFILE_FREE}/.*").mock(
        return_value=httpx.Response(
            200, json=free_ok_body(duration=60.0, distance=400.0)
        )
    )

    storage = Storage(str(tmp_path / "pipe.db"))
    with MapboxClient("test-token", max_attempts=1) as mapbox:
        result = process_reading(
            LAT, LON, settings, mapbox=mapbox, storage=storage
        )

    assert result.status == "INVALID"
    assert result.error == "ROUTE_MISMATCH"
    rows = storage.fetch_all()
    assert len(rows) == 1
    assert rows[0]["status"] == "INVALID"


@respx.mock
def test_dry_run_does_not_send(tmp_path, capsys) -> None:
    settings = Settings(
        MAPBOX_ACCESS_TOKEN="test-mapbox-token",
        STUDENT_ID="g2-test-student",
        SECRET_KEY="test-secret-key",
        DRY_RUN=True,
        SEGMENT_LENGTH_M=400,
        SEGMENT_BEARING_DEG=0,
        DB_PATH=str(tmp_path / "dry.db"),
        DW_ENDPOINT_URL="https://dw.example.com/ingest",
        DW_AUTH_TOKEN="should-not-be-used",
    )

    respx.get(url__regex=rf".*/{PROFILE_TRAFFIC}/.*").mock(
        return_value=httpx.Response(200, json=traffic_ok_body())
    )
    respx.get(url__regex=rf".*/{PROFILE_FREE}/.*").mock(
        return_value=httpx.Response(200, json=free_ok_body())
    )
    dw_route = respx.post("https://dw.example.com/ingest").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    storage = Storage(settings.db_path)
    with MapboxClient("test-token", max_attempts=1) as mapbox:
        result = process_reading(
            LAT,
            LON,
            settings,
            mapbox=mapbox,
            storage=storage,
            dw=DwClient("https://dw.example.com/ingest"),
        )

    assert result.status == "DRY_RUN"
    assert result.payload is not None
    assert dw_route.call_count == 0
    captured = capsys.readouterr()
    assert "traffic_congestion_index" in captured.out
    rows = storage.fetch_all()
    assert rows[0]["status"] == "DRY_RUN"


@respx.mock
def test_send_when_not_dry_run(tmp_path) -> None:
    settings = Settings(
        MAPBOX_ACCESS_TOKEN="test-mapbox-token",
        STUDENT_ID="g2-test-student",
        SECRET_KEY="test-secret-key",
        DRY_RUN=False,
        SEGMENT_LENGTH_M=400,
        SEGMENT_BEARING_DEG=0,
        DB_PATH=str(tmp_path / "send.db"),
        DW_ENDPOINT_URL="https://dw.example.com/ingest",
        DW_AUTH_TOKEN="token",
    )

    respx.get(url__regex=rf".*/{PROFILE_TRAFFIC}/.*").mock(
        return_value=httpx.Response(200, json=traffic_ok_body())
    )
    respx.get(url__regex=rf".*/{PROFILE_FREE}/.*").mock(
        return_value=httpx.Response(200, json=free_ok_body())
    )
    dw_route = respx.post("https://dw.example.com/ingest").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    storage = Storage(settings.db_path)
    with MapboxClient("test-token", max_attempts=1) as mapbox:
        with DwClient(
            "https://dw.example.com/ingest", auth_token="token", max_attempts=1
        ) as dw:
            result = process_reading(
                LAT, LON, settings, mapbox=mapbox, storage=storage, dw=dw
            )

    assert result.status == "SENT"
    assert dw_route.call_count == 1
    assert dw_route.calls[0].request.headers["Authorization"] == "Bearer token"


@respx.mock
def test_long_north_route_uses_next_bearing(settings: Settings, tmp_path) -> None:
    traffic = respx.get(url__regex=rf".*/{PROFILE_TRAFFIC}/.*")
    traffic.side_effect = [
        httpx.Response(
            200, json=traffic_ok_body(duration=300.0, distance=5000.0)
        ),
        httpx.Response(200, json=traffic_ok_body(duration=80.0, distance=420.0)),
    ]
    free = respx.get(url__regex=rf".*/{PROFILE_FREE}/.*").mock(
        return_value=httpx.Response(
            200, json=free_ok_body(duration=60.0, distance=410.0)
        )
    )

    storage = Storage(str(tmp_path / "bearings.db"))
    with MapboxClient("test-token", max_attempts=1) as mapbox:
        result = process_reading(LAT, LON, settings, mapbox=mapbox, storage=storage)

    assert result.status == "DRY_RUN"
    assert traffic.call_count == 2
    assert free.call_count == 1
    good_lat, good_lon = destination_point(LAT, LON, 400.0, 45.0)
    row = storage.fetch_all()[0]
    assert row["dest_lat"] == pytest.approx(good_lat)
    assert row["dest_lon"] == pytest.approx(good_lon)
    assert row["dist_traffic_m"] == 420.0
    params = traffic.calls[1].request.url.params
    assert params["radiuses"] == "50;50"
    assert params["bearings"] == "45.0000,45.0000;45.0000,45.0000"


@respx.mock
def test_all_bearings_too_long_stay_invalid(settings: Settings, tmp_path) -> None:
    traffic = respx.get(url__regex=rf".*/{PROFILE_TRAFFIC}/.*").mock(
        return_value=httpx.Response(
            200, json=traffic_ok_body(duration=400.0, distance=5000.0)
        )
    )
    free = respx.get(url__regex=rf".*/{PROFILE_FREE}/.*").mock(
        return_value=httpx.Response(200, json=free_ok_body())
    )

    storage = Storage(str(tmp_path / "long.db"))
    with MapboxClient("test-token", max_attempts=1) as mapbox:
        result = process_reading(LAT, LON, settings, mapbox=mapbox, storage=storage)

    assert result.status == "INVALID"
    assert result.error == "SEGMENT_MISMATCH"
    assert traffic.call_count == 8
    assert free.call_count == 0
    assert storage.fetch_all()[0]["dist_traffic_m"] == 5000.0


@respx.mock
def test_all_bearings_without_route_fail(settings: Settings, tmp_path) -> None:
    traffic = respx.get(url__regex=rf".*/{PROFILE_TRAFFIC}/.*").mock(
        return_value=httpx.Response(200, json={"code": "NoRoute", "routes": []})
    )

    storage = Storage(str(tmp_path / "noroute.db"))
    with MapboxClient("test-token", max_attempts=1) as mapbox:
        result = process_reading(LAT, LON, settings, mapbox=mapbox, storage=storage)

    assert result.status == "FAILED"
    assert "NoRoute" in (result.error or "")
    assert traffic.call_count == 8
    assert storage.fetch_all()[0]["status"] == "FAILED"
