from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "search.yaml"
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"


def load_config() -> dict:
    from .discover import load_discovered, merged_boards

    cfg = yaml.safe_load(CONFIG_PATH.read_text())
    cfg["rta_path"] = (ROOT / cfg["rta_path"]).resolve()
    cfg = merged_boards(cfg)
    # Display names for board tokens (Ashby/Lever APIs don't return the company name).
    names = cfg.setdefault("company_names", {})
    for entries in (load_discovered().get("sources") or {}).values():
        for e in entries:
            if e.get("token"):
                names.setdefault(e["token"], e["company"])
    return cfg
