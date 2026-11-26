"""Generates the synthetic (made-up) input files in data/.

Nothing here is real company data. The mortality table is an illustrative
Gompertz-Makeham curve and the yield curve is illustrative; replace them with
the SOA 2012 IAM table (mort.soa.org) and a Treasury curve (treasury.gov) for a
more realistic run - see the README.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ILLUSTRATIVE_CURVE = {   # tenor (years): annual rate
    0.25: 0.0400, 0.5: 0.0395, 1: 0.0380, 2: 0.0360, 3: 0.0358, 5: 0.0370,
    7: 0.0388, 10: 0.0412, 20: 0.0465, 30: 0.0472,
}


def make_policies(n: int = 1000, seed: int = 2025) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    issue_age = rng.integers(45, 81, n)
    duration = rng.integers(0, 10, n)                         # 0-9 completed years
    account_value = np.round(rng.lognormal(np.log(90_000), 0.6, n), 2)
    # older policies were sold with higher minimum guarantees
    guaranteed = np.where(duration >= 6, rng.choice([0.02, 0.025, 0.03], n, p=[0.3, 0.4, 0.3]),
                          rng.choice([0.01, 0.015, 0.02], n, p=[0.4, 0.4, 0.2]))
    return pd.DataFrame({
        "policy_id": [f"P{i:05d}" for i in range(1, n + 1)],
        "issue_age": issue_age,
        "duration_years": duration,
        "attained_age": issue_age + duration,
        "account_value": account_value,
        "guaranteed_min_rate": guaranteed,
        "policy_count": 1,
    })


def make_assets(total_par: float, n: int = 15, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    maturity = np.sort(rng.choice(np.arange(1, 21), n, replace=False)).astype(float)
    spread = rng.choice([0.008, 0.011, 0.014, 0.018], n)
    treasury = np.interp(maturity, list(ILLUSTRATIVE_CURVE), list(ILLUSTRATIVE_CURVE.values()))
    coupon = np.round(treasury + spread + rng.normal(-0.003, 0.004, n), 4)   # bought at different times
    weights = rng.dirichlet(np.ones(n) * 3)
    par = np.round(total_par * weights, -3)
    return pd.DataFrame({
        "asset_id": [f"BOND{i:02d}" for i in range(1, n + 1)],
        "par": par, "coupon_rate": coupon, "maturity_years": maturity,
        "credit_spread": spread, "book_value": par,
    })


def make_mortality() -> pd.DataFrame:
    ages = np.arange(0, 121)
    qx = np.clip(0.0002 + 0.00002 * 1.1 ** ages, 0, 1)
    qx[-1] = 1.0
    return pd.DataFrame({"age": ages, "qx": np.round(qx, 6)})


def make_curve() -> pd.DataFrame:
    return pd.DataFrame({"tenor_years": list(ILLUSTRATIVE_CURVE), "rate": list(ILLUSTRATIVE_CURVE.values())})


def make_custom_scenarios() -> pd.DataFrame:
    rows = [
        ("Base", "Level rates", 0, 0.0),
        ("Inverted shock", "Short-term spike then fall", 0, 0.0),
        ("Inverted shock", "Short-term spike then fall", 1, 0.025),
        ("Inverted shock", "Short-term spike then fall", 3, -0.01),
        ("Inverted shock", "Short-term spike then fall", 6, 0.0),
        ("Slow rise 2pct", "+2% over 20 years", 0, 0.0),
        ("Slow rise 2pct", "+2% over 20 years", 20, 0.02),
    ]
    return pd.DataFrame(rows, columns=["scenario", "label", "time_years", "shift"])


def write_all(data_dir: str | Path = "data", n_policies: int = 1000, seed: int = 2025) -> dict:
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    policies = make_policies(n_policies, seed)
    files = {
        "policies.csv": policies,
        "assets.csv": make_assets(policies["account_value"].sum() * 1.03),
        "mortality_illustrative.csv": make_mortality(),
        "yield_curve.csv": make_curve(),
        "scenarios_custom_example.csv": make_custom_scenarios(),
    }
    for name, df in files.items():
        df.to_csv(data_dir / name, index=False)
    return {name: len(df) for name, df in files.items()}
