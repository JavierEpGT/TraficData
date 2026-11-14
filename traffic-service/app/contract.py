"""Build and validate the shared Data Contract payload."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.models import DataContractPayload, DomainData

logger = logging.getLogger(__name__)


def utc_timestamp_z(now: datetime | None = None) -> str:
    """ISO-8601 UTC timestamp to seconds with Z suffix."""
    dt = now or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def compute_auth_hash(student_id: str, timestamp: str, secret_key: str) -> str:
    """
    SHA256 hex (lowercase) of student_id + timestamp + secret_key.

    TODO(G2): confirm exact hash format with G4 Data Warehouse team.
    """
    material = f"{student_id}{timestamp}{secret_key}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def build_payload(
    *,
    latitude: float,
    longitude: float,
    student_id: str,
    secret_key: str,
    traffic_congestion_index: float,
    timestamp: str | None = None,
    reading_id: Any | None = None,
) -> DataContractPayload:
    """Assemble a validated Data Contract payload for the DW."""
    ts = timestamp or utc_timestamp_z()
    rid = reading_id or uuid4()
    auth_hash = compute_auth_hash(student_id, ts, secret_key)

    # TODO(G2): confirm null handling for noise_db / air_quality_aqi with G4
    payload = DataContractPayload(
        reading_id=rid,
        timestamp=ts,
        latitude=latitude,
        longitude=longitude,
        student_id=student_id,
        auth_hash=auth_hash,
        domain_data=DomainData(
            noise_db=None,
            traffic_congestion_index=traffic_congestion_index,
            air_quality_aqi=None,
        ),
    )
    logger.debug("Built data contract payload reading_id=%s", rid)
    return payload


def payload_to_dict(payload: DataContractPayload) -> dict[str, Any]:
    """Serialize payload for JSON POST (UUID as str)."""
    return payload.model_dump(mode="json")
