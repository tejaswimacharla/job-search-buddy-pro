"""Rippling ATS public board: https://api.rippling.com/platform/api/ats/v1/board/<slug>/jobs (+ /<id>)."""
from __future__ import annotations

from ..models import Job
from ..text import html_to_text

API = "https://api.rippling.com/platform/api/ats/v1/board"


def fetch(sess, slug: str, name: str | None = None) -> list[Job]:
    r = sess.get(f"{API}/{slug}/jobs", timeout=30)
    r.raise_for_status()
    jobs = []
    for j in r.json():
        loc = (j.get("workLocation") or {}).get("label", "")
        jobs.append(Job(ats="rippling", company=name or slug, ext_id=j["uuid"], title=j["name"].strip(),
                        location=loc, url=j["url"], apply_url=j["url"] + "/apply", description="",
                        remote="remote" in loc.lower(), extra={"board": slug}))
    return jobs


def hydrate(sess, job: Job) -> Job:
    r = sess.get(f"{API}/{job.extra['board']}/jobs/{job.ext_id}", timeout=30)
    if not r.ok:
        return job
    d = r.json()
    desc = d.get("description") or {}
    parts = desc.values() if isinstance(desc, dict) else [desc]
    job.description = "\n\n".join(html_to_text(p) for p in parts if isinstance(p, str))
    job.posted_at = d.get("createdOn") or job.posted_at
    if d.get("workLocations"):
        job.location = "; ".join(d["workLocations"])
    ranges = [p for p in d.get("payRangeDetails") or [] if p.get("frequency") == "YEAR" and p.get("rangeStart")]
    if ranges:
        p = max(ranges, key=lambda p: p.get("rangeEnd") or 0)
        job.compensation = f"${int(p['rangeStart']):,} - ${int(p.get('rangeEnd') or p['rangeStart']):,}"
    return job
