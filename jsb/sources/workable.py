"""Workable public widget API: https://apply.workable.com/api/v1/widget/accounts/<slug>?details=true
(one request returns every job with its description)."""
from __future__ import annotations

import ast

from ..models import Job
from ..text import html_to_text


def _locations(j: dict) -> str:
    locs = j.get("locations")
    if isinstance(locs, str):
        try:
            locs = ast.literal_eval(locs)
        except (ValueError, SyntaxError):
            locs = None
    out = []
    for l in locs or [{"city": j.get("city"), "region": j.get("state"), "country": j.get("country")}]:
        s = ", ".join(x for x in [l.get("city"), l.get("region"), l.get("country")] if x)
        if s:
            out.append(s)
    s = "; ".join(dict.fromkeys(out))
    return s + ("; Remote" if str(j.get("telecommuting")).lower() == "true" else "")


def fetch(sess, slug: str, name: str | None = None) -> list[Job]:
    r = sess.get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}", params={"details": "true"}, timeout=30)
    r.raise_for_status()
    data = r.json()
    company = name or data.get("name") or slug
    jobs = []
    for j in data.get("jobs", []):
        jobs.append(Job(
            ats="workable", company=company, ext_id=j["shortcode"], title=j["title"].strip(),
            location=_locations(j), url=j.get("url") or j["shortlink"],
            apply_url=j.get("application_url") or j["shortlink"] + "/apply",
            description=html_to_text(j.get("description", "") + (j.get("requirements") or "") + (j.get("benefits") or "")),
            posted_at=j.get("published_on") or j.get("created_at"),
            remote=str(j.get("telecommuting")).lower() == "true", extra={"board": slug},
        ))
    return jobs
