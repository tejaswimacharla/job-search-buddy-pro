"""Find which public job board (Greenhouse / Ashby / Lever / SmartRecruiters / Workday) a company
uses, so it can be added to scraping. Results go to config/companies_discovered.yaml, which is
merged with config/search.yaml at load time (search.yaml stays hand-edited).

Resolution order per company:
  1. Sniff the company's homepage and careers pages for embedded board links (most reliable).
  2. Probe board APIs with slugs guessed from the website domain and company name.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

import yaml

from .config import ROOT

DISCOVERED_PATH = ROOT / "config" / "companies_discovered.yaml"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/128 Safari/537.36"

BOARD_PATTERNS = [
    ("greenhouse", r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_-]+)"),
    ("greenhouse", r"boards-api\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)"),
    ("ashby", r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)"),
    ("ashby", r"api\.ashbyhq\.com/posting-api/job-board/([A-Za-z0-9_.%-]+)"),
    ("lever", r"jobs\.lever\.co/([A-Za-z0-9_-]+)"),
    ("smartrecruiters", r"(?:careers|jobs)\.smartrecruiters\.com/([A-Za-z0-9_-]+)"),
    ("workday", r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)"),
    ("bamboohr", r"([a-z0-9-]+)\.bamboohr\.com"),
    ("workable", r"apply\.workable\.com/(?!j/|api/)([a-z0-9-]+)"),
    ("workable", r"([a-z0-9-]+)\.workable\.com"),
    ("workable", r"workable\.com\\?/j\\?/([A-Z0-9]{6,})"),          # job short link (maybe JSON-escaped); resolved below
    ("rippling", r"ats\.rippling\.com/(?:[a-z]{2}-[A-Z]{2}/)?([a-z0-9-]+)"),
]
IGNORE_TOKENS = {"embed", "api", "v1", "jobs", "boards", "js", "static", "assets", "careers", "search", "www", "apply",
                 "cdn", "widget", "resources", "help", "support"}
# Boards we can't scrape yet; recorded so we know which are worth adding.
OTHER_ATS = re.compile(r"(jobvite\.com|icims\.com|breezy\.hr|"
                       r"teamtailor\.com|pinpointhq\.com|recruitee\.com|paylocity\.com|paycomonline|ultipro|"
                       r"successfactors|taleo|jazzhr|applytojob\.com|dover\.com|gem\.com/jobs|welcometothejungle|"
                       r"oraclecloud\.com/hcmUI|adp\.com)", re.I)


def _slugs(name: str, website: str) -> list[str]:
    host = urlparse(website if "//" in website else "http://" + website).netloc.lower()
    host = re.sub(r"^www\.", "", host)
    root = host.split(".")[0] if host else ""
    base = re.sub(r"\b(inc|llc|corp|co|technologies|technology|labs|health|ai|hq|group)\b\.?", "", name.lower())
    cands = [root, re.sub(r"[^a-z0-9]", "", name.lower()), re.sub(r"[^a-z0-9]", "", base),
             re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"), root + "hq", root + "inc", "get" + root if root else ""]
    return [c for c in dict.fromkeys(cands) if c and len(c) >= 3]


def _count(sess, ats: str, token: str) -> int | None:
    """Open jobs on a board; None if the board doesn't exist (or couldn't be read)."""
    urls = {
        "greenhouse": (f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs", "jobs"),
        "ashby": (f"https://api.ashbyhq.com/posting-api/job-board/{token}", "jobs"),
        "lever": (f"https://api.lever.co/v0/postings/{token}?mode=json&limit=200", None),
        "bamboohr": (f"https://{token}.bamboohr.com/careers/list", "result"),
        "workable": (f"https://apply.workable.com/api/v1/widget/accounts/{token}", "jobs"),
        "rippling": (f"https://api.rippling.com/platform/api/ats/v1/board/{token}/jobs", None),
        "smartrecruiters": (f"https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=1", "totalFound"),
    }
    if ats not in urls:
        return None
    url, key = urls[ats]
    try:
        r = sess.get(url, timeout=15)
        if not r.ok or "json" not in r.headers.get("content-type", ""):
            return None
        data = r.json()
        if key is None:
            return len(data) if isinstance(data, list) else None
        v = data.get(key) if isinstance(data, dict) else None
        return v if isinstance(v, int) else (len(v) if isinstance(v, list) else None)
    except Exception:  # noqa: BLE001
        return None


def _sniff(sess, website: str, notes: dict) -> tuple[str, object] | None:
    if not website:
        return None
    base = website if "//" in website else "https://" + website
    base = base.replace("http://", "https://").rstrip("/")
    for path in ("", "/careers", "/jobs", "/company/careers", "/about/careers", "/careers/jobs"):
        try:
            r = sess.get(base + path, headers={"User-Agent": UA}, timeout=12, allow_redirects=True)
        except Exception:  # noqa: BLE001
            continue
        if r.status_code in (403, 429, 503):
            notes["blocked"] = True
        text = r.text if r.ok else ""
        other = OTHER_ATS.search(r.url + " " + text)
        if other and "other_ats" not in notes:
            notes["other_ats"] = other.group(1).lower().split(".")[0]
        # The careers page might itself live on the board (redirect to jobs.ashbyhq.com/x etc.)
        text = r.url + " " + text
        for ats, rx in BOARD_PATTERNS:
            for m in re.finditer(rx, text):
                if ats == "workday":
                    tenant, host, site = m.groups()
                    if site.lower() not in IGNORE_TOKENS:
                        return "workday", {"tenant": tenant, "host": host, "site": site}
                    continue
                token = m.group(1).strip(".")
                if token.lower() in IGNORE_TOKENS:
                    continue
                if ats == "workable" and "/j" in m.group(0).replace("\\", ""):
                    # A job short link: its page names the account in <meta name="subdomain">.
                    try:
                        jr = sess.get(f"https://apply.workable.com/j/{token}", headers={"User-Agent": UA}, timeout=12)
                        sm = re.search(r'name="subdomain" content="([a-z0-9-]+)"', jr.text) if jr.ok else None
                        token = sm.group(1) if sm else ""
                    except Exception:  # noqa: BLE001
                        token = ""
                    if not token:
                        continue
                # Linked from the company's own site: trust it even if it has no openings today.
                if _count(sess, ats, token) is not None:
                    return ats, token
    return None


def resolve(sess, name: str, website: str) -> dict:
    notes: dict = {}
    hit = _sniff(sess, website, notes)
    how = "careers page"
    if not hit:
        how = "slug probe"
        for slug in _slugs(name, website):
            for ats in ("greenhouse", "ashby", "lever"):
                if (_count(sess, ats, slug) or 0) > 0:
                    hit = (ats, slug)
                    break
            if hit:
                break
    if not hit:
        out = {"company": name, "ats": None}
        if notes.get("other_ats"):
            out["unsupported_ats"] = notes["other_ats"]
        elif notes.get("blocked"):
            out["note"] = "careers site blocks automated requests"
        return out
    ats, token = hit
    out = {"company": name, "ats": ats, "found_via": how}
    if ats == "workday":
        out["workday"] = dict(token, company=name)
    else:
        out["token"] = token
    return out


def load_discovered() -> dict:
    if not DISCOVERED_PATH.exists():
        return {}
    return yaml.safe_load(DISCOVERED_PATH.read_text()) or {}


def save_discovered(source: str, entries: list[dict]) -> None:
    data = load_discovered()
    data.setdefault("sources", {})[source] = entries
    header = ("# Generated by `jsb discover`. Companies found on external lists and the job board each one\n"
              "# uses. Merged into scraping alongside config/search.yaml. Safe to re-generate; set\n"
              "# `skip: true` on an entry to stop scraping it.\n")
    DISCOVERED_PATH.write_text(header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=120))


def merged_boards(cfg: dict) -> dict:
    """Add discovered boards to the config's greenhouse/ashby/lever/smartrecruiters/workday lists."""
    for entries in (load_discovered().get("sources") or {}).values():
        for e in entries:
            if not e.get("ats") or e.get("skip"):
                continue
            if e["ats"] == "workday":
                wd = e["workday"]
                if not any(w["tenant"] == wd["tenant"] and w["site"] == wd["site"] for w in cfg.setdefault("workday", [])):
                    cfg["workday"].append(wd)
            else:
                lst = cfg.setdefault(e["ats"], [])
                if e["token"] not in lst:
                    lst.append(e["token"])
    return cfg
