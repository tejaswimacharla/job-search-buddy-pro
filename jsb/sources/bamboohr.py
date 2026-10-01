"""BambooHR public careers feed: https://<sub>.bamboohr.com/careers/list (+ /careers/<id>/detail)."""
from __future__ import annotations

from ..models import Job
from ..text import html_to_text


def _loc(loc: dict | None, remote) -> str:
    loc = loc or {}
    parts = [loc.get("city"), loc.get("state") or loc.get("province"), loc.get("addressCountry") or loc.get("country")]
    s = ", ".join(p for p in parts if p)
    return (s + "; Remote" if s else "Remote") if remote else s


def fetch(sess, sub: str, name: str | None = None) -> list[Job]:
    r = sess.get(f"https://{sub}.bamboohr.com/careers/list", timeout=30)
    r.raise_for_status()
    jobs = []
    for j in r.json().get("result", []):
        remote = j.get("isRemote") or str(j.get("locationType")) == "1"
        url = f"https://{sub}.bamboohr.com/careers/{j['id']}"
        jobs.append(Job(ats="bamboohr", company=name or sub, ext_id=str(j["id"]), title=j["jobOpeningName"].strip(),
                        location=_loc(j.get("location"), remote), url=url, apply_url=url, description="",
                        remote=bool(remote), extra={"board": sub}))
    return jobs


def hydrate(sess, job: Job) -> Job:
    sub = job.extra["board"]
    r = sess.get(f"https://{sub}.bamboohr.com/careers/{job.ext_id}/detail", timeout=30)
    if r.ok:
        o = (r.json().get("result") or {}).get("jobOpening") or {}
        job.description = html_to_text(o.get("description") or "")
        job.posted_at = o.get("datePosted") or job.posted_at
        job.compensation = o.get("compensation") or ""
        if o.get("location"):
            job.location = _loc(o["location"], job.remote)
    return job
