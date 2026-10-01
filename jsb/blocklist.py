"""Companies that must never be tailored for or applied to (config/blocked_companies.yaml)."""
from __future__ import annotations

import re
from datetime import date

import yaml

from .config import BLOCKLIST_PATH

PATH = BLOCKLIST_PATH
REASONS = ("manual-override", "no-sponsorship-h1b", "no-perm-support", "application-in-progress", "other")
_SUFFIX = re.compile(r"\b(inc|llc|ltd|corp|corporation|co|company|technologies|labs|ai|hq)\b\.?", re.I)


def normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _SUFFIX.sub("", name.lower())) or re.sub(r"[^a-z0-9]", "", name.lower())


def _tokens(name: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _SUFFIX.sub(" ", name.lower()))


_LEGAL = re.compile(r"\b(inc|llc|ltd|corp|corporation|co|company)\b\.?", re.I)


def _same(company: str, blocked: str) -> bool:
    full = lambda x: re.sub(r"[^a-z0-9]", "", _LEGAL.sub("", x.lower()))
    if full(company) == full(blocked) or normalize(company) == normalize(blocked):
        return True
    # whole-word prefix: "Chime" covers "Chime Financial, Inc" but "Meta" doesn't cover "Metabase"
    tc, tb = _tokens(company), _tokens(blocked)
    return bool(tb) and tc[:len(tb)] == tb


def load() -> list[dict]:
    if not PATH.exists():
        return []
    return (yaml.safe_load(PATH.read_text()) or {}).get("companies") or []


def _save(entries: list[dict]) -> None:
    text = PATH.read_text() if PATH.exists() else ""
    header = text.split("companies:", 1)[0] if "companies:" in text else ""
    body = yaml.safe_dump({"companies": entries}, sort_keys=False, allow_unicode=True) if entries else "companies: []\n"
    PATH.write_text(header + body)


def match(company: str, entries: list[dict] | None = None) -> dict | None:
    """Return the blocklist entry covering this company name, if any."""
    for e in entries if entries is not None else load():
        if any(_same(company, name) for name in [e["company"], *(e.get("aliases") or [])]):
            return e
    return None


SHORTHAND = {"manual": "manual-override", "override": "manual-override", "h1b": "no-sponsorship-h1b",
             "no-h1b": "no-sponsorship-h1b", "no-sponsorship": "no-sponsorship-h1b", "sponsorship": "no-sponsorship-h1b",
             "perm": "no-perm-support", "no-perm": "no-perm-support",
             "in-progress": "application-in-progress", "in-process": "application-in-progress",
             "applied": "application-in-progress", "interviewing": "application-in-progress"}


def resolve_reason(reason: str) -> str:
    r = "-".join(reason.strip().lower().split())   # "Application in progress" -> "application-in-progress"
    if r in REASONS:
        return r
    if r in SHORTHAND:
        return SHORTHAND[r]
    hits = [x for x in REASONS if x.startswith(r)]
    if len(hits) == 1:
        return hits[0]
    raise ValueError(f"unknown reason '{reason}'. Use one of: {', '.join(REASONS)} "
                     f"(short forms: no-perm, h1b, manual, in-progress)")


def add(company: str, reason: str, note: str = "", aliases: list[str] | None = None) -> tuple[dict, bool]:
    reason = resolve_reason(reason)
    entries = load()
    existing = next((e for e in entries if normalize(e["company"]) == normalize(company)), None)
    entry = existing or {"company": company}
    entry["company"] = company   # re-running with different capitalisation updates the display name
    entry.update({"reason": reason, "note": note or entry.get("note", ""), "added": entry.get("added", date.today().isoformat())})
    if aliases:
        entry["aliases"] = sorted(set((entry.get("aliases") or []) + aliases))
    if not existing:
        entries.append(entry)
    _save(entries)
    return entry, existing is None


def remove(company: str) -> bool:
    entries = load()
    keep = [e for e in entries if normalize(e["company"]) != normalize(company)]
    _save(keep)
    return len(keep) != len(entries)


def describe(e: dict) -> str:
    return e["reason"] + (f" ({e['note']})" if e.get("note") else "")
