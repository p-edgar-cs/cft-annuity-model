import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cft_model.config import load_config  # noqa: E402
from cft_model.inputs import load_inputs  # noqa: E402


@pytest.fixture
def cfg():
    return load_config(ROOT / "config" / "base.yaml")


@pytest.fixture
def inputs(cfg):
    return load_inputs(cfg)
