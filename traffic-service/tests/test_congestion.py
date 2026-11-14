"""Unit tests for the pure congestion index."""

import pytest

from app.congestion import CongestionError, congestion_index


def test_fluido_equal_times() -> None:
    assert congestion_index(60.0, 60.0) == 0.0


def test_congestionado() -> None:
    # (120 - 60) / 120 = 0.5
    assert congestion_index(120.0, 60.0) == 0.5


def test_clamp_upper() -> None:
    # free > traffic would go negative → clamp to 0
    assert congestion_index(50.0, 80.0) == 0.0


def test_clamp_when_free_near_zero_ratio() -> None:
    # (100 - 1) / 100 = 0.99
    assert congestion_index(100.0, 1.0) == 0.99


def test_rounded_to_4_decimals() -> None:
    # (100 - 33) / 100 = 0.67
    assert congestion_index(100.0, 33.0) == 0.67


def test_division_by_zero_traffic() -> None:
    with pytest.raises(CongestionError):
        congestion_index(0.0, 30.0)


def test_non_positive_free() -> None:
    with pytest.raises(CongestionError):
        congestion_index(40.0, 0.0)


def test_negative_duration() -> None:
    with pytest.raises(CongestionError):
        congestion_index(-10.0, 20.0)
