"""Relative performance from stored ratios.

A ratio is `submission_time / reference_solution_time`, both measured on the
same machine moments apart, so hardware and ambient load cancel out. Lower is
faster; below 1.0 means faster than the reference.
"""

from __future__ import annotations

#: Below this many samples a percentile is noise dressed up as insight, so the
#: API returns None and the UI shows nothing rather than something misleading.
MIN_SAMPLES = 5


def percentile_for(ratio: float, ratios: list[float]) -> tuple[int | None, int, bool]:
    """Return (percentile, sample count, whether there were enough samples).

    The percentile is the share of recorded submissions this one is faster than.
    Ties count as half, which keeps identical submissions off both 0 and 100.
    """
    samples = len(ratios)
    if samples < MIN_SAMPLES:
        return None, samples, False

    slower = sum(1 for r in ratios if r > ratio)
    equal = sum(1 for r in ratios if r == ratio)
    share = (slower + equal / 2) / samples
    return round(share * 100), samples, True
