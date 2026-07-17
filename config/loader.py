"""Load config/experiment.yaml once; import from here everywhere."""

from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).parent / "experiment.yaml"


def load() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


CONFIG = load()
SLICE = CONFIG["stage1_slice"]
