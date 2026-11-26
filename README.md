# Annuity Cash Flow Testing Model

An illustrative **asset adequacy analysis (cash flow testing)** model for a block of single premium
deferred annuities (SPDAs), written in Python.

It projects liability and asset cash flows together under the seven NAIC "New York 7" interest rate
scenarios (or your own scenarios), runs assumption sensitivities, and produces:

- an **Excel workbook** of results by scenario and sensitivity, with year-by-year detail;
- **charts** of surplus, lapse rates and credited rates by scenario;
- a **draft CFT memo** (Markdown) with the results, assumptions, key risks and limitations filled in;
- a **run log** (inputs with file hashes, the full configuration, git commit) and a **change log**
  comparing each run to the previous one.

> **Synthetic data, illustrative model.** All policy and asset data is made up, and the mortality
> table and yield curve are illustrative. This is a learning and portfolio project, not a production
> valuation model or an actuarial opinion.

---

## Results with the default settings

| NY7 scenario | PV of ending surplus ($m) |
|---|---:|
| 1. Level | 19.16 |
| 2. Gradual increase | 1.99 |
| 3. Up then down | (2.62) |
| 4. Pop-up +3% | (2.12) |
| 5. Gradual decrease | 28.84 |
| 6. Down then up | 27.78 |
| 7. Pop-down −3% | 33.08 |

Rising rates are the main risk for this block. Credited rates lag market rates, so dynamic lapses
rise, and paying those surrenders forces bond sales at a loss. Starting with assets equal to reserves,
the block is deficient in scenarios 3 and 4.

---

## 1. Setup (macOS)

Requires Python 3.10 or later. Check with `python3 --version`. If needed, install it with
`brew install python` or from python.org.

```bash
cd ~/Projects                      # wherever you keep code
unzip ~/Downloads/cft-annuity-model.zip
cd cft-annuity-model

python3 -m venv .venv              # private Python environment for this project
source .venv/bin/activate          # your prompt now starts with (.venv)
pip install -r requirements.txt
```

Or run `make setup` to do the same thing.

## 2. Run it

```bash
source .venv/bin/activate          # once per terminal session

python -m cft_model validate       # check inputs and config, print a summary
python -m cft_model run            # 7 scenarios x 5 sensitivities, annual
python -m cft_model run -c config/monthly.yaml      # same model, monthly time step
python -m pytest -q                # run the tests
```

Each run creates a new folder such as `output/base_20261126_093015/` containing:

| File | What it is |
|---|---|
| `results.xlsx` | Summary, scenario x sensitivity tables, one detail sheet per scenario, assumptions, input summary |
| `cft_memo.md` | Draft CFT memo. Open in VS Code and press `Cmd+Shift+V` to preview it with charts |
| `charts/*.png` | Surplus, PV of surplus, lapse and credited rates by scenario; adequacy bar chart |
| `summary.csv` | One row per scenario/sensitivity |
| `results_long.csv` | Every period of every run in one tidy table, ready to load into SQL or Snowflake |
| `run_log.json` | Timestamp, git commit, input file paths and SHA-256 hashes, full config, results |
| `change_log.md` | What changed since the previous run with the same name: config, inputs, results |

Open the folder in Finder with `open output/`.

### Useful command-line options

```bash
# change any setting without editing files
python -m cft_model run --set liability.crediting.target_spread=0.0175 --name spread175

# several overrides, only some scenarios, skip sensitivities
python -m cft_model run --scenarios 1 4 --base-only \
    --set projection.time_step=monthly --set assets.disinvestment=borrow

# compare against a specific earlier run in the change log
python -m cft_model run --compare-to output/base_20261126_093015

# print each projection as it runs
python -m cft_model run -v

# regenerate the synthetic data (e.g. a bigger block)
python -m cft_model generate-data --n-policies 5000 --seed 1
```

`python -m cft_model run --help` lists every option.

---

## 3. Open it in VS Code

