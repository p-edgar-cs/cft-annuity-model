"""Drafts an illustrative Cash Flow Testing memo (Markdown) from a run's results.

The memo is a DRAFT for review: numbers, tables and the conclusion are filled
in automatically; the actuary still writes the judgement sections.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .config import get_dotted
from .reporting import adequacy_metric, base_name, matrix
from .runner import RunResults


def _m(v) -> str:
    """Format dollars in millions."""
    return f"{v / 1e6:,.2f}" if v >= 0 else f"({abs(v) / 1e6:,.2f})"


METRIC_NAMES = {
    "pv_ending_surplus": "present value of ending surplus",
    "min_pv_surplus": "minimum present value of surplus",
    "ending_surplus": "ending surplus",
}


def _signed_m(v) -> str:
    return f"-${abs(v) / 1e6:,.2f}m" if v < 0 else f"${v / 1e6:,.2f}m"


def _p(v) -> str:
    return "n/a" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v * 100:.2f}%"


def _table(df, money_cols) -> str:
    cols = list(df.columns)
    out = ["| " + " | ".join(str(c) for c in cols) + " |",
           "|" + "|".join("---:" if c in money_cols else "---" for c in cols) + "|"]
    for _, r in df.iterrows():
        out.append("| " + " | ".join(_m(r[c]) if c in money_cols else str(r[c]) for c in cols) + " |")
    return "\n".join(out)


def write_memo(results: RunResults, out_dir: Path, charts: dict) -> Path:
    cfg = results.cfg
    run, L, A = cfg["run"], cfg["liability"], cfg["assets"]
    base = base_name(results)
    metric = adequacy_metric(cfg)
    metric_name = METRIC_NAMES.get(metric, metric.replace("_", " "))
    s = results.summary
    b = s[s["sensitivity"] == base].reset_index(drop=True)
    inp = results.inputs
    pol = inp.policies

    failing = b[b[metric] < 0]
    worst = b.loc[b[metric].idxmin()]
    sens_fail = s[(s["sensitivity"] != base) & (s[metric] < 0)]

    if failing.empty:
        conclusion = (f"Under the {base.lower()} assumptions the {metric_name} is non-negative in all "
                      f"{len(b)} scenarios tested. On this illustrative basis the assets backing the block "
                      f"appear adequate to support the reserves.")
    else:
        names = ", ".join(f"{r.scenario} ({r.scenario_label})" for r in failing.itertuples())
        conclusion = (f"Under the {base.lower()} assumptions the {metric_name} is negative in "
                      f"{len(failing)} of {len(b)} scenarios: {names}. The largest deficiency is "
                      f"${_m(-worst[metric])} million in scenario {worst['scenario']}. On this illustrative "
                      f"basis, the reserves would need to be strengthened (for example by an additional "
                      f"reserve of up to roughly that amount, subject to the company's adequacy standard), "
                      f"or the investment / crediting strategy revisited.")

    up = b[b["scenario"].isin(["2", "3", "4"])]
    down = b[b["scenario"].isin(["5", "6", "7"])]
    risk_lines = []
    if not up.empty and up[metric].min() < (down[metric].min() if not down.empty else np.inf):
        risk_lines.append(
            "- **Rising rates are the main risk.** Higher market rates widen the gap between competitor "
            f"rates and the credited rate, so dynamic lapses rise (peak lapse rate {_p(up['peak_lapse_rate'].max())}). "
            "Paying those surrenders forces bond sales at a loss "
            f"(realised losses of up to ${_m(-up['total_realised_gains'].min())} million).")
    level = b.loc[b["scenario"] == "1", metric]
    if not down.empty and not level.empty:
        if down[metric].min() < level.iloc[0]:
            risk_lines.append(
                "- **Falling rates compress the spread.** New money is reinvested at lower yields while credited "
                "rates cannot fall below the minimum guarantees "
                f"({_p(pol.guaranteed_rate.min())} to {_p(pol.guaranteed_rate.max())}); falling-rate results are "
                f"below the level scenario (worst {_signed_m(down[metric].min())}).")
        else:
            risk_lines.append(
                "- **Falling rates are not a material risk on this basis.** Every falling-rate scenario is above "
                "the level scenario. A likely reason is that the block runs off faster than new money is "
                "reinvested, so the portfolio yield stays above the minimum guarantees "
                f"({_p(pol.guaranteed_rate.min())} to {_p(pol.guaranteed_rate.max())}). "
                "*Reviewer: confirm with the earned vs credited rate detail.*")
    if not sens_fail.empty:
        worst_sens = sens_fail.groupby("sensitivity")[metric].min().sort_values()
        risk_lines.append(f"- **Assumption sensitivity.** The {metric_name} is negative in "
                          f"{len(sens_fail)} sensitivity runs. Worst result by sensitivity: "
                          + ", ".join(f"{k} {_signed_m(v)}" for k, v in worst_sens.items()) + ".")

    res_table = b[["scenario", "scenario_label", "initial_surplus", "ending_surplus", "pv_ending_surplus", "min_pv_surplus"]].rename(
        columns={"scenario": "Scenario", "scenario_label": "Description", "initial_surplus": "Initial surplus",
                 "ending_surplus": "Ending surplus", "pv_ending_surplus": "PV ending surplus", "min_pv_surplus": "Min PV surplus"})
    sens = matrix(results, metric).rename(columns={"scenario": "Scenario", "scenario_label": "Description"})
    sens_cols = [c for c in sens.columns if c not in ("Scenario", "Description")]
    money_cols = {"Initial surplus", "Ending surplus", "PV ending surplus", "Min PV surplus", *sens_cols}

    dyn = L["lapse"].get("dynamic", {})
    ratio = get_dotted(cfg, "inputs.assets.scale_to_reserve_ratio")
    chart_md = "\n\n".join(f"![{k}]({v})" for k, v in charts.items() if k != "adequacy")

    text = f"""# Cash Flow Testing Memorandum - DRAFT (illustrative)

