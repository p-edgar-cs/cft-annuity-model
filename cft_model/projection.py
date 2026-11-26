"""Projection engine: projects liabilities and assets together, period by period,
for one interest rate scenario.

Order of operations in each period (length dt = 1 year or 1 month):
  1. Set the scenario's yield curve (starting curve + shift at period end, floored).
  2. Credited rate = max(guaranteed rate, portfolio earned rate - target spread).
  3. Expenses on the business in force at the start of the period.
  4. Account values grow at the credited rate.
  5. Deaths, then lapses (base x dynamic), then maturities at maturity_age.
     Surrenders are paid at account value less the surrender charge.
  6. Assets pay coupons and maturities; cash earns the short rate.
  7. Net cash flow is reinvested in new bonds (if positive) or raised by
     selling bonds at market value / borrowing (if negative).
  8. Interest-related gains/losses on sales go to the Interest Maintenance
     Reserve (IMR) and are released straight-line over the remaining life of
     the bonds sold (if assets.imr.enabled).
  9. Surplus = book value of assets - reserve (account value) - IMR.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .assets import Portfolio
from .config import time_step_years
from .inputs import ModelInputs
from .scenarios import Scenario


def _by_policy_year(table, policy_year, after_end: float | None = None):
    """Look up a per-policy-year table. Values past the end use the last entry,
    or ``after_end`` if given (e.g. 0 for surrender charges)."""
    arr = np.asarray(table, float)
    idx = np.clip(policy_year - 1, 0, len(arr) - 1)
    out = arr[idx]
    if after_end is not None:
        out = np.where(policy_year > len(arr), after_end, out)
    return out


def _to_period(annual_rate, dt: float):
    return 1.0 - (1.0 - annual_rate) ** dt


def project(cfg: dict, inputs: ModelInputs, scenario: Scenario) -> pd.DataFrame:
    dt = time_step_years(cfg)
    n_periods = int(round(cfg["projection"]["years"] / dt))
    floor = cfg["scenarios"].get("rate_floor")
    L, A = cfg["liability"], cfg["assets"]
    lapse_cfg, dyn = L["lapse"], L["lapse"].get("dynamic", {})
    exp_cfg = L["expenses"]
    curve, pol = inputs.curve, inputs.policies
    disc_tenor = cfg.get("reporting", {}).get("discount_tenor", 1)
    cash_tenor = A.get("cash_tenor", 0.25)

    # ---- liability state (per policy record) -------------------------------
    count = pol.count
    av = pol.account_value.astype(float).copy()      # account value per policy
    inforce = np.where(count > 0, 1.0, 0.0)          # fraction of policies still in force
    age0 = pol.attained_age
    max_age = len(inputs.mortality) - 1

    reserve0 = float(np.sum(inforce * count * av))

    # ---- asset state -------------------------------------------------------
    ratio = cfg["inputs"]["assets"].get("scale_to_reserve_ratio")
    scale = (ratio * reserve0 / inputs.assets.total_book) if ratio else 1.0
    port = Portfolio.from_inputs(inputs.assets, dt, scale)

    base_shift = float(scenario.shift(0.0))
    rows = [{
        "period": 0, "time": 0.0, "year": 0,
        "shift": base_shift,
        "book_assets": port.book_total,
        "market_assets": port.market_value(curve, base_shift, floor),
        "cash": port.cash,
        "reserve": reserve0,
        "imr": 0.0,
        "surplus": port.book_total - reserve0,
        "discount_factor": 1.0,
        "pv_surplus": port.book_total - reserve0,
        "policies_in_force": float(np.sum(inforce * count)),
    }]
    discount = 1.0
    imr_on = A.get("imr", {}).get("enabled", False)
    imr_layers: list[list[float]] = []       # [release per period, periods left]
    reinvest_term = A["reinvestment"]["term_years"]
    reinvest_periods = int(round(reinvest_term / dt))

    for p in range(n_periods):
        t0, t1 = p * dt, (p + 1) * dt
        shift = float(scenario.shift(t1))
        short = float(curve.rate(cash_tenor, shift, floor))
        disc_rate = float(curve.rate(disc_tenor, shift, floor))

        # 2. crediting
        earned = port.earned_rate(short)
        credited = np.maximum(pol.guaranteed_rate, earned - L["crediting"]["target_spread"])

        # 3. expenses (start of period)
        units = inforce * count
        av_start_total = float(np.sum(units * av))
        expenses = (exp_cfg["per_policy"] * (1 + exp_cfg.get("inflation", 0.0)) ** t0 * units.sum()
                    + exp_cfg.get("pct_of_av", 0.0) * av_start_total) * dt

        # 4. account value growth
        av_end = av * (1 + credited) ** dt
        interest_credited = float(np.sum(units * (av_end - av)))

        # 5. decrements
        policy_year = np.floor(pol.duration + t0 + 1e-9).astype(int) + 1
        attained = np.clip(np.floor(age0 + t0 + 1e-9).astype(int), 0, max_age)
        q_annual = np.clip(inputs.mortality[attained] * L.get("mortality_multiplier", 1.0), 0, 1)
        q = _to_period(q_annual, dt)

        competitor = float(curve.rate(dyn.get("competitor_tenor", 5), shift, floor)) + dyn.get("competitor_spread", 0.0)
        gap = competitor - credited
        if dyn.get("enabled", False):
            dyn_mult = np.minimum(dyn.get("max_multiplier", np.inf),
                                  1 + dyn.get("slope", 0.0) * np.maximum(0.0, gap - dyn.get("threshold", 0.0)))
        else:
            dyn_mult = np.ones_like(gap)
        w_annual = np.clip(_by_policy_year(lapse_cfg["base_by_policy_year"], policy_year)
                           * lapse_cfg.get("base_multiplier", 1.0) * dyn_mult,
                           0, lapse_cfg.get("max_rate", 1.0))
        w = _to_period(w_annual, dt)
        sc = _by_policy_year(L["surrender_charges"], policy_year, after_end=0.0)

        deaths = units * q
        survivors = units - deaths
        lapses = survivors * w
        remaining = survivors - lapses
        maturing = (age0 + t1 >= L.get("maturity_age", 200) - 1e-9) & (remaining > 0)
        maturities = np.where(maturing, remaining, 0.0)
        remaining = remaining - maturities

        death_benefits = float(np.sum(deaths * av_end))
        surrender_benefits = float(np.sum(lapses * av_end * (1 - sc)))
        surrender_charges = float(np.sum(lapses * av_end * sc))
        maturity_benefits = float(np.sum(maturities * av_end))
        liability_outflow = death_benefits + surrender_benefits + maturity_benefits + expenses

        inforce = np.divide(remaining, count, out=np.zeros_like(remaining), where=count > 0)
        av = av_end
        reserve = float(np.sum(remaining * av))

        # 6. asset cash flows
        cash_interest = port.accrue_cash(short, A.get("borrow_spread", 0.0))
        coupons, redemptions, maturity_gain = port.roll_forward()
        net_cash_flow = coupons + redemptions + cash_interest - liability_outflow
        port.cash += cash_interest + coupons + redemptions - liability_outflow

        # 7. IMR: release one period of each existing layer into surplus
        imr_release = 0.0
        if imr_on:
            for layer in imr_layers:
                imr_release += layer[0]
                layer[1] -= 1
            imr_layers = [layer for layer in imr_layers if layer[1] > 0]

        # 8. reinvest / disinvest
        reinvested = sold = realised = 0.0
        if port.cash > 0 and A["reinvestment"].get("enabled", True):
            new_rate = float(curve.rate(reinvest_term, shift, floor)) + A["reinvestment"]["spread"]
            reinvested = port.cash
            port.buy(port.cash, new_rate, reinvest_periods, A["reinvestment"]["spread"])
        elif port.cash < 0 and A["disinvestment"] == "sell":
            sold, realised, sold_periods = port.sell_to_raise(-port.cash, curve, shift, floor)
            if imr_on and realised != 0.0:       # new layer, released from next period
                n = max(1, int(round(sold_periods)))
                imr_layers.append([realised / n, float(n)])
        realised += maturity_gain
        imr_balance = sum(layer[0] * layer[1] for layer in imr_layers)

        # 9. balance sheet
        book_assets = port.book_total
        surplus = book_assets - reserve - imr_balance
        discount *= (1 + disc_rate) ** (-dt)
        weights = units * av_end          # account-value weights for average rates
        w_sum = float(np.sum(weights))

        rows.append({
            "period": p + 1, "time": t1, "year": int(np.ceil(t1 - 1e-9)),
            "shift": shift,
            "short_rate": short,
            "competitor_rate": competitor,
            "earned_rate": earned,
            "credited_rate": float(np.sum(weights * credited) / w_sum) if w_sum > 0 else np.nan,
            "lapse_rate": float(np.sum(weights * w_annual) / w_sum) if w_sum > 0 else np.nan,
            "interest_credited": interest_credited,
            "death_benefits": death_benefits,
            "surrender_benefits": surrender_benefits,
            "surrender_charges": surrender_charges,
            "maturity_benefits": maturity_benefits,
            "expenses": expenses,
            "liability_outflow": liability_outflow,
            "coupons": coupons,
            "asset_maturities": redemptions,
            "cash_interest": cash_interest,
            "net_cash_flow": net_cash_flow,
            "reinvested": reinvested,
            "assets_sold": sold,
            "realised_gains": realised,
            "imr_release": imr_release,
            "book_assets": book_assets,
            "market_assets": port.market_value(curve, shift, floor),
            "cash": port.cash,
            "reserve": reserve,
            "imr": imr_balance,
            "surplus": surplus,
            "discount_factor": discount,
            "pv_surplus": surplus * discount,
            "policies_in_force": float(np.sum(remaining)),
        })
    return pd.DataFrame(rows)


FLOW_COLUMNS = [
    "interest_credited", "death_benefits", "surrender_benefits", "surrender_charges",
    "maturity_benefits", "expenses", "liability_outflow", "coupons", "asset_maturities",
    "cash_interest", "net_cash_flow", "reinvested", "assets_sold", "realised_gains", "imr_release",
]


def annualise(df: pd.DataFrame) -> pd.DataFrame:
    """Summarise a monthly projection by year: flows are summed, balances and
    rates are taken at year end (rates are averaged)."""
    if df["time"].diff().dropna().round(6).eq(1.0).all():
        return df.copy()
    rate_cols = ["short_rate", "competitor_rate", "earned_rate", "credited_rate", "lapse_rate", "shift"]
    groups = df[df["period"] > 0].groupby("year")
    out = groups[[c for c in FLOW_COLUMNS if c in df]].sum()
    out[rate_cols] = groups[rate_cols].mean()
    last = groups.tail(1).set_index("year")
    for c in ["time", "book_assets", "market_assets", "cash", "reserve", "imr", "surplus",
              "discount_factor", "pv_surplus", "policies_in_force"]:
        out[c] = last[c]
    out = out.reset_index()
    first = df[df["period"] == 0].assign(year=0)
    out = pd.concat([first, out], ignore_index=True)
    out["period"] = out["year"]
    return out[[c for c in df.columns if c in out.columns]]
