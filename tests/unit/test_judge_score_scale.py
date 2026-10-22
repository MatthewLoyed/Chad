"""Judge scores are 0–10 with inefficiency docking."""

from evals.judges import (
    PASS_THRESHOLD,
    _coerce_score_0_10,
    _dock_for_inefficiencies,
)


def test_pass_threshold_is_six_of_ten():
    assert PASS_THRESHOLD == 6.0


def test_coerce_legacy_fraction_to_ten():
    assert _coerce_score_0_10(0.8) == 8.0
    assert _coerce_score_0_10(1.0) == 10.0
    assert _coerce_score_0_10(0.05) == 0.5


def test_coerce_already_out_of_ten():
    assert _coerce_score_0_10(8) == 8.0
    assert _coerce_score_0_10(10) == 10.0
    assert _coerce_score_0_10(12) == 10.0
    assert _coerce_score_0_10(0) == 0.0


def test_dock_inefficiencies_prevents_perfect_ten():
    assert _dock_for_inefficiencies(10.0, ["double Scout"]) == 9.0
    assert _dock_for_inefficiencies(10.0, ["a", "b"]) == 8.0
    assert _dock_for_inefficiencies(8.0, ["a"]) == 8.0  # already docked enough
    assert _dock_for_inefficiencies(10.0, []) == 10.0
