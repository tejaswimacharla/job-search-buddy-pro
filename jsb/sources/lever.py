from __future__ import annotations

from datetime import datetime, timezone

from ..models import Job


def _job(board: str, j: dict, company: str | None = None) -> Job:
    company = company or board
    cats = j.get("categories") or {}
    locs = cats.get("allLocations") or [cats.get("location", "")]
    loc = "; ".join(x for x in locs if x)
    desc = "\n\n".join(filter(None, [
        j.get("openingPlain") or j.get("descriptionPlain", ""),
        *[f"{l['text']}\n" + _li(l.get("content", "")) for l in j.get("lists", [])],
        j.get("additionalPlain", ""),
    ]))
    posted = datetime.fromtimestamp(j["createdAt"] / 1000, tz=timezone.utc).isoformat(timespec="seconds") \
        if j.get("createdAt") else None
    return Job(
        ats="lever", company=company, ext_id=j["id"], title=j["text"].strip(), location=loc,
        url=j["hostedUrl"], apply_url=j.get("applyUrl") or j["hostedUrl"] + "/apply",
        description=desc, posted_at=posted,
        remote=(j.get("workplaceType") == "remote") or "remote" in loc.lower(),
        compensation=_salary(j.get("salaryRange")), extra={"board": board},
    )


def _li(html: str) -> str:
    from ..text import html_to_text
    return html_to_text(html)


def _salary(sr: dict | None) -> str:
    if not sr or not sr.get("min"):
        return ""
    return f"${sr['min']:,}-${sr['max']:,} {sr.get('currency', '')}".strip()


def fetch(sess, board: str, name: str | None = None) -> list[Job]:
    r = sess.get(f"https://api.lever.co/v0/postings/{board}", params={"mode": "json"}, timeout=30)
    r.raise_for_status()
    return [_job(board, j, name) for j in r.json()]


def fetch_one(sess, company: str, job_id: str) -> Job | None:
    r = sess.get(f"https://api.lever.co/v0/postings/{company}/{job_id}", timeout=30)
    return _job(company, r.json()) if r.ok else None
