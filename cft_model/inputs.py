"""Loading and validating input files.

Every input file goes through a column mapping from the config, so an extract
with different headers (e.g. from a valuation system or Snowflake) can be used
by editing YAML rather than code. CSV, Excel and Parquet files are supported.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .config import get_dotted, resolve_path
from .curve import YieldCurve


class InputError(ValueError):
    """Raised when an input file is missing columns or has bad values."""


@dataclass
class PolicyData:
    policy_id: np.ndarray
    issue_age: np.ndarray
    duration: np.ndarray          # completed policy years at valuation
    attained_age: np.ndarray
    account_value: np.ndarray     # per policy (per unit of policy_count)
    guaranteed_rate: np.ndarray
    count: np.ndarray             # number of policies each record represents

    @property
    def n(self) -> int:
        return len(self.policy_id)

    @property
    def total_account_value(self) -> float:
        return float(np.sum(self.account_value * self.count))


@dataclass
class AssetData:
    asset_id: np.ndarray
    par: np.ndarray
    coupon: np.ndarray
    maturity: np.ndarray          # years from valuation
    spread: np.ndarray
    book_value: np.ndarray

    @property
    def total_book(self) -> float:
        return float(np.sum(self.book_value))


@dataclass
class ModelInputs:
    policies: PolicyData
    assets: AssetData
    curve: YieldCurve
    mortality: np.ndarray         # qx indexed by integer age
    sources: dict                 # field -> resolved file path (for the run log)


def read_table(path: Path, sheet: str | None = None) -> pd.DataFrame:
    if not path.exists():
        raise InputError(f"Input file not found: {path}")
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix in (".xlsx", ".xlsm", ".xls"):
        return pd.read_excel(path, sheet_name=sheet or 0)
    if suffix == ".parquet":
        return pd.read_parquet(path)
    raise InputError(f"Unsupported file type '{suffix}' for {path} (use .csv, .xlsx or .parquet)")


def _mapped(df: pd.DataFrame, columns: dict, required: list[str], optional: list[str], label: str) -> dict:
    out = {}
    for field in required + optional:
        col = (columns or {}).get(field, field)
        if col in df.columns:
            out[field] = df[col]
        elif field in required:
            raise InputError(
                f"{label}: column '{col}' (model field '{field}') not found.\n"
                f"  Columns in file: {list(df.columns)}\n"
                f"  Fix the mapping under inputs.{label}.columns in your config."
            )
    for field, series in out.items():
        if field.endswith("_id"):
            continue
        values = pd.to_numeric(series, errors="coerce")
        bad = values.isna()
        if bad.any():
            rows = list(np.where(bad)[0][:5] + 2)  # +2: header row and 1-based
            raise InputError(f"{label}: non-numeric or blank values in '{field}' at file rows {rows}")
        out[field] = values.to_numpy(dtype=float)
    return out


def _rate_factor(cfg: dict) -> float:
    return 0.01 if get_dotted(cfg, "inputs.rate_units", "decimal") == "percent" else 1.0


def load_policies(cfg: dict) -> tuple[PolicyData, Path]:
    spec = cfg["inputs"]["policies"]
    path = resolve_path(cfg, spec["path"])
    df = read_table(path, spec.get("sheet"))
    m = _mapped(
        df, spec.get("columns"),
        ["policy_id", "issue_age", "duration", "account_value", "guaranteed_rate"],
        ["policy_count", "attained_age"], "policies",
    )
    count = m.get("policy_count", np.ones(len(df)))
    attained = m.get("attained_age", m["issue_age"] + m["duration"])
    data = PolicyData(
        policy_id=m["policy_id"].to_numpy(),
        issue_age=m["issue_age"],
        duration=m["duration"],
        attained_age=attained,
        account_value=m["account_value"],
        guaranteed_rate=m["guaranteed_rate"] * _rate_factor(cfg),
        count=count,
    )
    if len(df) == 0:
        raise InputError("policies: file has no rows")
    if (data.account_value < 0).any() or (data.count < 0).any() or (data.duration < 0).any():
        raise InputError("policies: account_value, policy_count and duration must be >= 0")
    if (data.guaranteed_rate > 0.25).any():
        raise InputError("policies: guaranteed rates above 25% - are rates in percent? Set inputs.rate_units: percent")
    return data, path


def load_assets(cfg: dict) -> tuple[AssetData, Path]:
    spec = cfg["inputs"]["assets"]
    path = resolve_path(cfg, spec["path"])
    df = read_table(path, spec.get("sheet"))
    m = _mapped(df, spec.get("columns"), ["asset_id", "par", "coupon", "maturity"], ["spread", "book_value"], "assets")
    f = _rate_factor(cfg)
    data = AssetData(
        asset_id=m["asset_id"].to_numpy(),
        par=m["par"],
        coupon=m["coupon"] * f,
        maturity=m["maturity"],
        spread=m.get("spread", np.zeros(len(df))) * f,
        book_value=m.get("book_value", m["par"]),
    )
    if (data.par <= 0).any() or (data.maturity <= 0).any():
        raise InputError("assets: par and maturity_years must be > 0")
    if (data.coupon > 0.25).any():
        raise InputError("assets: coupons above 25% - are rates in percent? Set inputs.rate_units: percent")
    return data, path


def load_curve(cfg: dict) -> tuple[YieldCurve, Path]:
    spec = cfg["inputs"]["yield_curve"]
    path = resolve_path(cfg, spec["path"])
    df = read_table(path, spec.get("sheet"))
    m = _mapped(df, spec.get("columns"), ["tenor", "rate"], [], "yield_curve")
    return YieldCurve(m["tenor"], m["rate"] * _rate_factor(cfg)), path


def load_mortality(cfg: dict) -> tuple[np.ndarray, Path]:
    spec = cfg["inputs"]["mortality"]
    path = resolve_path(cfg, spec["path"])
    df = read_table(path, spec.get("sheet"))
    m = _mapped(df, spec.get("columns"), ["age", "qx"], [], "mortality")
    ages = m["age"].astype(int)
    qx = m["qx"]
    if (qx < 0).any() or (qx > 1).any():
        raise InputError("mortality: qx must be between 0 and 1")
    table = np.ones(max(int(ages.max()) + 1, 131))      # ages beyond the table die
    table[: ages.min()] = qx[np.argmin(ages)]            # ages below the table use the first rate
    table[ages] = qx
    return table, path


def load_inputs(cfg: dict) -> ModelInputs:
    policies, p_path = load_policies(cfg)
    assets, a_path = load_assets(cfg)
    curve, c_path = load_curve(cfg)
    mortality, m_path = load_mortality(cfg)
    return ModelInputs(
        policies=policies, assets=assets, curve=curve, mortality=mortality,
        sources={"policies": p_path, "assets": a_path, "yield_curve": c_path, "mortality": m_path},
    )
