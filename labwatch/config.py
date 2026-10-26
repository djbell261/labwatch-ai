"""Loads the building layout and the isolation runbook."""
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def load_layout(path: Path | None = None) -> dict:
    with open(path or CONFIG_DIR / "layout.yaml") as f:
        return yaml.safe_load(f)


def load_runbook(path: Path | None = None) -> dict:
    with open(path or CONFIG_DIR / "runbook.yaml") as f:
        return yaml.safe_load(f)


def requirement_text(layout: dict, room_type: str) -> str | None:
    req = layout["requirements"].get(room_type)
    if not req:
        return None
    if "max_dp_pa" in req:
        return f"pressure must stay at or below {req['max_dp_pa']} Pa"
    return f"pressure must stay at or above +{req['min_dp_pa']} Pa"


def in_range(layout: dict, room_type: str, dp: float) -> bool:
    req = layout["requirements"].get(room_type)
    if not req:
        return True
    if "max_dp_pa" in req:
        return dp <= req["max_dp_pa"]
    return dp >= req["min_dp_pa"]
