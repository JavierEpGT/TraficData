"""Tests for auth hash and Data Contract payload validation."""

import hashlib

import pytest
from pydantic import ValidationError

from app.contract import build_payload, compute_auth_hash, payload_to_dict
from app.models import DataContractPayload, DomainData


def test_auth_hash_known_value() -> None:
    """Known SHA256 of student_id + timestamp + secret_key (UTF-8, no separators)."""
    student_id = "g2-test-student"
    timestamp = "2026-10-10T22:30:00Z"
    secret_key = "test-secret-key"
    expected = hashlib.sha256(
        f"{student_id}{timestamp}{secret_key}".encode("utf-8")
    ).hexdigest()
    actual = compute_auth_hash(student_id, timestamp, secret_key)
    assert actual == expected
    assert len(actual) == 64
    assert actual == actual.lower()


def test_auth_hash_stable_vector() -> None:
    assert (
        compute_auth_hash("alice", "2026-10-10T22:30:00Z", "secret")
        == "093223b9650ac2abd855d6a6229ec943fd2fa13abeb44e88a4ddbd48aab6e877"
    )


def test_payload_valid() -> None:
    payload = build_payload(
        latitude=14.6349,
        longitude=-90.5069,
        student_id="g2-test-student",
        secret_key="test-secret-key",
        traffic_congestion_index=0.4321,
        timestamp="2026-10-10T22:30:00Z",
    )
    data = payload_to_dict(payload)
    assert set(data.keys()) == {
        "reading_id",
        "timestamp",
        "latitude",
        "longitude",
        "student_id",
        "auth_hash",
        "domain_data",
    }
    assert data["domain_data"]["noise_db"] is None
    assert data["domain_data"]["air_quality_aqi"] is None
    assert data["domain_data"]["traffic_congestion_index"] == 0.4321
    assert data["auth_hash"] == compute_auth_hash(
        "g2-test-student", "2026-10-10T22:30:00Z", "test-secret-key"
    )
    DataContractPayload.model_validate(data)


def test_payload_invalid_timestamp() -> None:
    with pytest.raises(ValidationError):
        DataContractPayload(
            reading_id="550e8400-e29b-41d4-a716-446655440000",
            timestamp="2026-10-10T22:30:00+00:00",
            latitude=14.0,
            longitude=-90.0,
            student_id="x",
            auth_hash="a" * 64,
            domain_data=DomainData(traffic_congestion_index=0.1),
        )


def test_payload_invalid_index() -> None:
    with pytest.raises(ValidationError):
        DomainData(traffic_congestion_index=1.5)


def test_payload_invalid_auth_hash() -> None:
    with pytest.raises(ValidationError):
        DataContractPayload(
            reading_id="550e8400-e29b-41d4-a716-446655440000",
            timestamp="2026-10-10T22:30:00Z",
            latitude=14.0,
            longitude=-90.0,
            student_id="x",
            auth_hash="NOT_HEX",
            domain_data=DomainData(traffic_congestion_index=0.1),
        )


def test_payload_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        DataContractPayload.model_validate(
            {
                "reading_id": "550e8400-e29b-41d4-a716-446655440000",
                "timestamp": "2026-10-10T22:30:00Z",
                "latitude": 14.0,
                "longitude": -90.0,
                "student_id": "x",
                "auth_hash": "a" * 64,
                "domain_data": {
                    "noise_db": None,
                    "traffic_congestion_index": 0.2,
                    "air_quality_aqi": None,
                },
                "extra_field": "nope",
            }
        )
