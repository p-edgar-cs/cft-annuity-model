"""Configuration loading.

* YAML files, with inheritance: a file containing ``extends: base.yaml`` starts
  from base.yaml and only changes the keys it lists.
* Dotted overrides such as ``liability.lapse.base_multiplier=1.5`` (used by the
  command line ``--set`` option and by sensitivities).
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

TIME_STEPS = {"annual": 1.0, "monthly": 1.0 / 12.0}


class ConfigError(ValueError):
    """Raised when the configuration is incomplete or inconsistent."""


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _read_yaml(path: Path, _seen: tuple = ()) -> dict:
    if path in _seen:
        raise ConfigError(f"Circular 'extends' chain involving {path}")
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    parent = data.pop("extends", None)
    if parent:
        data = deep_merge(_read_yaml((path.parent / parent).resolve(), _seen + (path,)), data)
    return data


def get_dotted(cfg: dict, key: str, default: Any = None) -> Any:
    node: Any = cfg
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def set_dotted(cfg: dict, key: str, value: Any) -> None:
    parts = key.split(".")
    node = cfg
    for part in parts[:-1]:
        if not isinstance(node.get(part), dict):
            node[part] = {}
        node = node[part]
    node[parts[-1]] = value


def parse_override(text: str) -> tuple[str, Any]:
    """Parse ``key=value``; the value is read as YAML (so 1.5, true, [1,2] work)."""
    if "=" not in text:
        raise ConfigError(f"Override must look like key=value, got: {text!r}")
    key, raw = text.split("=", 1)
    return key.strip(), yaml.safe_load(raw)


def apply_overrides(cfg: dict, overrides: dict | None) -> dict:
    out = copy.deepcopy(cfg)
    for key, value in (overrides or {}).items():
        set_dotted(out, key, value)
    return out


def flatten(cfg: dict, prefix: str = "") -> dict:
    """Flatten nested dicts to dotted keys (lists are kept as values)."""
    flat = {}
    for key, value in cfg.items():
        if str(key).startswith("_"):
            continue
        full = f"{prefix}{key}"
        if isinstance(value, dict) and value:
            flat.update(flatten(value, full + "."))
        else:
            flat[full] = value
    return flat


def _find_root(config_path: Path) -> Path:
    for parent in config_path.parents:
        if (parent / "cft_model").is_dir():
            return parent
    return config_path.parent


def load_config(path: str | Path, overrides: dict | None = None) -> dict:
    path = Path(path).resolve()
    cfg = apply_overrides(_read_yaml(path), overrides)
    cfg["_meta"] = {"config_path": str(path), "root": str(_find_root(path))}
    validate(cfg)
    return cfg


def resolve_path(cfg: dict, path: str | Path) -> Path:
    """Absolute paths are used as-is; relative paths are tried from the current
    folder first, then from the project root."""
    p = Path(path).expanduser()
    if p.is_absolute():
        return p
    if (Path.cwd() / p).exists():
        return (Path.cwd() / p).resolve()
    return Path(cfg.get("_meta", {}).get("root", ".")) / p


def time_step_years(cfg: dict) -> float:
    return TIME_STEPS[cfg["projection"]["time_step"]]


REQUIRED_KEYS = [
    "projection.time_step",
    "projection.years",
    "inputs.policies.path",
    "inputs.assets.path",
    "inputs.yield_curve.path",
    "inputs.mortality.path",
    "scenarios.source",
    "liability.crediting.target_spread",
    "liability.lapse.base_by_policy_year",
    "liability.surrender_charges",
    "liability.expenses.per_policy",
    "assets.reinvestment.term_years",
    "assets.disinvestment",
]


def validate(cfg: dict) -> None:
    problems = [f"missing key '{k}'" for k in REQUIRED_KEYS if get_dotted(cfg, k) is None]
    step = get_dotted(cfg, "projection.time_step")
    if step is not None and step not in TIME_STEPS:
        problems.append(f"projection.time_step must be one of {sorted(TIME_STEPS)}, got {step!r}")
    years = get_dotted(cfg, "projection.years")
    if years is not None and (not isinstance(years, (int, float)) or years <= 0):
        problems.append("projection.years must be a positive number")
    source = get_dotted(cfg, "scenarios.source")
    if source is not None and source not in ("ny7", "csv"):
        problems.append("scenarios.source must be 'ny7' or 'csv'")
    mode = get_dotted(cfg, "assets.disinvestment")
    if mode is not None and mode not in ("sell", "borrow"):
        problems.append("assets.disinvestment must be 'sell' or 'borrow'")
    units = get_dotted(cfg, "inputs.rate_units", "decimal")
    if units not in ("decimal", "percent"):
        problems.append("inputs.rate_units must be 'decimal' or 'percent'")
    if not get_dotted(cfg, "liability.lapse.base_by_policy_year"):
        problems.append("liability.lapse.base_by_policy_year needs at least one value")
    if problems:
        raise ConfigError("Invalid configuration:\n  - " + "\n  - ".join(problems))
