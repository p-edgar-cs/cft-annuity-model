import numpy as np
import pandas as pd
import pytest

from cft_model.config import apply_overrides
from cft_model.projection import annualise, project
from cft_model.runner import run_all
from cft_model.scenarios import ny7_scenarios

LEVEL, POP_UP = ny7_scenarios([1])[0], ny7_scenarios([4])[0]


def test_starting_balance_sheet(cfg, inputs):
    df = project(cfg, inputs, LEVEL)
    start = df.iloc[0]
    assert start["reserve"] == pytest.approx(inputs.policies.total_account_value)
    # scale_to_reserve_ratio = 1.00 -> no starting surplus
    assert start["book_assets"] == pytest.approx(start["reserve"])
    assert start["surplus"] == pytest.approx(0.0, abs=1e-6)


def test_cash_flows_reconcile_to_book_assets(cfg, inputs):
    """Change in book assets = income - outflows + realised gains. Redemptions are
    cash in, but only swap a bond for cash at book value, so they are excluded."""
    df = project(cfg, inputs, POP_UP)
    change = df["book_assets"].diff().iloc[1:]
    explained = (df["net_cash_flow"] - df["asset_maturities"] + df["realised_gains"]).iloc[1:]
    np.testing.assert_allclose(change, explained, rtol=0, atol=1e-3)


def test_reserve_roll_forward(cfg, inputs):
    """Reserve(t) = Reserve(t-1) + interest credited - benefits paid out of account value."""
    df = project(cfg, inputs, LEVEL).iloc[1:]
    prev = project(cfg, inputs, LEVEL)["reserve"].shift(1).iloc[1:]
    released = df["death_benefits"] + df["surrender_benefits"] + df["surrender_charges"] + df["maturity_benefits"]
    np.testing.assert_allclose(df["reserve"], prev + df["interest_credited"] - released, rtol=1e-9, atol=1e-3)


def test_credited_rate_never_below_guarantee(cfg, inputs):
    harsh = apply_overrides(cfg, {"liability.crediting.target_spread": 0.10})
    df = project(harsh, inputs, ny7_scenarios([7])[0])
    assert (df["credited_rate"].iloc[1:] >= inputs.policies.guaranteed_rate.min() - 1e-12).all()


def test_dynamic_lapses_increase_when_rates_rise(cfg, inputs):
    level = project(cfg, inputs, LEVEL)
    up = project(cfg, inputs, POP_UP)
    assert up["lapse_rate"].iloc[1:6].mean() > level["lapse_rate"].iloc[1:6].mean()
    off = apply_overrides(cfg, {"liability.lapse.dynamic.enabled": False})
    np.testing.assert_allclose(project(off, inputs, POP_UP)["lapse_rate"], project(off, inputs, LEVEL)["lapse_rate"])


def test_block_runs_off(cfg, inputs):
    df = project(cfg, inputs, LEVEL)
    assert df["policies_in_force"].is_monotonic_decreasing
    assert df["reserve"].iloc[-1] < 0.1 * df["reserve"].iloc[0]


def test_imr_only_changes_timing(cfg, inputs):
    on = project(cfg, inputs, POP_UP)
    off = project(apply_overrides(cfg, {"assets.imr.enabled": False}), inputs, POP_UP)
    assert (on["imr"] != 0).any()
    np.testing.assert_allclose(on["book_assets"], off["book_assets"])
    # once the IMR has fully released, surplus is the same with and without it
    if on["imr"].iloc[-1] == pytest.approx(0, abs=1):
        assert on["surplus"].iloc[-1] == pytest.approx(off["surplus"].iloc[-1], rel=1e-9)


def test_borrow_mode_never_sells(cfg, inputs):
    borrow = apply_overrides(cfg, {"assets.disinvestment": "borrow"})
    df = project(borrow, inputs, POP_UP)
    assert (df["assets_sold"].fillna(0) == 0).all()


def test_monthly_close_to_annual(cfg, inputs):
    monthly = apply_overrides(cfg, {"projection.time_step": "monthly"})
    a = project(cfg, inputs, LEVEL)
    m = project(monthly, inputs, LEVEL)
    assert len(m) == 12 * len(a) - 11
    # same order of magnitude: timing differences only
    assert m["reserve"].iloc[-1] == pytest.approx(a["reserve"].iloc[-1], rel=0.25)
    assert m["surplus"].iloc[-1] == pytest.approx(a["surplus"].iloc[-1], rel=0.25)
    yearly = annualise(m)
    assert len(yearly) == len(a)
    assert yearly["death_benefits"].iloc[1] == pytest.approx(m["death_benefits"].iloc[1:13].sum())


def test_run_all_shapes(cfg):
    res = run_all(cfg)
    n_sens, n_scen = len(cfg["sensitivities"]), len(cfg["scenarios"]["include"])
    assert len(res.summary) == n_sens * n_scen
    assert set(res.summary["sensitivity"]) == {s["name"] for s in cfg["sensitivities"]}
    assert isinstance(res.details[("Base", "1")], pd.DataFrame)


def test_spread_sensitivity_direction(cfg):
    """Crediting more (lower target spread) must reduce surplus in the level scenario."""
    res = run_all(cfg, [{"name": "Base", "overrides": {}},
                        {"name": "Low spread", "overrides": {"liability.crediting.target_spread": 0.01}}])
    s = res.summary[res.summary["scenario"] == "1"].set_index("sensitivity")
    assert s.loc["Low spread", "pv_ending_surplus"] < s.loc["Base", "pv_ending_surplus"]
