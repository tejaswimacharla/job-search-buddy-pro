"""Recent-layoff detection, so applications focus on employers likely to file PERM.

Why: before filing PERM, an employer must notify US workers it laid off in the same (or a
related) occupation in the area within the previous 6 months, so many employers pause PERM
after layoffs. This module treats any layoff at the company as a signal. That's conservative:
add an exception in config/layoffs.yaml when you know a company is still filing.

Sources, in order of trust:
  1. California WARN notices (official, EDD). Covers layoffs of 50+ at one CA site.
  2. Claude web research per company (news, company posts, layoffs.fyi, other states' WARN),
     cached for `research_ttl_days`. Every event carries a source URL.
  3. config/layoffs.yaml: your own entries and exceptions.
"""
from __future__ import annotations

import io
import json
import re
import subprocess
from datetime import date, datetime, timedelta
from functools import lru_cache

import yaml

from .config import DATA_DIR, ROOT

MANUAL_PATH = ROOT / "config" / "layoffs.yaml"
CA_PAGE = "https://edd.ca.gov/en/jobs_and_training/Layoff_Services_WARN"
CA_BASE = "https://edd.ca.gov"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/128 Safari/537.36"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS layoffs (
    company     TEXT NOT NULL,
    norm        TEXT NOT NULL,
    event_date  TEXT NOT NULL,
    employees   INTEGER,
    location    TEXT,
    kind        TEXT,
    source      TEXT NOT NULL,      -- 'ca-warn' | 'web' | 'manual'
    url         TEXT,
    UNIQUE(norm, event_date, source, location)
);
CREATE TABLE IF NOT EXISTS layoff_research (
    norm        TEXT PRIMARY KEY,
    company     TEXT NOT NULL,
    checked_at  TEXT NOT NULL,
    result      TEXT NOT NULL
);
"""

_LEGAL = re.compile(r"\b(inc|llc|l\.l\.c|ltd|corp|corporation|co|company|incorporated|plc|lp|holdings?|usa|us|u\.s\.)\b\.?", re.I)


# Legal entity names in WARN filings -> the brand companies post jobs under.
LEGAL_TO_BRAND = {
    "metaplatforms": "meta", "facebook": "meta", "oracleamerica": "oracle", "amazoncom": "amazon",
    "amazoncomservices": "amazon", "amazonwebservices": "amazon", "alphabet": "google", "salesforcecom": "salesforce",
    "tmobileusa": "tmobile", "microsoftcorporation": "microsoft", "applecomputer": "apple",
    "chimefinancial": "chime", "nvidiacorporation": "nvidia", "intelcorporation": "intel",
    "ciscosystems": "cisco", "paypalholdings": "paypal", "blockinc": "block", "zillowgroup": "zillow",
}


def norm(name: str) -> str:
    """'Salesforce, Inc.' -> 'salesforce'; 'Meta Platforms, Inc' -> 'meta'; 'Google LLC dba X' -> 'google'"""
    name = re.split(r"\b(dba|d/b/a|formerly)\b", name, flags=re.I)[0]
    n = re.sub(r"[^a-z0-9]", "", _LEGAL.sub("", name.lower()))
    return LEGAL_TO_BRAND.get(n, n)


def ensure_schema(conn) -> None:
    conn.executescript(SCHEMA_SQL)


# ---------------------------------------------------------------- California WARN
def refresh_ca_warn(sess, conn, since: date) -> int:
    """Download the current CA WARN spreadsheet plus the prior fiscal-year PDF (cached)."""
    import warnings

    import openpyxl

    warnings.filterwarnings("ignore", message="Data Validation extension", module="openpyxl")
    ensure_schema(conn)
    page = sess.get(CA_PAGE, headers={"User-Agent": UA}, timeout=30).text
    xlsx = re.search(r'href="([^"]*warn_report[^"]*\.xlsx)"', page)
    pdfs = re.findall(r'href="([^"]*warn-report-for-[^"]*\.pdf)"', page)
    rows: list[tuple] = []

    if xlsx:
        data = sess.get(CA_BASE + xlsx.group(1), headers={"User-Agent": UA}, timeout=60).content
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = next(w for w in wb.worksheets if w.title.strip().startswith("Detailed WARN"))
        for r in ws.iter_rows(min_row=3, values_only=True):
            if not r or not r[4] or not isinstance(r[1], datetime):
                continue
            rows.append((r[4], r[1].date(), r[6], f"{r[0]}; {r[7] or ''}".strip("; "), r[5]))

    # The newest PDF is the previous fiscal year; only needed while the window reaches into it.
    if pdfs and since < _fy_start(date.today()):
        rows += _ca_pdf(sess, CA_BASE + pdfs[0])

    n = 0
    for company, d, count, loc, kind in rows:
        if d < since - timedelta(days=31):
            continue
        cur = conn.execute("INSERT OR IGNORE INTO layoffs VALUES (?,?,?,?,?,?,?,?)",
                           (str(company).strip(), norm(str(company)), d.isoformat(),
                            int(count) if str(count or "").isdigit() else None, loc, kind, "ca-warn", CA_PAGE))
        n += cur.rowcount
    conn.commit()
    return n


WA_URL = "https://fortress.wa.gov/esd/file/WARN/Public/SearchWARN.aspx"


def refresh_wa_warn(sess, conn, since: date, max_pages: int = 40) -> int:
    """Washington ESD WARN notices (Seattle/Bellevue/Redmond). The portal is an ASP.NET grid,
    newest notices first; page through it via postbacks until notices predate `since`."""
    from bs4 import BeautifulSoup

    ensure_schema(conn)
    html = sess.get(WA_URL, headers={"User-Agent": UA}, timeout=30).text
    n, page = 0, 1
    while True:
        soup = BeautifulSoup(html, "html.parser")
        grid = soup.find("table", id=re.compile("gvMain"))
        oldest, got = None, 0
        for tr in grid.find_all("tr") if grid else []:
            # Company, Location, Layoff Start Date, # of Workers, Closure/Layoff, Type, Received Date
            tds = [td.get_text(" ", strip=True) for td in tr.find_all("td", recursive=False)]
            if len(tds) < 7 or not re.match(r"\d+/\d+/\d{4}$", tds[6] or ""):
                continue
            received = datetime.strptime(tds[6], "%m/%d/%Y").date()
            oldest, got = received, got + 1
            if received < since:
                continue
            cur = conn.execute("INSERT OR IGNORE INTO layoffs VALUES (?,?,?,?,?,?,?,?)",
                               (tds[0], norm(tds[0]), received.isoformat(),
                                int(tds[3]) if tds[3].isdigit() else None,
                                f"WA: {tds[1]}; starts {tds[2]}", f"{tds[4]} {tds[5]}".strip(), "wa-warn", WA_URL))
            n += cur.rowcount
        if not got or oldest < since or page >= max_pages:
            break
        page += 1
        form = {i["name"]: i.get("value", "") for i in soup.find_all("input", attrs={"name": True})
                if i.get("type") != "submit"}
        form.update({"__EVENTTARGET": "ucPSW$gvMain", "__EVENTARGUMENT": f"Page${page}"})
        html = sess.post(WA_URL, data=form, headers={"User-Agent": UA}, timeout=30).text
    conn.commit()
    return n


def _fy_start(d: date) -> date:
    return date(d.year if d.month >= 7 else d.year - 1, 7, 1)


def _ca_pdf(sess, url: str) -> list[tuple]:
    import pdfplumber

    cache = DATA_DIR / ("ca_warn_" + url.rsplit("/", 1)[-1] + ".json")
    if cache.exists():
        return [(c, date.fromisoformat(d), n, loc, k) for c, d, n, loc, k in json.loads(cache.read_text())]
    data = sess.get(url, headers={"User-Agent": UA}, timeout=120).content
    out = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            for r in page.extract_table() or []:
                # Notice Date, Received Date, Effective Date, Company, County, No. Of Employees, Type, Address
                if not r or len(r) < 8 or not re.match(r"\d\d/\d\d/\d{4}", r[0] or ""):
                    continue
                d = datetime.strptime(r[0], "%m/%d/%Y").date()
                address = (r[7] or "").replace("\n", " ")
                out.append(((r[3] or "").replace("\n", " "), d, r[5], f"{r[4]}; {address}", r[6]))
    cache.write_text(json.dumps([(c, d.isoformat(), n, loc, k) for c, d, n, loc, k in out]))
    return out


# ---------------------------------------------------------------- web research
RESEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "had_layoffs": {"type": "boolean"},
        "events": {"type": "array", "items": {"type": "object", "properties": {
            "date": {"type": "string", "description": "YYYY-MM-DD, or YYYY-MM if only the month is known"},
            "count": {"type": ["integer", "null"]},
            "scope": {"type": "string", "description": "who/where: teams, locations, % of staff"},
            "affects_engineering": {"type": ["boolean", "null"]},
            "confidence": {"type": "string", "enum": ["confirmed", "unconfirmed"],
                           "description": "confirmed = company statement, SEC filing, WARN notice, or reputable "
                                          "news outlet; unconfirmed = only crowdsourced/anonymous sources or rumor"},
            "source_url": {"type": "string"}},
            "required": ["date", "scope", "source_url", "confidence"]}},
        "summary": {"type": "string"},
    },
    "required": ["had_layoffs", "events", "summary"],
}

RESEARCH_PROMPT = """Research whether {company} (the employer posting jobs such as "{sample_title}") has
laid off employees between {since} and {today}. Use web search: news coverage, the company's own
announcements, layoffs.fyi, TrueUp, and state WARN notices.