**Company:** {run.get('company', '')}
**Block:** {run.get('block', '')}
**Valuation date:** {run.get('valuation_date', '')}
**Run:** `{out_dir.name}` (see `run_log.json` for the exact inputs and settings)

> **Illustration only.** This memo was produced from synthetic data by a simplified model built for
> learning purposes. It is not an actuarial opinion and does not describe any real company.

## 1. Purpose and scope

This memo documents asset adequacy analysis (cash flow testing) of the {run.get('block', 'block')}.
Asset and liability cash flows were projected for {cfg['projection']['years']} years on a
{'a monthly' if cfg['projection']['time_step'] == 'monthly' else 'an annual'} basis under the {len(b)} interest rate scenarios listed below and
{len(s['sensitivity'].unique())} assumption sets.

## 2. Summary and conclusion

{conclusion}

![adequacy]({charts.get('adequacy', '')})

## 3. Data

| Item | Value |
|---|---:|
| Policy records | {pol.n:,} |
| Policies in force | {pol.count.sum():,.0f} |
| Account value (= starting reserve) | ${_m(pol.total_account_value)} million |
| AV-weighted attained age | {np.average(pol.attained_age, weights=pol.account_value * pol.count):.1f} |
| Minimum guaranteed rates | {_p(pol.guaranteed_rate.min())} to {_p(pol.guaranteed_rate.max())} |
| Bonds in starting portfolio | {len(inp.assets.par)} |
| Starting assets (book) | {'scaled to ' + format(ratio, '.2f') + ' x reserve' if ratio else 'as provided'} |

Input files: {', '.join(f'`{p.name}`' for p in inp.sources.values())}.
*Reviewer: add data reconciliation to the valuation system / financial statements here.*

## 4. Key assumptions

