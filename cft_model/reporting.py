"""Writes run outputs: Excel workbook, CSVs, charts, run log and change log."""
from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from . import __version__
from .config import flatten, get_dotted, resolve_path
from .projection import annualise
from .runner import RunResults

MONEY_COLS = {
    "initial_surplus", "ending_surplus", "pv_ending_surplus", "min_pv_surplus", "ending_reserve",
    "total_realised_gains", "interest_credited", "death_benefits", "surrender_benefits",
    "surrender_charges", "maturity_benefits", "expenses", "liability_outflow", "coupons",
    "asset_maturities", "cash_interest", "net_cash_flow", "reinvested", "assets_sold",
    "realised_gains", "imr_release", "imr", "book_assets", "market_assets", "cash", "reserve", "surplus", "pv_surplus",
}
RATE_COLS = {"shift", "short_rate", "competitor_rate", "earned_rate", "credited_rate", "lapse_rate",
             "peak_lapse_rate", "avg_credited_rate_yr1_10"}


METRIC_TITLES = {
    "pv_ending_surplus": "PV of ending surplus",
    "min_pv_surplus": "Minimum PV of surplus",
    "ending_surplus": "Ending surplus",
}


def base_name(results: RunResults) -> str:
    return results.summary["sensitivity"].iloc[0]


def adequacy_metric(cfg: dict) -> str:
    return get_dotted(cfg, "reporting.adequacy_metric", "pv_ending_surplus")


def make_output_dir(cfg: dict) -> Path:
    root = resolve_path(cfg, cfg["run"].get("output_dir", "output"))
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = root / f"{cfg['run'].get('name', 'run')}_{stamp}"
    suffix = 1
    while out.exists():
        out = root / f"{cfg['run'].get('name', 'run')}_{stamp}_{suffix}"
        suffix += 1
    (out / "charts").mkdir(parents=True)
    return out


def detail_table(results: RunResults, key) -> pd.DataFrame:
    df = results.details[key]
    if get_dotted(results.cfg, "reporting.detail_frequency", "annual") == "annual":
        df = annualise(df)
    return df


def matrix(results: RunResults, value: str) -> pd.DataFrame:
    s = results.summary
    m = s.pivot_table(index=["scenario", "scenario_label"], columns="sensitivity", values=value, sort=False)
    return m[list(dict.fromkeys(s["sensitivity"]))].reset_index()


# --------------------------------------------------------------------- Excel
def write_excel(results: RunResults, path: Path) -> None:
    cfg = results.cfg
    with pd.ExcelWriter(path, engine="xlsxwriter") as xw:
        wb = xw.book
        money = wb.add_format({"num_format": "#,##0;[Red](#,##0)"})
        pct = wb.add_format({"num_format": "0.00%"})
        bold = wb.add_format({"bold": True})
        title = wb.add_format({"bold": True, "font_size": 14})

        def put(df: pd.DataFrame, sheet: str, start_row: int = 2, heading: str | None = None):
            df.to_excel(xw, sheet_name=sheet, index=False, startrow=start_row)
            ws = xw.sheets[sheet]
            if heading:
                ws.write(0, 0, heading, title)
            for j, col in enumerate(df.columns):
                fmt = money if col in MONEY_COLS or col in set(s_names) else pct if col in RATE_COLS else None
                ws.set_column(j, j, max(12, min(40, len(str(col)) + 2)), fmt)
            ws.freeze_panes(start_row + 1, 0)
            return ws

        s_names = list(dict.fromkeys(results.summary["sensitivity"]))
        base = base_name(results)
        summ = results.summary[results.summary["sensitivity"] == base].drop(columns=["sensitivity"])
        ws = put(summ, "Summary", heading=f"{cfg['run'].get('block', '')} - {base} results by scenario")
        ws.write(1, 0, f"Valuation date {cfg['run'].get('valuation_date')} | time step "
                       f"{cfg['projection']['time_step']} | {cfg['projection']['years']} years | synthetic data", bold)

        put(matrix(results, "pv_ending_surplus"), "PV ending surplus", heading="PV of ending surplus: scenario x sensitivity")
        put(matrix(results, "min_pv_surplus"), "Min PV surplus", heading="Minimum PV of surplus over the projection")
        put(results.summary, "All runs", heading="All runs")

        for (sens, sid), _ in results.details.items():
            if sens != base:
                continue
            label = results.labels.get(sid, sid)
            sheet = f"S{sid} {label.split(' (')[0]}"[:31]
            for ch in '[]:*?/\\':
                sheet = sheet.replace(ch, "-")
            put(detail_table(results, (sens, sid)), sheet, heading=f"Scenario {sid}: {label} ({base})")

        flat = flatten(cfg)
        assumptions = pd.DataFrame({"setting": list(flat), "value": [json.dumps(v) if isinstance(v, (list, dict)) else v for v in flat.values()]})
        ws = put(assumptions, "Assumptions", heading="Configuration used for this run")
        ws.set_column(0, 0, 50)
        ws.set_column(1, 1, 60)

        inp = results.inputs
        info = pd.DataFrame([
            ("Policies (records)", inp.policies.n),
            ("Policies (count)", float(inp.policies.count.sum())),
            ("Account value", inp.policies.total_account_value),
            ("Average attained age", float(np.average(inp.policies.attained_age, weights=inp.policies.account_value * inp.policies.count))),
            ("Bonds", len(inp.assets.par)),
            ("Bond book value (before scaling)", inp.assets.total_book),
        ], columns=["item", "value"])
        ws = put(info, "Inputs", heading="Input data summary")
        ws.set_column(0, 0, 36)
        ws.set_column(1, 1, 18, money)


