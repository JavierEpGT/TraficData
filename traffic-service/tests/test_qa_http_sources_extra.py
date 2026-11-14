
"""Pruebas QA de entradas y errores de servicios externos, sin acceso a red."""

from __future__ import annotations

import httpx
import pytest

from app import dw_client
from app.dw_client import DwClient, DwClientError
from app.sources import CsvSource, Group1ApiSource, Group1ApiError


@pytest.mark.parametrize("data, expected", [
    ("", "header"),
    ("other,val\n1,2\n", "lat/latitude"),
    ("lat,lon\nabc,2\n", "Invalid lat/lon"),
    ("lat,lon\n91,2\n", "Out-of-range"),
    ("LATITUDE,LONGITUDE\n14.6,-90.5\n", None),
    (" LAT , LNG \n14.6,-90.5\n", None),
])
def test_qa_csv_inputs(tmp_path, data, expected):
    path = tmp_path / "input.csv"
    path.write_text(data, encoding="utf-8")

    if expected:
        with pytest.raises(ValueError, match=expected):
            CsvSource(str(path)).get_points()
    else:
        assert CsvSource(str(path)).get_points() == [(14.6, -90.5)]


def test_qa_csv_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        CsvSource(str(tmp_path / "missing.csv")).get_points()


def g1_source(handler, *, max_pages=None):
    client = httpx.Client(
        transport=httpx.MockTransport(handler)
    )

    return (
        Group1ApiSource(
            "https://example.invalid/records?source=test",
            "dummy",
            client=client,
            max_pages=max_pages,
        ),
        client,
    )


def test_qa_g1_url_headers_and_pagination():
    requests = []

    def handler(request):
        requests.append(request)
        page = request.url.params["page"]

        return httpx.Response(
            200,
            json={
                "data": (
                    [{"latitude": 14.6, "longitude": -90.5}]
                    if page == "1"
                    else [{"latitude": 14.7, "longitude": -90.6}]
                ),
                "pagination": {
                    "has_next": page == "1"
                },
            },
        )

    source, client = g1_source(handler)

    with client:
        assert source.get_points() == [
            (14.6, -90.5),
            (14.7, -90.6),
        ]

    assert len(requests) == 2
    assert requests[0].headers["x-api-key"] == "dummy"
    assert requests[0].url.params["source"] == "test"


def test_qa_g1_max_pages():
    requests = []

    def handler(request):
        requests.append(request)

        return httpx.Response(
            200,
            json={
                "data": [
                    {"latitude": 14.6, "longitude": -90.5}
                ],
                "pagination": {
                    "has_next": True
                },
            },
        )

    source, client = g1_source(handler, max_pages=1)

    with client:
        assert len(source.get_points()) == 1

    assert len(requests) == 1


@pytest.mark.parametrize("status", [400, 403, 404, 429, 500])
def test_qa_g1_http_error(status):
    source, client = g1_source(
        lambda request: httpx.Response(
            status,
            json={"error": "bad"},
        )
    )

    expected = (
        "unauthorized"
        if status == 403
        else f"HTTP {status}"
    )

    with client, pytest.raises(
        Group1ApiError,
        match=expected,
    ):
        source.get_points()


@pytest.mark.parametrize("response, expected", [
    (
        httpx.Response(200, text="not-json"),
        "not JSON",
    ),
    (
        httpx.Response(200, json=[1, 2]),
        "root must be an object",
    ),
    (
        httpx.Response(200, json={"result": []}),
        "missing data list",
    ),
])
def test_qa_g1_malformed_response(response, expected):
    source, client = g1_source(
        lambda request: response
    )

    with client, pytest.raises(
        Group1ApiError,
        match=expected,
    ):
        source.get_points()


def test_qa_g1_timeout():
    def handler(request):
        raise httpx.ReadTimeout(
            "timed out",
            request=request,
        )

    source, client = g1_source(handler)

    with client, pytest.raises(
        Group1ApiError,
        match="timeout",
    ):
        source.get_points()


