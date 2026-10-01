from __future__ import annotations

import re
from datetime import date, timedelta

from ..models import Job
from ..text import html_to_text


def _base(t: dict) -> str:
    return f"https://{t['tenant']}.{t['host']}.myworkdayjobs.com"


def _posted(posted_on: str) -> str | None:
    # "Posted Today", "Posted Yesterday", "Posted 3 Days Ago", "Posted 30+ Days Ago"
    s = (posted_on or "").lower()
    if "today" in s:
        return date.today().isoformat()
    if "yesterday" in s:
        return (date.today() - timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\+?\s+days", s)
    return (date.today() - timedelta(days=int(m.group(1)))).isoformat() if m else None


def fetch(sess, t: dict, queries: list[str], max_per_query: int = 100) -> list[Job]:
    api = f"{_base(t)}/wday/cxs/{t['tenant']}/{t['site']}"
    seen: dict[str, dict] = {}
    for q in queries:
        for offset in range(0, max_per_query, 20):
            r = sess.post(f"{api}/jobs", json={"appliedFacets": {}, "limit": 20, "offset": offset,
                                               "searchText": q}, timeout=30)
            if not r.ok:
                break
            postings = r.json().get("jobPostings", [])
            for p in postings:
                if p.get("externalPath"):
                    seen.setdefault(p["externalPath"], p)
            if len(postings) < 20:
                break
    jobs = []
    for path, p in seen.items():
        posted = _posted(p.get("postedOn", ""))
        jobs.append(Job(
            ats="workday", company=t["company"], ext_id=path.rsplit("_", 1)[-1],
            title=p.get("title", "").strip(), location=p.get("locationsText", ""),
            url=f"{_base(t)}/{t['site']}{path}", apply_url=f"{_base(t)}/{t['site']}{path}/apply",
            description="", posted_at=posted, remote="remote" in p.get("locationsText", "").lower(),
            extra={"workday": t, "path": path},
        ))
    return jobs


def hydrate(sess, job: Job) -> Job:
    """Workday list responses have no description; fetch it for shortlisted jobs only."""
    t, path = job.extra["workday"], job.extra["path"]
    r = sess.get(f"{_base(t)}/wday/cxs/{t['tenant']}/{t['site']}{path}", timeout=30)
    if r.ok:
        info = r.json().get("jobPostingInfo", {})
        job.description = html_to_text(info.get("jobDescription", ""))
        if info.get("location"):
            extra_locs = info.get("additionalLocations") or []
            job.location = "; ".join([info["location"], *extra_locs])
        if info.get("startDate"):
            job.posted_at = info["startDate"][:10]
    return job
