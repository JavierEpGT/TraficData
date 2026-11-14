"""Pruebas adicionales de QA para orquestación y API, sin servicios externos."""
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.congestion import CongestionError
from app.dw_client import DwClientError
from app.mapbox_client import DualRouteResult, MapboxRouteError, RouteMetrics


class FakeStorage:
    def __init__(self):
        self.rows = []

    def insert_reading(self, reading):
        self.rows.append(reading)


class FakeMapbox:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.closed = False
        self.calls = []

    def fetch_route(self, profile, *args, **kwargs):
        self.calls.append(profile)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        self.closed = True


class FakeDw:
    def __init__(self, error=None):
        self.error = error
        self.sent = []
        self.closed = False

    def send(self, payload):
        self.sent.append(payload)
        if self.error:
            raise self.error

    def close(self):
        self.closed = True


def metric(duration, distance=400):
    return RouteMetrics(duration_s=duration, distance_m=distance, congestion_numeric_avg=None)


def match_ok():
    return main.SegmentMatch(14.64, -90.51, metric(90), metric(60))


def test_qa_relative_diff_with_nonpositive_denominator():
    assert main._relative_diff(0, 0) == 1.0


def test_qa_validate_routes_rejects_nonpositive_duration():
    routes = DualRouteResult(traffic=metric(0), free=metric(60))
    assert main.validate_routes(routes, 400) == "NON_POSITIVE_DURATION"


def test_qa_resolve_segment_skips_free_route_error(monkeypatch):
    monkeypatch.setattr(main, "candidate_bearings", lambda *_: [0, 45])
    fake = FakeMapbox([
        metric(90), MapboxRouteError("NoRoute"),
        metric(90), metric(60),
    ])
    result = main.resolve_segment(fake, 14.6, -90.5, 400, 0)
    assert result.error is None
    assert len(fake.calls) == 4


def test_qa_process_reading_rejects_congestion_error(monkeypatch, settings):
    storage = FakeStorage()
    monkeypatch.setattr(main, "resolve_segment", lambda *args: match_ok())
    def bad_congestion(*args):
        raise CongestionError("invalid duration")
    monkeypatch.setattr(main, "congestion_index", bad_congestion)
    result = main.process_reading(14.6, -90.5, settings, mapbox=FakeMapbox(), storage=storage)
    assert result.status == "INVALID"
    assert result.error == "invalid duration"
    assert storage.rows[-1].status == "INVALID"


def test_qa_process_reading_dw_failure_is_saved(monkeypatch, settings):
    cfg = settings.model_copy(update={"dry_run": False, "dw_endpoint_url": "https://example.invalid/dw"})
    storage = FakeStorage()
    dw = FakeDw(DwClientError("service offline"))
    monkeypatch.setattr(main, "resolve_segment", lambda *args: match_ok())
    result = main.process_reading(14.6, -90.5, cfg, mapbox=FakeMapbox(), storage=storage, dw=dw)
    assert result.status == "FAILED"
    assert "service offline" in result.error
    assert len(dw.sent) == 1
    assert storage.rows[-1].status == "FAILED"


def test_qa_process_reading_closes_owned_mapbox(monkeypatch, settings):
    created = []
    class OwnedMapbox(FakeMapbox):
        def __init__(self, *args, **kwargs):
            super().__init__()
            created.append(self)
    monkeypatch.setattr(main, "MapboxClient", OwnedMapbox)
    monkeypatch.setattr(main, "resolve_segment", lambda *args: match_ok())
    result = main.process_reading(14.6, -90.5, settings, storage=FakeStorage())
    assert result.status == "DRY_RUN"
    assert created[0].closed is True


def test_qa_process_reading_closes_owned_dw(monkeypatch, settings):
    cfg = settings.model_copy(update={"dry_run": False, "dw_endpoint_url": "https://example.invalid/dw"})
    created = []
    class OwnedDw(FakeDw):
        def __init__(self, *args, **kwargs):
            super().__init__()
            created.append(self)
    monkeypatch.setattr(main, "DwClient", OwnedDw)
    monkeypatch.setattr(main, "resolve_segment", lambda *args: match_ok())
    result = main.process_reading(14.6, -90.5, cfg, mapbox=FakeMapbox(), storage=FakeStorage())
    assert result.status == "SENT"
    assert created[0].closed is True


def test_qa_health_returns_ok(settings):
    with TestClient(main.create_app(settings)) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_qa_post_reading_calls_pipeline(monkeypatch, settings):
    captured = []
    def fake_process(lat, lon, cfg):
        captured.append((lat, lon, cfg))
        return main.ProcessResult(status="DRY_RUN")
    monkeypatch.setattr(main, "process_reading", fake_process)
    with TestClient(main.create_app(settings)) as client:
        response = client.post("/traffic/reading", json={"latitude": 14.6, "longitude": -90.5})
    assert response.status_code == 200
    assert response.json()["status"] == "DRY_RUN"
    assert captured[0] == (14.6, -90.5, settings)


def test_qa_post_reading_rejects_invalid_coordinates(settings):
    with TestClient(main.create_app(settings)) as client:
        response = client.post("/traffic/reading", json={"latitude": 200, "longitude": -90.5})
    assert response.status_code == 422
