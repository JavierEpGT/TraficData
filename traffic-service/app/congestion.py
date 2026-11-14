"""Pure congestion index calculation."""

from __future__ import annotations


class CongestionError(ValueError):
    """Raised when the congestion index cannot be computed from inputs."""


def congestion_index(t_traffic_s: float, t_free_s: float) -> float:
    """
    Compute congestion index in [0, 1].

    indice = (t_traffic - t_free) / t_traffic, clamped to [0, 1], rounded to 4 decimals.
    0 = free flow, 1 = collapsed.
    """
    if t_traffic_s <= 0 or t_free_s <= 0:
        raise CongestionError("durations must be > 0")

    raw = (t_traffic_s - t_free_s) / t_traffic_s
    clamped = max(0.0, min(1.0, raw))
    return round(clamped, 4)
