"""Tests for coordinate sources (CSV + Group 1 API)."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.sources import CsvSource, Group1ApiError, Group1ApiSource


def test_csv_source_loads_points(tmp_path) -> None:
    path = tmp_path / "points.csv"
    path.write_text("lat,lon\n14.63,-90.50\n14.64,-90.51\n", encoding="utf-8")
    assert CsvSource(str(path)).get_points() == [(14.63, -90.50), (14.64, -90.51)]


@respx.mock
def test_group1_api_source_paginates_and_dedupes() -> None:
    url = "https://api.example.test/api/records"
    route = respx.get(url__regex=r"https://api\.example\.test/api/records.*").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "reading_id": "a",
                            "latitude": 14.6096277,
                            "longitude": -90.5055252,
                        },
                        {
                            "reading_id": "b",
                            "latitude": 14.60962771,
                            "longitude": -90.50552521,
                        },
                    ],
                    "pagination": {
                        "page": 1,
                        "limit": 2,
                        "has_next": True,
                    },
                },
            ),
            httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "reading_id": "c",
                            "latitude": 14.6383643,
                            "longitude": -90.6803163,
                        }
                    ],
                    "pagination": {
                        "page": 2,
                        "limit": 2,
                        "has_next": False,
                    },
                },
            ),
        ]
    )

    with Group1ApiSource(url, "test-key", page_limit=2) as source:
        points = source.get_points()

    assert len(route.calls) == 2
    assert route.calls[0].request.headers["x-api-key"] == "test-key"
    assert "page=1" in str(route.calls[0].request.url)
    assert "limit=2" in str(route.calls[0].request.url)
    assert points == [
        (14.6096277, -90.5055252),
        (14.6383643, -90.6803163),
    ]


@respx.mock
def test_group1_api_source_unauthorized() -> None:
    url = "https://api.example.test/api/records"
    respx.get(url__regex=r"https://api\.example\.test/api/records.*").mock(
        return_value=httpx.Response(401, json={"error": "nope"})
    )

    with Group1ApiSource(url, "bad-key") as source:
        with pytest.raises(Group1ApiError, match="unauthorized"):
            source.get_points()


def test_group1_requires_url_and_token() -> None:
    with pytest.raises(ValueError, match="GROUP1_API_URL"):
        Group1ApiSource("", "key")
    with pytest.raises(ValueError, match="GROUP1_API_TOKEN"):
        Group1ApiSource("https://api.example.test/api/records", "")
