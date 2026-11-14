"""Coordinate input sources (CSV and Group 1 API)."""

from __future__ import annotations

import csv
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 10.0
DEFAULT_PAGE_LIMIT = 100
COORD_ROUND_DECIMALS = 5


class Group1ApiError(Exception):
    """Raised when the Group 1 records API fails."""


class CoordinateSource(ABC):
    """Interface for obtaining real GPS points."""

    @abstractmethod
    def get_points(self) -> list[tuple[float, float]]:
        """Return list of (latitude, longitude) from a real source."""


class CsvSource(CoordinateSource):
    """Load lat,lon pairs from a CSV file of real GPS points."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def get_points(self) -> list[tuple[float, float]]:
        if not self.path.exists():
            raise FileNotFoundError(f"CSV not found: {self.path}")

        points: list[tuple[float, float]] = []
        with self.path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            if not reader.fieldnames:
                raise ValueError("CSV has no header row")

            field_map = {name.lower().strip(): name for name in reader.fieldnames}
            lat_key = field_map.get("lat") or field_map.get("latitude")
            lon_key = field_map.get("lon") or field_map.get("lng") or field_map.get("longitude")
            if not lat_key or not lon_key:
                raise ValueError("CSV must include lat/latitude and lon/longitude columns")

            for i, row in enumerate(reader, start=2):
                try:
                    lat = float(row[lat_key])
                    lon = float(row[lon_key])
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError(f"Invalid lat/lon at CSV line {i}") from exc
                if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
                    raise ValueError(f"Out-of-range coordinates at CSV line {i}")
                points.append((lat, lon))

        logger.info("Loaded %s points from %s", len(points), self.path)
        return points


class Group1ApiSource(CoordinateSource):
    """
    Fetch real GPS points from Group 1's Noise Monitor records API.

    Auth: header ``x-api-key`` (not ``x-api-token``).
    Response shape: ``{"data": [...], "pagination": {...}}``.
    """

    def __init__(
        self,
        api_url: str,
        api_key: str,
        *,
        page_limit: int = DEFAULT_PAGE_LIMIT,
        max_pages: int | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_url:
            raise ValueError("GROUP1_API_URL is required")
        if not api_key:
            raise ValueError("GROUP1_API_TOKEN is required")
        self.api_url = api_url.rstrip("/")
        self._api_key = api_key
        self._page_limit = page_limit
        self._max_pages = max_pages
        self._timeout_s = timeout_s
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout_s)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> Group1ApiSource:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def get_points(self) -> list[tuple[float, float]]:
        records = self._fetch_all_records()
        points = self._extract_unique_points(records)
        logger.info(
            "Loaded %s unique points from Group 1 (%s records)",
            len(points),
            len(records),
        )
        return points

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "x-api-key": self._api_key,
        }

    def _page_url(self, page: int) -> str:
        parsed = urlparse(self.api_url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["page"] = str(page)
        query["limit"] = str(self._page_limit)
        return urlunparse(parsed._replace(query=urlencode(query)))

    def _fetch_all_records(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        page = 1
        while True:
            if self._max_pages is not None and page > self._max_pages:
                break
            payload = self._fetch_page(page)
            batch = payload.get("data")
            if not isinstance(batch, list):
                raise Group1ApiError("G1 response missing data list")
            records.extend(item for item in batch if isinstance(item, dict))

            pagination = payload.get("pagination") or {}
            has_next = bool(pagination.get("has_next"))
            if not has_next or not batch:
                break
            page += 1

        return records

    def _fetch_page(self, page: int) -> dict[str, Any]:
        url = self._page_url(page)
        try:
            response = self._client.get(
                url,
                headers=self._headers(),
                timeout=self._timeout_s,
            )
        except httpx.TimeoutException as exc:
            raise Group1ApiError(f"G1 timeout on page {page}") from exc
        except httpx.HTTPError as exc:
            raise Group1ApiError(f"G1 HTTP error: {exc}") from exc

        if response.status_code == 401 or response.status_code == 403:
            # Never log the API key
            raise Group1ApiError(f"G1 unauthorized HTTP {response.status_code}")
        if response.status_code >= 400:
            raise Group1ApiError(f"G1 HTTP {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise Group1ApiError("G1 response is not JSON") from exc
        if not isinstance(payload, dict):
            raise Group1ApiError("G1 response root must be an object")
        return payload

    @staticmethod
    def _extract_unique_points(records: list[dict[str, Any]]) -> list[tuple[float, float]]:
        points: list[tuple[float, float]] = []
        seen: set[tuple[float, float]] = set()
        for i, item in enumerate(records):
            try:
                lat = float(item["latitude"])
                lon = float(item["longitude"])
            except (KeyError, TypeError, ValueError):
                logger.warning("Skipping G1 record without valid lat/lon at index %s", i)
                continue
            if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
                logger.warning("Skipping out-of-range G1 coordinates at index %s", i)
                continue
            key = (round(lat, COORD_ROUND_DECIMALS), round(lon, COORD_ROUND_DECIMALS))
            if key in seen:
                continue
            seen.add(key)
            points.append((lat, lon))
        return points
