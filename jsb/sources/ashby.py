from __future__ import annotations

from ..models import Job
from ..text import html_to_text


def _job(board: str, org_name: str, j: dict) -> Job:
    locs = [j.get("location", "")] + [s.get("location", "") for s in j.get("secondaryLocations", [])]
    loc = "; ".join(x for x in locs if x)
    comp = (j.get("compensation") or {}).get("scrapeableCompensationSalarySummary") or ""
    return Job(
        ats="ashby", company=org_name, ext_id=j["id"], title=j["title"].strip(), location=loc,
        url=j["jobUrl"], apply_url=j.get("applyUrl") or j["jobUrl"] + "/application",
        description=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml", "")),
        posted_at=j.get("publishedAt") or None,
        remote=bool(j.get("isRemote")) or (j.get("workplaceType") == "Remote"),
        compensation=comp, extra={"board": board, "workplaceType": j.get("workplaceType")},
    )


def fetch(sess, board: str, name: str | None = None) -> list[Job]:
    r = sess.get(f"https://api.ashbyhq.com/posting-api/job-board/{board}",
                 params={"includeCompensation": "true"}, timeout=30)
    r.raise_for_status()
    data = r.json()
    name = name or board.capitalize()
    return [_job(board, name, j) for j in data.get("jobs", []) if j.get("isListed", True)]


def fetch_one(sess, board: str, job_id: str) -> Job | None:
    for j in fetch(sess, board):
        if j.ext_id == job_id:
            return j
    return None