1. Install [VS Code](https://code.visualstudio.com/) and the **Python** extension (by Microsoft).
2. Enable the `code` command: open the Command Palette (`Cmd+Shift+P`), then run
   **Shell Command: Install 'code' command in PATH**.
3. From the project folder, run `code .`
4. VS Code should detect `.venv` automatically. If it doesn't, open the Command Palette, run
   **Python: Select Interpreter**, and choose `./.venv/bin/python`.
5. **Run and debug:** open the Run and Debug panel (`Cmd+Shift+D`), pick **CFT: run base (annual)**
   and press F5. You can set breakpoints in `cft_model/projection.py` to step through a projection
   period by period.
6. **Tests:** open the Testing panel (the beaker icon). The tests are already configured.
7. **Memo:** open `output/<run>/cft_memo.md` and press `Cmd+Shift+V` to preview it.

The integrated terminal (`` Ctrl+` ``) activates `.venv` automatically, so the commands above work there.

---

## 4. Push it to GitHub

**One-time setup**

```bash
git --version                                  # install the Xcode tools if prompted
git config --global user.name  "Your Name"
git config --global user.email "you@example.com"
```

**Option A: GitHub CLI (easiest)**

```bash
brew install gh
gh auth login                                  # choose GitHub.com, HTTPS, log in with browser

cd cft-annuity-model
git init -b main
git add .
git commit -m "Initial commit: annuity cash flow testing model"
gh repo create cft-annuity-model --public --source=. --push
```

**Option B: GitHub website**

1. On github.com, click **New repository** and name it `cft-annuity-model`. Leave
   "Add a README" unchecked.
2. Then run:

```bash
git init -b main
git add .
git commit -m "Initial commit: annuity cash flow testing model"
git remote add origin https://github.com/<your-username>/cft-annuity-model.git
git push -u origin main
```

When asked for a password, use a **personal access token**: GitHub, then Settings, Developer
settings, Personal access tokens. Your account password won't work.

**After that**, each time you make changes:

```bash
git add .
git commit -m "Describe what changed"
git push
```

`output/` is in `.gitignore` because results are regenerated by running the model. If you want a
sample of the outputs on GitHub, copy one run's memo and charts into a `docs/` folder and commit that
folder.

---

## 5. How the model works

Each period (one year or one month), for every policy record and scenario:

1. **Rates.** The starting Treasury curve is shifted in parallel by the scenario, then floored
   (`scenarios.rate_floor`).
2. **Crediting.** Credited rate = max(minimum guarantee, portfolio earned rate − target spread).
3. **Expenses.** A per-policy amount (inflating) plus a percentage of account value.
4. **Account value** grows at the credited rate.
5. **Decrements**, in this order:
   - Deaths: mortality table × multiplier.
   - Lapses: base rate by policy year × multiplier × a dynamic factor. The dynamic factor rises
     when the competitor rate (a Treasury tenor plus a spread) exceeds the credited rate by more
     than a threshold.
   - Maturities: remaining account value is paid out at `maturity_age`.
   Surrenders are paid at account value minus the surrender charge.
6. **Assets.** Bonds pay coupons and mature, and cash earns the short rate.
7. **IMR.** Realized gains and losses on sales are deferred through the Interest Maintenance
   Reserve and released over the remaining life of the bonds sold.
8. **Net cash flow.** If positive, it buys new bonds at par (Treasury + spread). If negative, it is
   covered by selling a slice of every bond at market value, or by borrowing (`assets.disinvestment`).
9. **Surplus** = book value of assets − reserve (account value) − IMR. Surplus is discounted at the
   scenario's 1-year rate.

**Adequacy measure:** the PV of ending surplus by default. Set `reporting.adequacy_metric` to change
it to `min_pv_surplus` (the greatest present value of accumulated deficiency) or `ending_surplus`.

The tests in `tests/` check the starting balance sheet, the asset and reserve roll-forwards, the
guarantee floor, dynamic lapse behavior, IMR, borrow vs. sell, annual vs. monthly consistency, and
the command line.

### Simplifications

- Reserve = account value (no CARVM / AG 33 / VM-22).
- Non-callable fixed-rate bonds only: no defaults, calls, MBS prepayments or derivatives.
- Book value with no amortization of premium or discount.
- Deterministic parallel shifts only: no stochastic scenarios or curve twists.
- No taxes or required capital. The IMR is a simplified straight-line release.

---

## 6. Using it with other data or for other analyses

Everything that changes from one use to another lives in YAML config files, not in the code.

**A different policy or asset extract.** Point `inputs.policies.path` at the file (`.csv`, `.xlsx`
or `.parquet`). Then map your column names under `columns:`:

```yaml
inputs:
  rate_units: percent          # if your extract has 3.0 rather than 0.03
  policies:
    path: data/private/spda_inforce_2026Q3.xlsx
    sheet: Inforce
    columns:
      policy_id: POL_NO
      issue_age: ISS_AGE
      duration: DUR_YRS
      account_value: ACCT_VAL
      guaranteed_rate: GMIR
      policy_count: NUM_POL     # model points: one row representing many policies
```

`python -m cft_model validate` catches missing columns, blanks, negative values, and rates that look
like percents, with a clear message. Keep real data in `data/private/`, which git ignores.

**Real assets.** Set `inputs.assets.scale_to_reserve_ratio: null` so the portfolio is used exactly
as provided. If you have a `book_value` column, map it too.

**Your own curve, mortality table or scenarios**
- Yield curve: two columns, tenor and rate, for example the Treasury par curve from treasury.gov.
- Mortality: `age, qx`, for example the SOA 2012 IAM basic table from mort.soa.org. Download it,
  save it as CSV, and update `inputs.mortality.path`.
- Scenarios: set `scenarios.source: csv` and supply `scenario, time_years, shift` rows. See
  `config/custom_scenarios.yaml` and `data/scenarios_custom_example.csv`. This also works for
  converting company or regulator scenario sets into parallel shifts.

**Variations without copying files.** Create a small YAML file that starts with `extends: base.yaml`
and lists only what's different, like `config/monthly.yaml`. Useful for prior-quarter vs.
current-quarter runs, or for a new product's assumptions.

**Sensitivities.** Add entries under `sensitivities:`. Any setting can be overridden:

```yaml
  - name: Reinvest at +80bp
    overrides:
      assets.reinvestment.spread: 0.008
```

**Feeding other tools.** `results_long.csv` has one row per period, scenario and sensitivity, ready
for Excel pivot tables, SQL/Snowflake, or BI dashboards. `summary.csv` is the one-row-per-run version.

**Using it as a library** (for example in a Jupyter notebook):

```python
from cft_model.config import load_config
from cft_model.runner import run_all

cfg = load_config("config/base.yaml", {"liability.lapse.base_multiplier": 1.2})
results = run_all(cfg)
results.summary                       # DataFrame of results
results.details[("Base", "4")]        # period-by-period projection for scenario 4
```

---

## Project structure

```
cft-annuity-model/
├── config/
│   ├── base.yaml              # all assumptions and settings (commented)
│   ├── monthly.yaml           # extends base: monthly time step
│   └── custom_scenarios.yaml  # extends base: scenarios from CSV
├── data/                      # synthetic inputs (regenerate: python -m cft_model generate-data)
├── cft_model/
│   ├── config.py              # YAML loading, inheritance, overrides, validation
│   ├── inputs.py              # file loading with column mapping and checks
│   ├── curve.py               # yield curve interpolation, shifts, discounting
│   ├── scenarios.py           # NY7 and CSV scenarios
│   ├── assets.py              # bond portfolio: coupons, maturities, pricing, buy/sell
│   ├── projection.py          # the period-by-period projection engine
│   ├── runner.py              # runs scenarios x sensitivities, builds the summary
│   ├── reporting.py           # Excel, charts, CSVs, run log, change log
│   ├── memo.py                # draft CFT memo
│   ├── synthetic.py           # synthetic data generator
│   └── cli.py                 # command line interface
├── tests/                     # pytest suite
├── Makefile                   # make setup / run / test
└── .vscode/                   # interpreter, test and run/debug settings
```
