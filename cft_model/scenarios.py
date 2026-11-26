"""Interest rate scenarios as parallel shifts to the starting yield curve.

A scenario is a set of (time, shift) points; the shift at any time is linearly
interpolated and held flat after the last point. The rates for a projection
period use the shift at the END of the period, so in the annual model year 1
of the "gradual increase" scenario is +0.5%, year 10 is +5.0%.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import resolve_path

# The seven NAIC "New York 7" deterministic scenarios.
NY7 = {
    1: ("Level", [(0, 0.0)]),
    2: ("Gradual increase (+0.5%/yr for 10 yrs)", [(0, 0.0), (10, 0.05)]),
    3: ("Up then down (+1%/yr 5 yrs, -1%/yr 5 yrs)", [(0, 0.0), (5, 0.05), (10, 0.0)]),
    4: ("Pop-up +3%", [(0, 0.03)]),
    5: ("Gradual decrease (-0.5%/yr for 10 yrs)", [(0, 0.0), (10, -0.05)]),
    6: ("Down then up (-1%/yr 5 yrs, +1%/yr 5 yrs)", [(0, 0.0), (5, -0.05), (10, 0.0)]),
    7: ("Pop-down -3%", [(0, -0.03)]),
}


@dataclass
class Scenario:
    id: str
    label: str
    times: np.ndarray
    shifts: np.ndarray

    def shift(self, t):
        return np.interp(t, self.times, self.shifts)


def _make(sid, label, points) -> Scenario:
    pts = sorted(points)
    return Scenario(str(sid), label, np.array([p[0] for p in pts], float), np.array([p[1] for p in pts], float))


def ny7_scenarios(include=None) -> list[Scenario]:
    wanted = {str(i) for i in include} if include else {str(i) for i in NY7}
    unknown = wanted - {str(i) for i in NY7}
    if unknown:
        raise ValueError(f"Unknown NY7 scenario ids: {sorted(unknown)} (valid: 1-7)")
    return [_make(i, label, pts) for i, (label, pts) in NY7.items() if str(i) in wanted]


def csv_scenarios(path, include=None, rate_factor: float = 1.0) -> list[Scenario]:
    """CSV columns: scenario, time_years, shift (optional: label)."""
    df = pd.read_csv(path)
    missing = {"scenario", "time_years", "shift"} - set(df.columns)
    if missing:
        raise ValueError(f"Scenario file {path} is missing columns {sorted(missing)}")
    out = []
    for sid, grp in df.groupby("scenario", sort=False):
        if include and str(sid) not in {str(i) for i in include}:
            continue
        label = str(grp["label"].iloc[0]) if "label" in grp else str(sid)
        out.append(_make(sid, label, list(zip(grp["time_years"], grp["shift"] * rate_factor))))
    if not out:
        raise ValueError(f"No scenarios selected from {path} (check scenarios.include)")
    return out


def load_scenarios(cfg: dict) -> list[Scenario]:
    spec = cfg["scenarios"]
    if spec["source"] == "ny7":
        return ny7_scenarios(spec.get("include"))
    factor = 0.01 if cfg.get("inputs", {}).get("rate_units") == "percent" else 1.0
    return csv_scenarios(resolve_path(cfg, spec["csv_path"]), spec.get("include"), factor)
