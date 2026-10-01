"""Apply criteria: posted within N hours, base pay floor, required-years window, match score,
citizenship, recent layoffs, and per-company application caps."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from functools import lru_cache

from . import pay


def hours_since(posted: str | None) -> float | None:
    if not posted:
        return None
    try:
        if len(posted) <= 10:   # date only: count from the start of that day
            d = date.fromisoformat(posted)
            return (date.today() - d).days * 24.0
        dt = datetime.fromisoformat(posted.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    except ValueError:
        return None


def check(crit: dict, *, posted: str | None, compensation: str, description: str,
          years_min: int | None, years_max: int | None, flags: list[str] | None = None) -> list[str]:
    """Returns the list of failed criteria (empty = eligible)."""
    fails = []
    h = hours_since(posted)
    if h is None or h > crit["max_posted_hours"]:
        fails.append("older than %dh" % crit["max_posted_hours"] if h is not None else "post date unknown")

    rng = pay.parse(compensation, description)
    if rng is None:
        if not crit.get("include_unlisted_pay"):
            fails.append("pay not listed")
    else:
        lo, hi = rng
        v = hi if crit.get("pay_basis", "top") == "top" else lo
        if v <= crit["min_base_pay"]:
            fails.append(f"pay ${hi // 1000}K max" if crit.get("pay_basis", "top") == "top" else f"pay from ${lo // 1000}K")

    if years_min is None:
        if not crit.get("include_unlisted_years", True):
            fails.append("years not stated")
    elif years_min < crit["years_min"] or (years_max or years_min) > crit["years_max"]:
        fails.append(f"asks {years_max if (years_max or 0) > crit['years_max'] else years_min}+ yrs")
    if crit.get("exclude_citizenship_required") and "Requires US citizenship / clearance" in (flags or []):
        fails.append("US citizenship / clearance required")
    if crit.get("exclude_no_sponsorship") and "States no visa sponsorship" in (flags or []):
        fails.append("states no visa sponsorship")
    return fails


def match_score(row) -> float:
    """Claude's score when the job has been re-ranked, otherwise the rule score."""
    return row["llm_score"] if row["llm_score"] is not None else (row["score"] or 0)


def match_tier(crit: dict, row) -> str:
    """'apply' (min_match+, or above a capped company's bar) or 'stretch' (stretch_match..min_match)."""
    if company_limit(crit, row["company"]) or match_score(row) >= (crit.get("min_match") or 0):
        return "apply"
    return "stretch"


def company_limit(crit: dict, company: str) -> dict | None:
    from .blocklist import _same
    for lim in crit.get("company_limits") or []:
        if any(_same(company, n) for n in [lim["company"], *(lim.get("aliases") or [])]):
            return lim
    return None


@lru_cache(maxsize=4)
def _submitted(stamp: str) -> list[tuple[str, str]]:
    from . import db
    conn = db.connect()
    return [(r[0], r[1]) for r in conn.execute(
        "SELECT company, COALESCE(submitted_at, substr(notes, 11, 10)) FROM jobs WHERE status='submitted'")]


def applications_used(lim: dict) -> list[str]:
    """Dates of applications to this company inside its window: tracked submissions + ones you logged."""
    from .blocklist import _same
    since = (date.today() - timedelta(days=lim.get("window_days", 180))).isoformat()
    names = [lim["company"], *(lim.get("aliases") or [])]
    dates = [d for c, d in _submitted(datetime.now().strftime("%Y%m%d%H%M"))
             if d and d >= since and any(_same(c, n) for n in names)]
    dates += [str(a.get("date") if isinstance(a, dict) else a) for a in lim.get("already_applied") or []]
    return sorted(d for d in dates if d >= since)


def check_row(crit: dict, row, detail: dict, ignore_match: bool = False) -> list[str]:
    fails = check(crit, posted=row["posted_at"], compensation=row["compensation"] or "",
                  description=row["description"] or "", years_min=detail.get("required_years"),
                  years_max=detail.get("years_max", detail.get("required_years")), flags=detail.get("flags"))
    m = match_score(row)
    lim = company_limit(crit, row["company"])
    if lim:
        used = applications_used(lim)
        if len(used) >= lim["max_applications"]:
            fails.append(f"{lim['company']} cap reached ({len(used)}/{lim['max_applications']} in {lim['window_days']}d)")
        if not ignore_match and m <= lim["above_match"]:
            fails.append(f"match {m:.0f}% (needs >{lim['above_match']}% at {lim['company']})")
    elif not ignore_match and crit.get("min_match"):
        floor = crit.get("stretch_match") or crit["min_match"]
        if m < floor:
            fails.append(f"match {m:.0f}% (needs {floor}%+)")
    months = crit.get("exclude_recent_layoffs_months") or 0
    if months:
        from .layoffs import index
        hit = index(months).summary(row["company"])
        if hit:
            fails.append(hit)
    return fails
