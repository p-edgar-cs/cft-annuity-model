"""Runs every (sensitivity x scenario) combination and builds the summary table."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import pandas as pd

from .config import apply_overrides, validate
from .inputs import ModelInputs, load_inputs
from .projection import project
from .scenarios import load_scenarios


@dataclass
class RunResults:
    cfg: dict
    summary: pd.DataFrame
    details: dict = field(default_factory=dict)     # (sensitivity, scenario_id) -> DataFrame
    inputs: ModelInputs | None = None
    runtime_seconds: float = 0.0
    labels: dict = field(default_factory=dict)      # scenario_id -> label


def summarise(df: pd.DataFrame) -> dict:
    proj = df[df["period"] > 0]
    end = df.iloc[-1]
    i_min = proj["pv_surplus"].idxmin()
    return {
        "initial_surplus": df.iloc[0]["surplus"],
        "ending_surplus": end["surplus"],
        "pv_ending_surplus": end["pv_surplus"],
        "min_pv_surplus": proj.loc[i_min, "pv_surplus"],
        "min_pv_surplus_time": proj.loc[i_min, "time"],
        "ending_reserve": end["reserve"],
        "total_realised_gains": proj["realised_gains"].sum(),
        "peak_lapse_rate": proj["lapse_rate"].max(),
        "avg_credited_rate_yr1_10": proj.loc[proj["time"] <= 10, "credited_rate"].mean(),
    }


def run_all(cfg: dict, sensitivities: list | None = None, progress=None) -> RunResults:
    """Run all scenarios for each sensitivity. ``sensitivities`` defaults to the
    list in the config (or just the base case if none are defined)."""
    start = time.perf_counter()
    sens_list = sensitivities if sensitivities is not None else cfg.get("sensitivities") or [{"name": "Base", "overrides": {}}]
    rows, details, labels = [], {}, {}
    base_inputs = None
    cache: dict = {}

    for sens in sens_list:
        scfg = apply_overrides(cfg, sens.get("overrides"))
        validate(scfg)
        key = repr(scfg["inputs"])
        if key not in cache:
            cache[key] = load_inputs(scfg)
        inputs = cache[key]
        base_inputs = base_inputs or inputs
        for scen in load_scenarios(scfg):
            if progress:
                progress(sens["name"], scen)
            df = project(scfg, inputs, scen)
            details[(sens["name"], scen.id)] = df
            labels[scen.id] = scen.label
            rows.append({"sensitivity": sens["name"], "scenario": scen.id, "scenario_label": scen.label, **summarise(df)})

    summary = pd.DataFrame(rows)
    return RunResults(cfg=cfg, summary=summary, details=details, inputs=base_inputs,
                      runtime_seconds=time.perf_counter() - start, labels=labels)