# -------------------------------------------------------------------- charts
def write_charts(results: RunResults, chart_dir: Path) -> dict:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    base = base_name(results)
    keys = [k for k in results.details if k[0] == base]
    files = {}

    def line_chart(column, ylabel, fname, title, scale=1e6, as_pct=False):
        fig, ax = plt.subplots(figsize=(9, 5))
        for key in keys:
            df = detail_table(results, key)
            df = df[df["period"] > 0] if column in RATE_COLS else df
            y = df[column] * (100 if as_pct else 1 / scale)
            ax.plot(df["time"], y, label=f"{key[1]}: {results.labels[key[1]]}", linewidth=1.8)
        ax.axhline(0, color="black", linewidth=0.8)
        ax.set_xlabel("Projection year")
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8, loc="best")
        fig.tight_layout()
        fig.savefig(chart_dir / fname, dpi=150)
        plt.close(fig)
        files[column] = f"charts/{fname}"

    line_chart("surplus", "Surplus ($ millions)", "surplus_by_scenario.png", "Statutory surplus by scenario")
    line_chart("pv_surplus", "PV of surplus ($ millions)", "pv_surplus_by_scenario.png", "Present value of surplus by scenario")
    line_chart("lapse_rate", "Annual lapse rate (%)", "lapse_rate_by_scenario.png", "Lapse rate by scenario (incl. dynamic lapses)", as_pct=True)
    line_chart("credited_rate", "Credited rate (%)", "credited_rate_by_scenario.png", "Average credited rate by scenario", as_pct=True)

    metric = adequacy_metric(results.cfg)
    summ = results.summary[results.summary["sensitivity"] == base]
    fig, ax = plt.subplots(figsize=(9, 4.5))
    vals = summ[metric] / 1e6
    ax.bar([f"{s}\n{l.split(' (')[0][:20]}" for s, l in zip(summ["scenario"], summ["scenario_label"])], vals,
           color=["#2a7f62" if v >= 0 else "#b8423b" for v in vals])
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_ylabel("$ millions")
    ax.set_title(f"{METRIC_TITLES.get(metric, metric)} by scenario ({base})")
    ax.tick_params(axis="x", labelsize=7)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(chart_dir / "adequacy_by_scenario.png", dpi=150)
    plt.close(fig)
    files["adequacy"] = "charts/adequacy_by_scenario.png"
    return files


# ------------------------------------------------------------ run/change log
def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_commit(root: Path) -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True, text=True, timeout=5)
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, timeout=5)
        if out.returncode == 0:
            return out.stdout.strip() + ("-dirty" if dirty.stdout.strip() else "")
    except Exception:
        pass
    return None


def _jsonable(value):
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    return value


def write_run_log(results: RunResults, out_dir: Path, cli_overrides: dict | None = None) -> dict:
    import matplotlib
    cfg = results.cfg
    log = {
        "run_name": cfg["run"].get("name"),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "model_version": __version__,
        "git_commit": _git_commit(Path(cfg["_meta"]["root"])),
        "python": platform.python_version(),
        "packages": {"numpy": np.__version__, "pandas": pd.__version__, "matplotlib": matplotlib.__version__},
        "config_file": cfg["_meta"]["config_path"],
        "command_line_overrides": cli_overrides or {},
        "runtime_seconds": round(results.runtime_seconds, 3),
        "inputs": {k: {"path": str(p), "sha256": _sha256(p)} for k, p in results.inputs.sources.items()},
        "config": {k: v for k, v in cfg.items() if not k.startswith("_")},
        "summary": results.summary.to_dict(orient="records"),
    }
    with open(out_dir / "run_log.json", "w") as f:
        json.dump(_jsonable(log), f, indent=2, default=str)
    return log