| Assumption | Basis |
|---|---|
| Reserve | Account value (simplified; CARVM not modelled) |
| Crediting strategy | max(guaranteed rate, portfolio earned rate - {_p(L['crediting']['target_spread'])}) |
| Base lapses | {', '.join(_p(x) for x in L['lapse']['base_by_policy_year'])} by policy year (last value thereafter) x {L['lapse'].get('base_multiplier', 1.0)} |
| Dynamic lapses | {'+' + format(dyn.get('slope', 0) / 100, '.0%') + ' per 1% that the competitor rate (Treasury ' + str(dyn.get('competitor_tenor')) + 'y ' + format(dyn.get('competitor_spread', 0) * 1e4, '+.0f') + 'bp) exceeds the credited rate by more than ' + _p(dyn.get('threshold', 0)) + ', capped at ' + str(dyn.get('max_multiplier')) + 'x' if dyn.get('enabled') else 'not used'} |
| Surrender charges | {', '.join(_p(x) for x in L['surrender_charges'])} by policy year |
| Mortality | Illustrative table x {L.get('mortality_multiplier', 1.0)}; remaining AV paid at age {L.get('maturity_age')} |
| Expenses | ${L['expenses']['per_policy']:,.0f} per policy (inflating {_p(L['expenses'].get('inflation', 0))}) + {_p(L['expenses'].get('pct_of_av', 0))} of AV |
| Reinvestment | {A['reinvestment']['term_years']}-year bonds at Treasury + {_p(A['reinvestment']['spread'])} |
| Disinvestment | {'Pro-rata sale of bonds at market value' if A['disinvestment'] == 'sell' else 'Borrowing at the short rate + ' + _p(A.get('borrow_spread', 0))} |
| Rate floor | {_p(cfg['scenarios'].get('rate_floor'))} |
| Interest Maintenance Reserve | {'Realised gains/losses on sales deferred and released over the remaining life of the bonds sold' if A.get('imr', {}).get('enabled') else 'Not modelled (gains/losses go straight to surplus)'} |
| Taxes | Not modelled |

*Reviewer: add the source and support for each assumption (experience studies, pricing, investment strategy).*

## 5. Methodology

Liabilities and assets are projected together each period. Account values grow at the credited rate;
deaths, lapses and maturities are paid from the asset portfolio; positive net cash flow is reinvested
and negative net cash flow is covered by {'selling bonds at market value' if A['disinvestment'] == 'sell' else 'borrowing'}.
Statutory surplus is book value of assets less the reserve{' and the IMR' if A.get('imr', {}).get('enabled') else ''}. Surplus is discounted at the scenario's
{get_dotted(cfg, 'reporting.discount_tenor', 1)}-year rate. The adequacy measure is the **{metric_name}**.

Interest rate scenarios are parallel shifts of the starting Treasury curve
({'the seven NAIC "New York 7" scenarios' if cfg['scenarios']['source'] == 'ny7' else 'company-defined scenarios from ' + str(cfg['scenarios'].get('csv_path'))}).

## 6. Results

### Base assumptions ($ millions)

{_table(res_table, money_cols)}

### Sensitivities: {metric_name} ($ millions)

{_table(sens, money_cols)}

### Key risks

{chr(10).join(risk_lines) if risk_lines else '- No material risks identified on this basis.'}

### Charts

{chart_md}

## 7. Limitations

- Synthetic policy and asset data; illustrative mortality table and yield curve.
- Reserve is approximated by account value; no CARVM, AG 33 or VM-22 calculation.
- Non-callable fixed-rate bonds only; no defaults, calls, prepayments or derivatives.
- Deterministic parallel shifts only; no stochastic scenarios or curve twists.
- No federal income tax or required capital; IMR is a simplified straight-line release.

## 8. Reviewer sign-off

| Role | Name | Date |
|---|---|---|
| Prepared by | | |
| Reviewed by | | |
"""
    path = out_dir / "cft_memo.md"
    path.write_text(text)
    return path
