"""Command line interface.

Examples (run from the project root):
  python -m cft_model run
  python -m cft_model run -c config/monthly.yaml
  python -m cft_model run --set liability.crediting.target_spread=0.0175 --name spread175
  python -m cft_model run --scenarios 1 4 --base-only
  python -m cft_model validate -c config/base.yaml
  python -m cft_model generate-data --n-policies 5000
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

from .config import ConfigError, load_config, parse_override
from .inputs import InputError, load_inputs
from .scenarios import load_scenarios

DEFAULT_CONFIG = "config/base.yaml"


def _overrides(args) -> dict:
    out = dict(parse_override(s) for s in (args.set or []))
    if getattr(args, "scenarios", None):
        out["scenarios.include"] = args.scenarios
    if getattr(args, "name", None):
        out["run.name"] = args.name
    if getattr(args, "output_dir", None):
        out["run.output_dir"] = args.output_dir
    for flag in ("excel", "charts", "memo"):
        if getattr(args, f"no_{flag}", False):
            out[f"reporting.{flag}"] = False
    return out


def cmd_run(args) -> int:
    from .reporting import write_outputs
    from .runner import run_all

    overrides = _overrides(args)
    cfg = load_config(args.config, overrides)
    sens = [{"name": "Base", "overrides": {}}] if args.base_only else None
    print(f"Running {cfg['run']['name']} ({cfg['projection']['time_step']}, {cfg['projection']['years']} years) ...")
    results = run_all(cfg, sens, progress=(lambda s, sc: print(f"  {s:<20} scenario {sc.id}: {sc.label}")) if args.verbose else None)
    out = write_outputs(results, overrides, args.compare_to)

    base = results.summary[results.summary["sensitivity"] == results.summary["sensitivity"].iloc[0]]
    print(f"\nFinished {len(results.summary)} projections in {results.runtime_seconds:.1f}s\n")
    print(f"{'Scenario':<45}{'PV ending surplus':>20}{'Min PV surplus':>18}")
    for r in base.itertuples():
        print(f"{(r.scenario + ': ' + r.scenario_label)[:44]:<45}{r.pv_ending_surplus:>20,.0f}{r.min_pv_surplus:>18,.0f}")
    print(f"\nOutputs written to: {out}")
    return 0


def cmd_validate(args) -> int:
    cfg = load_config(args.config, _overrides(args))
    inp = load_inputs(cfg)
    p, a = inp.policies, inp.assets
    print("Config OK:", cfg["_meta"]["config_path"])
    print(f"Policies: {p.n:,} records, {p.count.sum():,.0f} policies, account value {p.total_account_value:,.0f}")
    print(f"  attained ages {p.attained_age.min():.0f}-{p.attained_age.max():.0f}, "
          f"guarantees {p.guaranteed_rate.min():.2%}-{p.guaranteed_rate.max():.2%}")
    print(f"Assets: {len(a.par)} bonds, book {a.total_book:,.0f}, coupons {a.coupon.min():.2%}-{a.coupon.max():.2%}, "
          f"maturities {a.maturity.min():.1f}-{a.maturity.max():.1f} yrs")
    print(f"Yield curve: {len(inp.curve.tenors)} points, {inp.curve.rates.min():.2%}-{inp.curve.rates.max():.2%}")
    print(f"Mortality: q65={inp.mortality[65]:.5f}, q85={inp.mortality[85]:.5f}")
    for s in load_scenarios(cfg):
        print(f"Scenario {s.id}: {s.label}  (shift at yr 5: {float(s.shift(5)):+.2%}, yr 10: {float(s.shift(10)):+.2%})")
    return 0


def cmd_generate(args) -> int:
    from .synthetic import write_all
    counts = write_all(args.data_dir, args.n_policies, args.seed)
    for name, n in counts.items():
        print(f"  wrote {Path(args.data_dir) / name} ({n} rows)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cft_model", description="Illustrative annuity cash flow testing model")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("-c", "--config", default=DEFAULT_CONFIG, help=f"YAML config (default {DEFAULT_CONFIG})")
        p.add_argument("--set", action="append", metavar="KEY=VALUE",
                       help="override any config value, e.g. --set projection.time_step=monthly (repeatable)")
        p.add_argument("--scenarios", nargs="+", help="only run these scenario ids, e.g. --scenarios 1 4")

    p_run = sub.add_parser("run", help="run the projections and write outputs")
    common(p_run)
    p_run.add_argument("--name", help="run name (used in the output folder name)")
    p_run.add_argument("--output-dir", help="where to write outputs (default: output/)")
    p_run.add_argument("--base-only", action="store_true", help="skip sensitivities")
    p_run.add_argument("--compare-to", help="earlier output folder to compare against in change_log.md")
    p_run.add_argument("--no-excel", action="store_true")
    p_run.add_argument("--no-charts", action="store_true")
    p_run.add_argument("--no-memo", action="store_true")
    p_run.add_argument("-v", "--verbose", action="store_true", help="print each projection as it runs")
    p_run.set_defaults(func=cmd_run)

    p_val = sub.add_parser("validate", help="check the config and inputs without running")
    common(p_val)
    p_val.set_defaults(func=cmd_validate)

    p_gen = sub.add_parser("generate-data", help="(re)create the synthetic input files")
    p_gen.add_argument("--data-dir", default="data")
    p_gen.add_argument("--n-policies", type=int, default=1000)
    p_gen.add_argument("--seed", type=int, default=2025)
    p_gen.set_defaults(func=cmd_generate)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, InputError, ValueError, FileNotFoundError) as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