def find_previous_run(out_dir: Path, run_name: str | None = None) -> Path | None:
    """Most recent earlier run with the same run name, or else the most recent run."""
    runs = sorted((p for p in out_dir.parent.iterdir()
                   if p.is_dir() and p != out_dir and (p / "run_log.json").exists()),
                  key=lambda p: (p / "run_log.json").stat().st_mtime)
    same = [p for p in runs if run_name and p.name.startswith(f"{run_name}_")]
    return (same or runs or [None])[-1]


def write_change_log(log: dict, out_dir: Path, previous: Path | None) -> Path:
    lines = [f"# Change log: {out_dir.name}", ""]
    if previous is None:
        lines.append("No previous run found in the output folder, so there is nothing to compare against.")
    else:
        with open(previous / "run_log.json") as f:
            prev = json.load(f)
        lines += [f"Compared with previous run **{previous.name}** ({prev.get('timestamp')}).", ""]
        lines += ["## Code", "", f"- Git commit: {prev.get('git_commit')} -> {log.get('git_commit')}", ""]

        lines += ["## Inputs", ""]
        changed_inputs = [k for k, v in log["inputs"].items()
                          if prev.get("inputs", {}).get(k, {}).get("sha256") != v["sha256"]]
        lines += [f"- **{k}** changed ({log['inputs'][k]['path']})" for k in changed_inputs] or ["- No input files changed."]
        lines.append("")

        lines += ["## Configuration", ""]
        old, new = flatten(prev.get("config", {})), flatten(_jsonable(log["config"]))
        diffs = [(k, old.get(k, "(not set)"), new.get(k, "(removed)")) for k in sorted(set(old) | set(new))
                 if json.dumps(old.get(k), default=str) != json.dumps(new.get(k), default=str)]
        if diffs:
            lines += ["| Setting | Previous | Current |", "|---|---|---|"]
            lines += [f"| `{k}` | {a} | {b} |" for k, a, b in diffs]
        else:
            lines.append("- No configuration changes.")
        lines.append("")

        lines += ["## Results (PV of ending surplus, $)", ""]
        p_df = pd.DataFrame(prev.get("summary", []))
        n_df = pd.DataFrame(log["summary"])
        if not p_df.empty:
            merged = n_df.merge(p_df, on=["sensitivity", "scenario"], how="outer", suffixes=("", "_prev"))
            merged["change"] = merged["pv_ending_surplus"] - merged["pv_ending_surplus_prev"]
            lines += ["| Sensitivity | Scenario | Previous | Current | Change |", "|---|---|---:|---:|---:|"]
            for _, r in merged.iterrows():
                fmt = lambda v: "n/a" if pd.isna(v) else f"{v:,.0f}"
                lines.append(f"| {r['sensitivity']} | {r['scenario']} | {fmt(r['pv_ending_surplus_prev'])} | "
                             f"{fmt(r['pv_ending_surplus'])} | {fmt(r['change'])} |")
    path = out_dir / "change_log.md"
    path.write_text("\n".join(lines) + "\n")
    return path


def write_outputs(results: RunResults, cli_overrides: dict | None = None, compare_to: str | Path | None = None) -> Path:
    from .memo import write_memo

    cfg = results.cfg
    out_dir = make_output_dir(cfg)
    previous = Path(compare_to) if compare_to else find_previous_run(out_dir, cfg["run"].get("name"))

    results.summary.to_csv(out_dir / "summary.csv", index=False)
    long = pd.concat([df.assign(sensitivity=s, scenario=sid) for (s, sid), df in results.details.items()], ignore_index=True)
    long.to_csv(out_dir / "results_long.csv", index=False)   # tidy format for SQL/Snowflake/BI tools

    rep = cfg.get("reporting", {})
    charts = write_charts(results, out_dir / "charts") if rep.get("charts", True) else {}
    if rep.get("excel", True):
        write_excel(results, out_dir / "results.xlsx")
    log = write_run_log(results, out_dir, cli_overrides)
    write_change_log(log, out_dir, previous)
    if rep.get("memo", True):
        write_memo(results, out_dir, charts)
    return out_dir
