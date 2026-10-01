from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "search.yaml"
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"
RESUME_DIR = ROOT / "resume"
OUTPUT_DIR = ROOT / "output"

RESUME_DEFAULTS = {"base": None, "max_pages": 1, "model": "sonnet", "cover_letter": False}


def load_config() -> dict:
    from .discover import load_discovered, merged_boards

    cfg = yaml.safe_load(CONFIG_PATH.read_text())
    cfg["resume"] = {**RESUME_DEFAULTS, **(cfg.get("resume") or {})}
    cfg.setdefault("candidate", {})
    cfg = merged_boards(cfg)
    # Display names for board tokens (Ashby/Lever APIs don't return the company name).
    names = cfg.setdefault("company_names", {})
    for entries in (load_discovered().get("sources") or {}).values():
        for e in entries:
            if e.get("token"):
                names.setdefault(e["token"], e["company"])
    _normalize_tracks(cfg)
    return cfg


def _normalize_tracks(cfg: dict) -> None:
    """Fill in `tracks` / `active_tracks`. A config from before tracks existed (top-level
    title_include, seniority_stretch, workday_queries) becomes a single `swe` track."""
    if "tracks" not in cfg:
        cfg["tracks"] = {"swe": {"label": "Software engineering", "target": "Senior Software Engineer",
                                 "title_include": cfg.get("title_include", []),
                                 "seniority_stretch": cfg.get("seniority_stretch", []),
                                 "workday_queries": cfg.get("workday_queries", [])}}
    for name, t in cfg["tracks"].items():
        t.setdefault("label", name)
        t.setdefault("target", "Senior Software Engineer")
        for k in ("title_include", "title_exclude", "seniority_stretch", "workday_queries"):
            t[k] = t.get(k) or []
    cfg["active_tracks"] = [t for t in cfg.get("active_tracks") or list(cfg["tracks"]) if t in cfg["tracks"]]
    if not cfg["active_tracks"]:
        raise SystemExit("config/search.yaml: active_tracks names no track defined under `tracks`.")
    # Every active track's search terms, de-duplicated, for boards searched server-side.
    cfg["workday_queries"] = list(dict.fromkeys(q for t in cfg["active_tracks"]
                                                for q in cfg["tracks"][t]["workday_queries"]))


def candidate_note(cfg: dict, profile) -> str:
    """Where the candidate is and where they'll work, for Claude's prompts."""
    c = cfg.get("candidate") or {}
    loc = c.get("location") or profile.identity.get("location") or "location not stated"
    return f"Based in {loc}. " + (c.get("relocation") or "")


def track_target(cfg: dict, track: str | None, family: str | None = None) -> str:
    """The role you're judged against: the family's target if the track sets one, else the track's."""
    t = cfg.get("tracks", {}).get(track or "") or {}
    return (t.get("family_targets") or {}).get(family or "") or t.get("target") or "Senior Software Engineer"


def track_criteria(cfg: dict, track: str | None) -> dict:
    """apply_criteria with the track's own overrides (e.g. a different pay floor or match bar)."""
    over = (cfg.get("tracks", {}).get(track or "") or {}).get("apply_criteria") or {}
    return {**cfg["apply_criteria"], **over}