Rules:
- Only count layoffs announced or carried out inside that window. Mention older ones only in the summary.
- Every event needs a source URL you actually saw. No source, no event.
- Mark an event "confirmed" only if a company statement, SEC filing, WARN notice or reputable news
  outlet reports it. Events seen only on crowdsourced/anonymous trackers (Blind, Reddit, forums)
  or labelled as rumors are "unconfirmed".
- Hiring freezes, reorganisations without job cuts, and layoffs at a different company with a similar
  name don't count.
- Say in the summary if the cuts were outside the US or outside engineering.
"""


def research(company: str, sample_title: str, since: date, model: str = "sonnet") -> dict:
    prompt = RESEARCH_PROMPT.format(company=company, sample_title=sample_title, since=since.isoformat(),
                                    today=date.today().isoformat())
    proc = subprocess.run(
        ["claude", "-p", "--output-format", "json", "--tools", "WebSearch,WebFetch",
         "--allowedTools", "WebSearch,WebFetch", "--model", model, "--no-session-persistence",
         "--json-schema", json.dumps(RESEARCH_SCHEMA)],
        input=prompt, capture_output=True, text=True, timeout=600)
    out = json.loads(proc.stdout or "{}")
    if proc.returncode != 0 or out.get("is_error") or not out.get("structured_output"):
        raise RuntimeError(out.get("result") or proc.stderr[:300] or "research failed")
    return out["structured_output"]


def store_research(conn, company: str, result: dict) -> None:
    ensure_schema(conn)
    n = norm(company)
    conn.execute("INSERT OR REPLACE INTO layoff_research VALUES (?,?,?,?)",
                 (n, company, datetime.now().isoformat(timespec="seconds"), json.dumps(result)))
    conn.execute("DELETE FROM layoffs WHERE norm=? AND source='web'", (n,))
    for e in result.get("events", []):
        d = e["date"] if len(e["date"]) == 10 else e["date"][:7] + "-15"
        conn.execute("INSERT OR IGNORE INTO layoffs VALUES (?,?,?,?,?,?,?,?)",
                     (company, n, d, e.get("count"), e.get("scope", ""), e.get("confidence", "unconfirmed"),
                      "web", e.get("source_url")))
    conn.commit()


def needs_research(conn, company: str, ttl_days: int) -> bool:
    ensure_schema(conn)
    row = conn.execute("SELECT checked_at FROM layoff_research WHERE norm=?", (norm(company),)).fetchone()
    return not row or datetime.fromisoformat(row[0]) < datetime.now() - timedelta(days=ttl_days)


# ---------------------------------------------------------------- manual file
def load_manual() -> dict:
    if not MANUAL_PATH.exists():
        return {"entries": [], "exceptions": []}
    d = yaml.safe_load(MANUAL_PATH.read_text()) or {}
    return {"entries": d.get("entries") or [], "exceptions": d.get("exceptions") or []}


# ---------------------------------------------------------------- lookup
class LayoffIndex:
    def __init__(self, conn, months: int):
        ensure_schema(conn)
        self.since = date.today() - timedelta(days=round(months * 30.44))
        manual = load_manual()
        self.exceptions = {norm(e["company"]): e for e in manual["exceptions"]}
        self.events: dict[str, list[dict]] = {}
        for company, n, d, count, loc, kind, source, url in conn.execute(
                "SELECT * FROM layoffs WHERE event_date >= ?", (self.since.isoformat(),)):
            # WARN notices are official; web events count only when research graded them "confirmed".
            self.events.setdefault(n, []).append(
                {"company": company, "date": d, "employees": count, "where": loc, "source": source, "url": url,
                 "confirmed": source != "web" or kind == "confirmed"})
        for e in manual["entries"]:
            d = str(e["date"])
            if d >= self.since.isoformat():
                self.events.setdefault(norm(e["company"]), []).append(
                    {"company": e["company"], "date": d, "employees": e.get("employees"),
                     "where": e.get("note", ""), "source": "manual", "url": e.get("url"), "confirmed": True})
        self.researched = {r[0]: json.loads(r[1]) for r in
                           conn.execute("SELECT norm, result FROM layoff_research").fetchall()}

    def lookup(self, company: str, aliases: list[str] | None = None) -> list[dict]:
        keys = {norm(company), *(norm(a) for a in aliases or [])}
        if keys & set(self.exceptions):
            return []
        return sorted((e for k in keys for e in self.events.get(k, [])), key=lambda e: e["date"], reverse=True)

    def summary(self, company: str) -> str | None:
        """One-line reason for excluding the company, from confirmed events only."""
        ev = [e for e in self.lookup(company) if e["confirmed"]]
        if not ev:
            return None
        e = ev[0]
        who = f"{e['employees']} people" if e.get("employees") else "reported"
        src = {"ca-warn": "CA WARN", "wa-warn": "WA WARN", "web": "news", "manual": "your list"}.get(e["source"], e["source"])
        more = f" +{len(ev) - 1} more" if len(ev) > 1 else ""
        return f"layoffs {e['date']} ({who}, {src}){more}"

    def unconfirmed(self, company: str) -> list[dict]:
        return [e for e in self.lookup(company) if not e["confirmed"]]


@lru_cache(maxsize=1)
def _index_cached(months: int, stamp: str):
    from . import db
    return LayoffIndex(db.connect(), months)


def index(months: int) -> LayoffIndex:
    # Rebuilt at most once per process per minute; data only changes on refresh/research.
    return _index_cached(months, datetime.now().strftime("%Y%m%d%H%M"))