def test_qa_g1_generic_network_failure():
    def handler(request):
        raise httpx.ConnectError(
            "offline",
            request=request,
        )

    source, client = g1_source(handler)

    with client, pytest.raises(
        Group1ApiError,
        match="HTTP error",
    ):
        source.get_points()


def test_qa_g1_skips_invalid_and_duplicate_points():
    data = [
        {
            "latitude": "14.600000",
            "longitude": "-90.5",
        },
        {
            "latitude": 14.6000001,
            "longitude": -90.5,
        },
        {
            "latitude": "bad",
            "longitude": 1,
        },
        {
            "latitude": 95,
            "longitude": 1,
        },
        {
            "longitude": 1,
        },
        {
            "latitude": 14.7,
            "longitude": -90.6,
        },
    ]

    assert Group1ApiSource._extract_unique_points(data) == [
        (14.6, -90.5),
        (14.7, -90.6),
    ]


def dw_client_with(handler, *, max_attempts=3, token=None):
    client = httpx.Client(
        transport=httpx.MockTransport(handler)
    )

    return (
        DwClient(
            "https://example.invalid/warehouse",
            client=client,
            max_attempts=max_attempts,
            auth_token=token,
        ),
        client,
    )


def test_qa_dw_authorization_header():
    observed = []

    def handler(request):
        observed.append(request)
        return httpx.Response(201)

    service, client = dw_client_with(
        handler,
        token="test-token",
    )

    with client:
        service.send({"reading_id": "a"})

    assert (
        observed[0].headers["Authorization"]
        == "Bearer test-token"
    )

    assert (
        observed[0].headers["Content-Type"]
        == "application/json"
    )


def test_qa_dw_without_token():
    observed = []

    def handler(request):
        observed.append(request)
        return httpx.Response(204)

    service, client = dw_client_with(handler)

    with client:
        service.send({"reading_id": "a"})

    assert (
        "authorization"
        not in observed[0].headers
    )


@pytest.mark.parametrize("status", [401, 403, 404, 422, 429])
def test_qa_dw_no_retry_for_client_errors(status):
    count = []

    def handler(request):
        count.append(1)
        return httpx.Response(status)

    service, client = dw_client_with(handler)

    with client, pytest.raises(
        DwClientError,
        match=str(status),
    ):
        service.send({"reading_id": "a"})

    assert len(count) == 1


def test_qa_dw_server_error_exhausts_retry(monkeypatch):
    monkeypatch.setattr(
        dw_client.time,
        "sleep",
        lambda t: None,
    )

    count = []

    def handler(request):
        count.append(1)
        return httpx.Response(503)

    service, client = dw_client_with(
        handler,
        max_attempts=3,
    )

    with client, pytest.raises(
        DwClientError,
        match="503",
    ):
        service.send({"reading_id": "a"})

    assert len(count) == 3


def test_qa_dw_timeout_exhausts_retry(monkeypatch):
    monkeypatch.setattr(
        dw_client.time,
        "sleep",
        lambda t: None,
    )

    count = []

    def handler(request):
        count.append(1)

        raise httpx.ReadTimeout(
            "slow",
            request=request,
        )

    service, client = dw_client_with(
        handler,
        max_attempts=2,
    )

    with client, pytest.raises(
        DwClientError,
        match="timeout on attempt 2",
    ):
        service.send({"reading_id": "a"})

    assert len(count) == 2


def test_qa_dw_connection_error_no_retry():
    count = []

    def handler(request):
        count.append(1)

        raise httpx.ConnectError(
            "offline",
            request=request,
        )

    service, client = dw_client_with(handler)

    with client, pytest.raises(
        DwClientError,
        match="HTTP error",
    ):
        service.send({"reading_id": "a"})

    assert len(count) == 1


def test_qa_dw_owned_http_client_is_closed():
    service = DwClient("https://example.invalid")

    with service:
        assert not service._client.is_closed

    assert service._client.is_closed


def test_qa_g1_owned_http_client_is_closed():
    service = Group1ApiSource(
        "https://example.invalid",
        "test-token",
    )

    with service:
        assert not service._client.is_closed

    assert service._client.is_closed
