import numpy as np
import pytest

from cft_model.curve import YieldCurve
from cft_model.scenarios import csv_scenarios, ny7_scenarios


def test_ny7_shapes():
    s = {sc.id: sc for sc in ny7_scenarios()}
    assert len(s) == 7
    assert s["1"].shift(7) == 0
    assert s["2"].shift(1) == pytest.approx(0.005)
    assert s["2"].shift(10) == pytest.approx(0.05)
    assert s["2"].shift(25) == pytest.approx(0.05)          # level after year 10
    assert s["3"].shift(5) == pytest.approx(0.05)
    assert s["3"].shift(10) == pytest.approx(0.0)
    assert s["4"].shift(0.5) == pytest.approx(0.03)
    assert s["6"].shift(5) == pytest.approx(-0.05)
    assert s["7"].shift(20) == pytest.approx(-0.03)


def test_ny7_subset_and_bad_id():
    assert [s.id for s in ny7_scenarios([1, 4])] == ["1", "4"]
    with pytest.raises(ValueError):
        ny7_scenarios([8])


def test_curve_interpolation_floor_and_discounting():
    c = YieldCurve([1, 10], [0.02, 0.04])
    assert c.rate(5.5) == pytest.approx(0.03)
    assert c.rate(0.1) == pytest.approx(0.02)                # flat extrapolation
    assert c.rate(30) == pytest.approx(0.04)
    assert c.rate(1, shift=-0.05, floor=0.0025) == pytest.approx(0.0025)
    assert c.discount_factors([2], shift=0.0)[0] == pytest.approx(1 / 1.0222222 ** 2, rel=1e-6)


def test_csv_scenarios(tmp_path):
    f = tmp_path / "s.csv"
    f.write_text("scenario,time_years,shift\nA,0,0\nA,10,0.02\nB,0,-0.01\n")
    out = {s.id: s for s in csv_scenarios(f)}
    assert out["A"].shift(5) == pytest.approx(0.01)
    assert out["B"].shift(3) == pytest.approx(-0.01)
    assert [s.id for s in csv_scenarios(f, include=["B"])] == ["B"]
