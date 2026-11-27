"""Check the prespecified paired analysis and independent content graders."""

from pathlib import Path

import pytest


def test_paired_bounds_and_calibration_cap(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "eval"))
    from compression_report24 import quality_lower
    from compression_study24 import ARMS, plan_size
    from scipy.stats import norm

    z2 = norm.ppf(0.95) ** 2
    assert quality_lower(0, 0, 60) == pytest.approx(-z2 / (60 + z2))
    assert quality_lower(0, 12, 12) == -1
    assert quality_lower(12, 0, 12) == pytest.approx((12 - z2) / (12 + z2))
    rows = [
        {"case": i, "arm": arm, "verified_by_deadline": arm != "scoped" or i > 1}
        for i in range(18)
        for arm in ARMS
    ]
    planned = plan_size(rows)
    assert planned["tasks"] == 180 and planned["uncapped_tasks"] == 552
    with pytest.raises(ValueError, match="54"):
        plan_size(rows[:-1])


def test_reference_graders_and_unknown_cost(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "eval"))
    from compression_report24 import cost
    from compression_study24 import grade_output

    assert grade_output("image", "0", {"answers": ["0"]})[0]
    assert not grade_output("image", ".01", {"answers": ["0"]})[0]
    assert grade_output("image", "99", {"answers": ["100"]})[0]
    assert not grade_output("image", "NaN", {"answers": ["100"]})[0]
    assert grade_output("audio", "One, TWO.", {"transcript": "one two"})[0]
    assert not grade_output("audio", "one two", {"transcript": "one three"})[0]
    assert (
        cost(
            {
                "executor_attempts": [],
                "advisor": {
                    "possible_dispatch": True,
                    "model": "unpriced",
                    "usage": {},
                },
            }
        )
        is None
    )
