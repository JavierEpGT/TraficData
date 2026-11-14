"""Data Warehouse REST client (G4)."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 10.0
MAX_ATTEMPTS = 3


class DwClientError(Exception):
    """Raised when the DW POST fails after retries."""


class DwClient:
    """
    POST JSON readings to the G4 Data Warehouse.

    TODO(G2): confirm exact endpoint path and auth scheme with G4.
    """

    def __init__(
        self,
        endpoint_url: str,
        *,
        auth_token: str | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_attempts: int = MAX_ATTEMPTS,
        client: httpx.Client | None = None,
    ) -> None:
        self._url = endpoint_url
        self._auth_token = auth_token
        self._timeout_s = timeout_s
        self._max_attempts = max_attempts
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout_s)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> DwClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._auth_token:
            # TODO(G2): confirm auth header format with G4
            headers["Authorization"] = f"Bearer {self._auth_token}"
        return headers

    def send(self, payload: dict[str, Any]) -> None:
        """POST payload; retry on 5xx with exponential backoff."""
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._client.post(
                    self._url,
                    json=payload,
                    headers=self._headers(),
                    timeout=self._timeout_s,
                )
            except httpx.TimeoutException as exc:
                last_error = DwClientError(f"DW timeout on attempt {attempt}")
                logger.warning("DW timeout attempt=%s", attempt)
                if attempt < self._max_attempts:
                    time.sleep(2 ** (attempt - 1))
                    continue
                raise last_error from exc
            except httpx.HTTPError as exc:
                raise DwClientError(f"DW HTTP error: {exc}") from exc

            if 500 <= response.status_code < 600:
                last_error = DwClientError(f"DW HTTP {response.status_code}")
                logger.warning(
                    "DW retryable status=%s attempt=%s",
                    response.status_code,
                    attempt,
                )
                if attempt < self._max_attempts:
                    time.sleep(2 ** (attempt - 1))
                    continue
                raise last_error

            if response.status_code >= 400:
                # Never log Authorization / tokens
                raise DwClientError(f"DW HTTP {response.status_code}")

            logger.info("DW accepted reading_id=%s", payload.get("reading_id"))
            return

        raise last_error or DwClientError("DW request failed")
