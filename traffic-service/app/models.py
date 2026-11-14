"""Pydantic models for the shared Data Contract and API I/O."""

import re
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
AUTH_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class DomainData(BaseModel):
    """Domain payload shared by all four groups."""

    model_config = ConfigDict(extra="forbid")

    # TODO(G2): confirm with G4 that null is accepted for unused domains
    noise_db: float | None = None
    traffic_congestion_index: float = Field(..., ge=0.0, le=1.0)
    air_quality_aqi: float | None = None


class DataContractPayload(BaseModel):
    """Standard reading payload sent to the Data Warehouse (G4)."""

    model_config = ConfigDict(extra="forbid")

    reading_id: UUID
    timestamp: str
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    student_id: str = Field(..., min_length=1)
    auth_hash: str
    domain_data: DomainData

    @field_validator("reading_id")
    @classmethod
    def must_be_uuid4(cls, value: UUID) -> UUID:
        if value.version != 4:
            raise ValueError("reading_id must be a UUID v4")
        return value

    @field_validator("timestamp")
    @classmethod
    def timestamp_iso_z(cls, value: str) -> str:
        if not TIMESTAMP_RE.match(value):
            raise ValueError("timestamp must be ISO-8601 UTC to seconds with Z suffix")
        return value

    @field_validator("auth_hash")
    @classmethod
    def auth_hash_hex(cls, value: str) -> str:
        if not AUTH_HASH_RE.match(value):
            raise ValueError("auth_hash must be 64 lowercase hex characters")
        return value


class ReadingRequest(BaseModel):
    """API request body for a single traffic reading."""

    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)


class ReadingResponse(BaseModel):
    """API response after processing a reading."""

    status: str
    payload: dict[str, Any] | None = None
    error: str | None = None


class FeedQuery(BaseModel):
    """Query params for the G4 Prefect feed."""

    since: str | None = None
    limit: int = Field(default=100, ge=1, le=500)

    @field_validator("since")
    @classmethod
    def since_iso_z(cls, value: str | None) -> str | None:
        if value is not None and not TIMESTAMP_RE.match(value):
            raise ValueError("since must be ISO-8601 UTC to seconds with Z suffix")
        return value


class FeedResponse(BaseModel):
    """Paginated Data Contract list for G4 Prefect."""

    count: int
    items: list[DataContractPayload]
