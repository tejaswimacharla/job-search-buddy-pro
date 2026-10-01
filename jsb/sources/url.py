"""Import a single job by URL (ATS links, Jobright links, LinkedIn links, company career pages).

This fetches only the one public page the user pasted. It does not crawl LinkedIn or
Jobright search results, which both require a login and prohibit automated scraping.
"""
from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from ..models import Job
from ..text import html_to_text
from . import ashby, greenhouse, lever

BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")


def import_url(sess, url: str) -> Job:
    u = urlparse(url)
    host, parts = u.netloc.lower(), [p for p in u.path.split("/") if p]

    if "greenhouse.io" in host:
        # job-boards.greenhouse.io/<token>/jobs/<id>  or  boards.greenhouse.io/<token>/jobs/<id>
        if "jobs" in parts:
            i = parts.index("jobs")
            job = greenhouse.fetch_one(sess, parts[i - 1], parts[i + 1])
            if job:
                return job
    if "gh_jid=" in u.query:
        # Company-hosted Greenhouse embed; board token unknown, fall through to generic.
        pass
    if host == "jobs.lever.co" and len(parts) >= 2:
        job = lever.fetch_one(sess, parts[0], parts[1])
        if job:
            return job
    if host == "jobs.ashbyhq.com" and len(parts) >= 2:
        job = ashby.fetch_one(sess, parts[0], parts[1])
        if job:
            return job
    if "jobright.ai" in host:
        return _jobright(sess, url)
    if "linkedin.com" in host:
        return _linkedin(sess, url)
    return _generic(sess, url)


def _get(sess, url: str) -> str:
    r = sess.get(url, headers={"User-Agent": BROWSER_UA}, timeout=30)
    r.raise_for_status()
    return r.text


def _jobright(sess, url: str) -> Job:
    html = _get(sess, url)
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise ValueError("Jobright page had no embedded job data (it may require login).")
    ds = json.loads(m.group(1))["props"]["pageProps"]["dataSource"]
    jr, cr = ds["jobResult"], ds.get("companyResult") or {}
    q = jr.get("qualifications") or {}
    lines = [jr.get("jobSummary", ""), "", "Responsibilities"]
    lines += [f"- {x}" for x in jr.get("coreResponsibilities", [])]
    lines += ["", "Required"] + [f"- {x}" for x in q.get("mustHave", [])]
    if q.get("preferredHave"):
        lines += ["", "Preferred"] + [f"- {x}" for x in q["preferredHave"]]
    skills = [s["skill"] for s in jr.get("jdCoreSkills", [])]
    if skills:
        lines += ["", "Qualification tags: " + ", ".join(skills)]
    return Job(
        ats="jobright", company=cr.get("companyName") or "Unknown", ext_id=jr["jobId"],
        title=jr["jobTitle"], location="; ".join(jr.get("jobLocations") or [jr.get("jobLocation", "")]),
        url=url, apply_url=url, description="\n".join(lines).strip(),
        posted_at=(jr.get("publishTime") or "")[:10] or None, remote=bool(jr.get("isRemote")),
        compensation=jr.get("salaryDesc", ""),
        extra={"min_years": jr.get("minYearsOfExperience"), "seniority": jr.get("jobSeniority")},
    )


def _linkedin(sess, url: str) -> Job:
    m = re.search(r"(?:currentJobId=|/jobs/view/(?:[^/]*-)?)(\d{6,})", url)
    if not m:
        raise ValueError("Could not find a LinkedIn job id in that URL.")
    jid = m.group(1)
    # Public guest view of a single posting (no login).
    html = _get(sess, f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{jid}")
    soup = BeautifulSoup(html, "html.parser")
    title = _txt(soup.select_one(".top-card-layout__title, h2"))
    company = _txt(soup.select_one(".topcard__org-name-link, .topcard__flavor"))
    loc = _txt(soup.select_one(".topcard__flavor--bullet"))
    desc_el = soup.select_one(".show-more-less-html__markup, .description__text")
    apply = soup.select_one("code#applyUrl")
    apply_url = url
    if apply and apply.string:
        am = re.search(r'url=([^"&]+)', apply.string)
        if am:
            from urllib.parse import unquote
            apply_url = unquote(am.group(1))
    return Job(ats="linkedin", company=company or "Unknown", ext_id=jid, title=title or "Unknown",
               location=loc, url=f"https://www.linkedin.com/jobs/view/{jid}", apply_url=apply_url,
               description=html_to_text(str(desc_el)) if desc_el else "",
               remote="remote" in (loc or "").lower())


def _generic(sess, url: str) -> Job:
    html = _get(sess, url)
    soup = BeautifulSoup(html, "html.parser")
    # Prefer schema.org JobPosting JSON-LD, which most career sites emit.
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or "")
        except json.JSONDecodeError:
            continue
        for d in data if isinstance(data, list) else [data]:
            if isinstance(d, dict) and d.get("@type") == "JobPosting":
                org = d.get("hiringOrganization") or {}
                loc = d.get("jobLocation") or {}
                loc = loc[0] if isinstance(loc, list) and loc else loc
                addr = (loc or {}).get("address") or {}
                loc_s = ", ".join(x for x in [addr.get("addressLocality"), addr.get("addressRegion"),
                                              addr.get("addressCountry") if isinstance(addr.get("addressCountry"), str) else None] if x)
                return Job(ats="web", company=org.get("name", urlparse(url).netloc), ext_id=_hash(url),
                           title=d.get("title", "Unknown"), location=loc_s, url=url, apply_url=url,
                           description=html_to_text(d.get("description", "")),
                           posted_at=(d.get("datePosted") or "")[:10] or None,
                           remote=d.get("jobLocationType") == "TELECOMMUTE")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    main = soup.find("main") or soup.body or soup
    return Job(ats="web", company=urlparse(url).netloc, ext_id=_hash(url),
               title=_txt(soup.find("h1")) or _txt(soup.title) or "Unknown", location="",
               url=url, apply_url=url, description=html_to_text(str(main)))


def _txt(el) -> str:
    return el.get_text(" ", strip=True) if el else ""


def _hash(s: str) -> str:
    return hashlib.sha1(s.encode()).hexdigest()[:12]
