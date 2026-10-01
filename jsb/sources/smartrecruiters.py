from __future__ import annotations

from ..models import Job
from ..text import html_to_text


def fetch(sess, company: str) -> list[Job]:
    jobs, offset = [], 0
    while True:
        r = sess.get(f"https://api.smartrecruiters.com/v1/companies/{company}/postings",
                     params={"limit": 100, "offset": offset, "country": "us"}, timeout=30)
        r.raise_for_status()
        data = r.json()
        for p in data.get("content", []):
            loc = p.get("location") or {}
            loc_s = ", ".join(x for x in [loc.get("city"), loc.get("region"), loc.get("country", "").upper()] if x)
            jobs.append(Job(
                ats="smartrecruiters", company=(p.get("company") or {}).get("name", company),
                ext_id=p["id"], title=p["name"].strip(), location=loc_s + (" (remote)" if loc.get("remote") else ""),
                url=f"https://jobs.smartrecruiters.com/{company}/{p['id']}",
                apply_url=f"https://jobs.smartrecruiters.com/{company}/{p['id']}",
                description="", posted_at=p.get("releasedDate") or None,
                remote=bool(loc.get("remote")), extra={"board": company},
            ))
        offset += 100
        if offset >= data.get("totalFound", 0) or offset >= 1000:
            break
    return jobs


def hydrate(sess, job: Job) -> Job:
    r = sess.get(f"https://api.smartrecruiters.com/v1/companies/{job.extra['board']}/postings/{job.ext_id}", timeout=30)
    if r.ok:
        sections = (r.json().get("jobAd") or {}).get("sections") or {}
        job.description = "\n\n".join(
            f"{s.get('title', '')}\n{html_to_text(s.get('text', ''))}" for s in sections.values() if s.get("text"))
    return job
