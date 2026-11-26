import json

import pandas as pd
import pytest

from cft_model.cli import main
from cft_model.config import ConfigError, load_config, parse_override
from cft_model.inputs import InputError, load_policies
from conftest import ROOT


def test_extends_and_overrides():
    cfg = load_config(ROOT / "config" / "monthly.yaml", {"liability.lapse.base_multiplier": 1.5})
    assert cfg["projection"]["time_step"] == "monthly"          # from monthly.yaml
    assert cfg["liability"]["crediting"]["target_spread"] == 0.02  # inherited from base.yaml
    assert cfg["liability"]["lapse"]["base_multiplier"] == 1.5     # override


def test_parse_override_types():
    assert parse_override("a.b=1.5") == ("a.b", 1.5)
    assert parse_override("a=true") == ("a", True)
    assert parse_override("a=[1, 4]") == ("a", [1, 4])
    with pytest.raises(ConfigError):
        parse_override("no_equals_sign")


def test_invalid_config_is_reported():
    with pytest.raises(ConfigError, match="time_step"):
        load_config(ROOT / "config" / "base.yaml", {"projection.time_step": "weekly"})


def test_column_mapping_and_percent_units(tmp_path, cfg):
    """A work extract with different headers and rates in percent loads via config only."""
    src = pd.read_csv(ROOT / "data" / "policies.csv")
    extract = src.rename(columns={"policy_id": "POL_NO", "account_value": "ACCT_VAL",
                                  "guaranteed_min_rate": "GMIR_PCT", "duration_years": "DUR"})
    extract["GMIR_PCT"] *= 100
    f = tmp_path / "extract.xlsx"
    extract.to_excel(f, index=False)
    cfg["inputs"]["policies"] = {"path": str(f), "columns": {
        "policy_id": "POL_NO", "issue_age": "issue_age", "duration": "DUR",
        "account_value": "ACCT_VAL", "guaranteed_rate": "GMIR_PCT"}}
    cfg["inputs"]["rate_units"] = "percent"
    pol, _ = load_policies(cfg)
    assert pol.n == len(src)
    assert pol.guaranteed_rate.max() == pytest.approx(src["guaranteed_min_rate"].max())
    assert (pol.count == 1).all()                                   # optional column defaulted


def test_missing_column_message(tmp_path, cfg):
    f = tmp_path / "bad.csv"
    f.write_text("policy_id,issue_age\nP1,60\n")
    cfg["inputs"]["policies"]["path"] = str(f)
    with pytest.raises(InputError, match="not found"):
        load_policies(cfg)


def test_cli_run_writes_outputs(tmp_path):
    code = main(["run", "-c", str(ROOT / "config" / "base.yaml"), "--base-only",
                 "--scenarios", "1", "4", "--output-dir", str(tmp_path), "--name", "t"])
    assert code == 0
    (run_dir,) = list(tmp_path.iterdir())
    for name in ["results.xlsx", "summary.csv", "results_long.csv", "run_log.json", "change_log.md", "cft_memo.md"]:
        assert (run_dir / name).exists(), name
    log = json.loads((run_dir / "run_log.json").read_text())
    assert len(log["summary"]) == 2 and "sha256" in log["inputs"]["policies"]

    # second run with a change -> change log lists it
    main(["run", "-c", str(ROOT / "config" / "base.yaml"), "--base-only", "--scenarios", "1", "4",
          "--output-dir", str(tmp_path), "--name", "t", "--set", "liability.crediting.target_spread=0.018"])
    newest = max(tmp_path.iterdir(), key=lambda p: p.stat().st_mtime)
    assert "liability.crediting.target_spread" in (newest / "change_log.md").read_text()


def test_cli_reports_errors_cleanly(capsys):
    assert main(["validate", "-c", str(ROOT / "config" / "base.yaml"), "--set", "assets.disinvestment=hold"]) == 1
    assert "disinvestment" in capsys.readouterr().err
