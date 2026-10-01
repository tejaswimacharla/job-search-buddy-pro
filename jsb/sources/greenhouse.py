from __future__ import annotations

from ..models import Job
from ..text import html_to_text


def fetch(sess, token: str) -> list[Job]:
    r = sess.get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
                 params={"content": "true"}, timeout=30)
    r.raise_for_status()
    jobs = []
    for j in r.json().get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        jobs.append(Job(
            ats="greenhouse",
            company=j.get("company_name") or token,
            ext_id=str(j["id"]),
            title=j["title"].strip(),
            location=loc,
            url=j["absolute_url"],
            # Company career pages often wrap the form in an iframe; the hosted embed is uniform.
            apply_url=f"https://job-boards.greenhouse.io/embed/job_app?for={token}&token={j['id']}",
            description=html_to_text(j.get("content", "")),
            posted_at=j.get("first_published") or j.get("updated_at") or None,
            remote="remote" in loc.lower(),
            extra={"board": token},
        ))
    return jobs


def fetch_one(sess, token: str, job_id: str) -> Job | None:
    r = sess.get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{job_id}", timeout=30)
    if not r.ok:
        return None
    j = r.json()
    loc = (j.get("location") or {}).get("name", "")
    return Job("greenhouse", j.get("company_name") or token, str(j["id"]), j["title"].strip(), loc,
               j["absolute_url"], f"https://job-boards.greenhouse.io/embed/job_app?for={token}&token={j['id']}", html_to_text(j.get("content", "")),
               j.get("first_published") or j.get("updated_at") or None,
               "remote" in loc.lower(), extra={"board": token})
