"""Tailor your base resume to one job.

1. Claude reads the job description and your parsed base resume (every bullet has an ID) and
   returns a JD analysis plus a tailored resume in which every bullet and the summary cite the
   base-resume bullets they come from.
2. A deterministic truth audit checks that draft: citations exist and stay within the same role,
   every number appears in the cited bullets, ownership verbs (led / owned / designed) aren't
   stronger than the source, and every listed skill is on the base resume. Style fixes (em dashes,
   trailing periods) are applied automatically.
3. Bullets that fail get one repair round with Claude; anything still failing falls back to your
   original bullet text. Nothing unverified reaches the PDF.
4. The resume is rendered to an ATS-safe, single-column PDF (headless Chrome) and fitted to
   `resume.max_pages`, first by tightening spacing and then by dropping the least relevant bullets.

Output: output/<company>_<role>_<date>/ with the PDF, Markdown and plain-text versions, the JD,
analysis.md (themes, matches, gaps, questions) and audit.md (every bullet with its sources).
"""
from __future__ import annotations

import html
import json
import re
from datetime import date
from pathlib import Path

from . import resume as base
from .config import OUTPUT_DIR, ROOT
from .llm import ask_json
from .models import Job
from .profile import Profile
from .text import slugify

MIN_DIRECT_THEMES = 3   # fewer core themes with direct evidence than this: stop and ask instead

TRUTH_RULES = """Truth rules (non-negotiable):
- Every bullet must cite, in `sources`, the base-resume bullet IDs it is built from. An experience
  bullet may only cite bullets from that same role (E2.* for E2); never move work between jobs.
- Rewording, reordering, merging bullets from the same role, and leading with the parts the job
  cares about are all fine. Adding facts is not: no new tools, systems, scope, users, teams or
  outcomes that the cited bullets don't state.
- Numbers: use only numbers that appear in the cited bullets, unchanged. Never invent, round,
  convert (2x -> 100%) or combine metrics.
- Ownership: never upgrade a verb. "Contributed to" can't become "Led"; "worked on" can't become
  "Owned" or "Designed". Use led / owned / designed / architected only if a cited bullet says so.
- Skills section: only skills that appear somewhere on the base resume. Reorder and regroup them
  for the job; don't add any, even ones the job asks for.
- The summary follows the same rules and cites the bullets it draws on. Leave it empty if the base
  resume has no summary and the job doesn't benefit from one.
- If the job needs something the resume doesn't show, list it under gaps and ask about it in
  questions. Never paper over it."""

STYLE_RULES = """Style:
- Bullet shape: accurate action verb, what was built or changed, how, and the result when the
  source states one. One to two rendered lines (about 120-220 characters).
- Past roles in past tense; the current role in present tense for ongoing work.
- No first person, no trailing periods, no em dashes (use commas or semicolons).
- Avoid: responsible for, worked on, various, cutting-edge, leveraged, results-driven, passionate,
  team player, innovative, synergy.
- Mirror the job's own terms where the source supports them (if a bullet describes Kubernetes
  work and the job says "K8s", either is fine; if it describes ECS, don't call it Kubernetes)."""

TAILOR_PROMPT = """You are an expert resume writer tailoring a candidate's base resume to one job.
Target: {target}.

{truth}

{style}

What to produce:
1. analysis: the role's type, its core themes (what the job is really about, not every tool
   named) with importance and how directly the resume covers each (direct / transferable /
   none) and which bullet IDs show it; ATS keywords from the posting; gaps; questions you'd ask
   the candidate to strengthen this application.
2. experience: every role from the base resume, newest first, by `id`. For each, its bullets in
   priority order for this job (most relevant first; bullets near the end get cut if the page
   overflows). Recent and relevant roles get more bullets; older or unrelated roles 1-2. Aim for
   about {bullet_budget} experience bullets in total for a {pages}-page resume.
3. projects: include a project (by id) only if it out-ranks experience bullets for this job.
4. skills: regrouped and reordered for this job, most relevant first. Group names like
   Languages / Cloud & Infrastructure / Data / ML & AI; 3-6 groups.
5. changes: short notes on what you changed and why.

# Job: {title} at {company} ({location})
{description}

# Base resume (IDs in brackets)
{resume}
"""

_BULLETS = {"type": "array", "items": {"type": "object", "properties": {
    "text": {"type": "string"}, "sources": {"type": "array", "items": {"type": "string"}}},
    "required": ["text", "sources"]}}
TAILOR_SCHEMA = {
    "type": "object",
    "properties": {
        "analysis": {"type": "object", "properties": {
            "role_type": {"type": "string"},
            "core_themes": {"type": "array", "items": {"type": "object", "properties": {
                "theme": {"type": "string"}, "importance": {"type": "string", "enum": ["must", "strong", "nice"]},
                "coverage": {"type": "string", "enum": ["direct", "transferable", "none"]},
                "evidence": {"type": "array", "items": {"type": "string"}}},
                "required": ["theme", "importance", "coverage", "evidence"]}},
            "keywords": {"type": "array", "items": {"type": "string"}},
            "gaps": {"type": "array", "items": {"type": "string"}},
            "questions": {"type": "array", "items": {"type": "string"}}},
            "required": ["role_type", "core_themes", "keywords", "gaps", "questions"]},
        "summary": {"type": "object", "properties": {
            "text": {"type": "string"}, "sources": {"type": "array", "items": {"type": "string"}}},
            "required": ["text", "sources"]},
        "experience": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "bullets": _BULLETS}, "required": ["id", "bullets"]}},
        "projects": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "bullets": _BULLETS}, "required": ["id", "bullets"]}},
        "skills": {"type": "array", "items": {"type": "object", "properties": {
            "group": {"type": "string"}, "items": {"type": "array", "items": {"type": "string"}}},
            "required": ["group", "items"]}},
        "changes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["analysis", "summary", "experience", "skills", "changes"],
}

REPAIR_PROMPT = """These tailored resume bullets failed a truth check against the candidate's base
resume. Rewrite each one so it passes: keep it as relevant to the job as you can, but stay within
what its cited source bullets state. You may change the citations, but only to bullets from the
same role (same E#/P# prefix).

{truth}

{items}
"""
REPAIR_SCHEMA = {"type": "object", "properties": {"fixes": {"type": "array", "items": {"type": "object", "properties": {
    "where": {"type": "string"}, "text": {"type": "string"}, "sources": {"type": "array", "items": {"type": "string"}}},
    "required": ["where", "text", "sources"]}}}, "required": ["fixes"]}

COVER_PROMPT = """Write a short cover letter (3 paragraphs, under 250 words) for this job.

{truth}
Every paragraph cites the base-resume bullet IDs its claims come from. Plain, specific and
confident; no clichés ("I am excited to apply", "passionate"), no em dashes. Don't restate the
whole resume: pick the two or three things that matter most for this role. Don't invent anything
about the company beyond what the job description says.

# Job: {title} at {company}
{description}

# Base resume (IDs in brackets)
{resume}
"""
COVER_SCHEMA = {"type": "object", "properties": {
    "paragraphs": _BULLETS}, "required": ["paragraphs"]}


# ---------------------------------------------------------------- entry point
def slug_for(job: Job) -> str:
    return f"{slugify(job.company)}_{slugify(job.title)}"


def run_tailoring(job: Job, profile: Profile, cfg: dict, target: str, cover_letter: bool = False,
                  anyway: bool = False, model: str | None = None, log=print) -> tuple[str, str]:
    """Returns (status, output folder relative to the project): status is 'ready' or 'needs_input'."""
    res = profile.resume
    model = model or cfg["resume"]["model"]
    pages = int(cfg["resume"].get("max_pages") or 1)
    out = OUTPUT_DIR / f"{slug_for(job)}_{date.today().isoformat()}"
    out.mkdir(parents=True, exist_ok=True)
    rel = str(out.relative_to(ROOT))
    (out / "job_description.txt").write_text(_jd_text(job))

    log(f"Analyzing the job and drafting ({model}, ~1-2 min)...")
    draft = ask_json(TAILOR_PROMPT.format(
        target=target, truth=TRUTH_RULES, style=STYLE_RULES, bullet_budget=15 * pages, pages=pages,
        title=job.title, company=job.company, location=job.location, description=job.description[:15000],
        resume=base.as_text(res)), TAILOR_SCHEMA, model, timeout=900)
    (out / "draft.json").write_text(json.dumps(draft, indent=1))

    themes = draft["analysis"]["core_themes"]
    direct = [t for t in themes if t["coverage"] == "direct"]
    (out / "analysis.md").write_text(_analysis_md(job, target, draft))
    if len(direct) < MIN_DIRECT_THEMES and not anyway:
        (out / "questions.md").write_text(_questions_md(job, draft, len(direct)))
        return "needs_input", rel

    doc = assemble(draft, res)
    if not doc["identity"].get("location") and (cfg.get("candidate") or {}).get("location"):
        doc["identity"] = dict(doc["identity"], location=cfg["candidate"]["location"])
    issues = audit(doc, res, profile.years_experience)
    to_fix = [i for i in issues if i["severity"] == "fix"]
    if to_fix:
        log(f"Truth audit: {len(to_fix)} issue(s) to fix; asking Claude to repair them...")
        repair(doc, res, to_fix, model)
        removed = [i for i in issues if i["severity"] == "removed"]
        issues = removed + [dict(i, severity="repaired") for i in to_fix] + audit(doc, res, profile.years_experience)
        still = [i for i in issues if i["severity"] == "fix"]
        if still:
            fallback(doc, res, still)
            log(f"  {len(still)} still failed; used your original wording for those.")
            issues = [i for i in issues if i["severity"] != "fix"] + [dict(i, severity="reverted") for i in still]
        else:
            log("  All repaired.")
    else:
        log("Truth audit: clean.")

    log("Rendering the PDF and fitting it to "
        f"{pages} page{'s' if pages > 1 else ''}...")
    pdf_name = re.sub(r"[^A-Za-z0-9]+", "_", res["identity"].get("name") or "resume").strip("_") + "_Resume.pdf"
    trimmed = fit_and_render(doc, out / pdf_name, pages)
    (out / "resume.html").write_text(render_html(doc, DENSITIES[0]))
    (out / "resume.md").write_text(render_md(doc))
    (out / "resume.txt").write_text(render_txt(doc))
    (out / "tailored.json").write_text(json.dumps(doc, indent=1))
    (out / "audit.md").write_text(_audit_md(doc, res, issues, trimmed))

    if cover_letter:
        log("Writing the cover letter...")
        write_cover_letter(job, res, out, model, pdf_name.replace("_Resume.pdf", "_Cover_Letter.pdf"))
    return "ready", rel


def _jd_text(job: Job) -> str:
    head = [job.title, f"{job.company} - {job.location or 'Location not listed'}", f"Source: {job.url}"]
    if job.compensation:
        head.append(f"Pay: {job.compensation}")
    return "\n".join(head) + "\n\n" + job.description.strip() + "\n"


# ---------------------------------------------------------------- assemble + audit
def assemble(draft: dict, res: dict) -> dict:
    """The tailored document: header, dates and education come straight from the base resume;
    only bullets, summary and skills come from the draft."""
    by_id = {e["id"]: e for e in res["experience"]}
    drafted = {e["id"]: e["bullets"] for e in draft.get("experience") or [] if e.get("id") in by_id}
    exp = []
    for e in res["experience"]:   # every role, base order, even if the draft skipped one
        bullets = drafted.get(e["id"]) or [{"text": b["text"], "sources": [b["id"]]} for b in e["bullets"][:2]]
        exp.append({k: e.get(k, "") for k in ("id", "company", "title", "location", "start", "end")}
                   | {"bullets": [dict(b, sources=list(b.get("sources") or [])) for b in bullets]})
    projs = {p["id"]: p for p in res.get("projects") or []}
    projects = [{k: projs[p["id"]].get(k, "") for k in ("id", "name", "subtitle")}
                | {"bullets": [dict(b, sources=list(b.get("sources") or [])) for b in p["bullets"]]}
                for p in draft.get("projects") or [] if p.get("id") in projs and p.get("bullets")]
    summary = draft.get("summary") or {"text": "", "sources": []}
    return {"identity": res["identity"], "summary": dict(summary), "experience": exp, "projects": projects,
            "skills": [dict(g) for g in draft.get("skills") or [] if g.get("items")],
            "education": res.get("education") or [], "certifications": res.get("certifications") or [],
            "other": res.get("other") or [], "changes": draft.get("changes") or []}


NUM = re.compile(r"\d+(?:[.,]\d+)*")
VERB_GROUPS = {
    "lead": {"led", "lead", "leads", "leading", "headed", "spearheaded", "directed", "managed", "drove", "orchestrated",
             "championed"},
    "own": {"owned", "own", "owns", "owning", "owner", "ownership"},
    "design": {"designed", "design", "designs", "architected", "architect", "architecture", "architecting"},
}
BANNED = re.compile(r"\b(responsible for|worked on|various|cutting[- ]edge|leveraged|results[- ]driven|passionate|"
                    r"team player|synergy)\b", re.I)
FIRST_PERSON = re.compile(r"\b(I|my|me|we|our)\b")


def _nums(s: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in NUM.findall(s)}


def _words(s: str) -> set[str]:
    return set(re.findall(r"[a-z]+", s.lower()))


def _items(doc: dict):
    """(where, item, scope) for every citing block. scope = the ID prefix its sources must share."""
    if doc["summary"].get("text"):
        yield "summary", doc["summary"], None
    for sec in ("experience", "projects"):
        for e in doc[sec]:
            for i, b in enumerate(e["bullets"], 1):
                yield f"{e['id']}#{i}", b, e["id"]


def audit(doc: dict, res: dict, years: float) -> list[dict]:
    """Check the tailored document against the base resume. Fixes style in place; returns issues
    with severity 'fix' (must be repaired or reverted) or 'warn' (shown in audit.md)."""
    src = {b["id"]: b["text"] for b in base.all_bullets(res)}
    if res.get("summary"):
        src["SUMMARY"] = res["summary"]
    issues = []
    for where, item, scope in _items(doc):
        t = item["text"].strip()
        t = re.sub(r"\s*[—–]\s*", ", ", t).rstrip(".").strip()   # house style: no em dashes, no periods
        item["text"] = t
        if where == "summary" and "SUMMARY" not in item["sources"] and "SUMMARY" in src:
            item["sources"].append("SUMMARY")
        cited = [s for s in item["sources"] if s in src]
        bad = [s for s in item["sources"] if s not in src]
        if bad:
            issues.append(_issue(where, "fix", "unknown source", f"cites {', '.join(bad)}, which aren't on the base resume"))
        if scope and any(not s.startswith(scope + ".") for s in cited):
            issues.append(_issue(where, "fix", "cross-role source",
                                 f"cites {', '.join(s for s in cited if not s.startswith(scope + '.'))} from another role"))
        if not cited:
            issues.append(_issue(where, "fix", "no source", "cites no base-resume bullet"))
            continue
        source_text = " ".join(src[s] for s in cited)
        allowed = _nums(source_text) | ({str(int(years)), str(round(years))} if where == "summary" else set())
        new_nums = sorted(n for n in _nums(t) if n not in allowed)
        if new_nums:
            issues.append(_issue(where, "fix", "number not in source", f"{', '.join(new_nums)} not in {', '.join(cited)}"))
        tw, sw = _words(t), _words(source_text)
        for group, verbs in VERB_GROUPS.items():
            if tw & verbs and not sw & verbs:
                issues.append(_issue(where, "fix", "ownership upgrade",
                                     f"says '{sorted(tw & verbs)[0]}' but the sources never say {group}"))
        if BANNED.search(t):
            issues.append(_issue(where, "warn", "style", f"uses '{BANNED.search(t).group(0)}'"))
        if FIRST_PERSON.search(t) and where != "summary":
            issues.append(_issue(where, "warn", "style", "first person"))
    # Skills: anything not on the base resume is dropped outright.
    known = base.as_text(res, with_ids=False)
    for g in doc["skills"]:
        keep = [s for s in g["items"] if base.skill_rx(s).search(known)]
        for s in g["items"]:
            if s not in keep:
                issues.append(_issue("skills", "removed", "skill not on resume", f"dropped '{s}' from {g['group']}"))
        g["items"] = keep
    doc["skills"] = [g for g in doc["skills"] if g["items"]]
    return issues


def _issue(where, severity, kind, detail):
    return {"where": where, "severity": severity, "kind": kind, "detail": detail}


def _find(doc: dict, where: str) -> dict | None:
    return next((item for w, item, _ in _items(doc) if w == where), None)


def repair(doc: dict, res: dict, issues: list[dict], model: str) -> None:
    src = {b["id"]: b["text"] for b in base.all_bullets(res)}
    blocks = []
    for where in dict.fromkeys(i["where"] for i in issues):
        item = _find(doc, where)
        scope = where.split("#")[0]
        pool = [f"    [{k}] {v}" for k, v in src.items() if where == "summary" or k.startswith(scope + ".")]
        problems = "; ".join(i["detail"] for i in issues if i["where"] == where)
        blocks.append(f"## {where}\nBullet: {item['text']}\nCited: {', '.join(item['sources'])}\n"
                      f"Problem: {problems}\nBullets it may cite:\n" + "\n".join(pool))
    out = ask_json(REPAIR_PROMPT.format(truth=TRUTH_RULES, items="\n\n".join(blocks)), REPAIR_SCHEMA, model)
    for f in out["fixes"]:
        item = _find(doc, f["where"])
        if item is not None:
            item.update(text=f["text"], sources=list(f["sources"]), repaired=True)


def fallback(doc: dict, res: dict, issues: list[dict]) -> None:
    """Last resort for bullets that still fail: your original wording of the first valid source."""
    src = {b["id"]: b["text"] for b in base.all_bullets(res)}
    for where in dict.fromkeys(i["where"] for i in issues):
        item = _find(doc, where)
        if where == "summary":
            item.update(text=res.get("summary") or "", sources=["SUMMARY"] if res.get("summary") else [], reverted=True)
            continue
        scope = where.split("#")[0]
        ok = [s for s in item["sources"] if s in src and s.startswith(scope + ".")]
        if ok:
            item.update(text=src[ok[0]], sources=[ok[0]], reverted=True)
        else:
            item.update(text="", sources=[], reverted=True)   # nothing to fall back on: removed below
    for sec in ("experience", "projects"):
        for e in doc[sec]:
            e["bullets"] = [b for b in e["bullets"] if b["text"]]


# ---------------------------------------------------------------- render
# (font pt, line height, page margin in, gap between sections pt)
DENSITIES = [(10.5, 1.28, 0.6, 9), (10, 1.22, 0.5, 7), (9.5, 1.18, 0.45, 6), (9, 1.15, 0.4, 5)]


def _month(d: str) -> str:
    if not d or d.lower() in ("present", "current"):
        return "Present"
    m = re.match(r"(\d{4})-(\d{2})", d)
    if not m:
        return d
    return date(int(m.group(1)), int(m.group(2)), 1).strftime("%b %Y")


def _contact(ident: dict) -> list[str]:
    strip = lambda u: re.sub(r"^https?://(www\.)?", "", u).rstrip("/")
    return [x for x in [ident.get("location"), ident.get("email"), ident.get("phone"),
                        strip(ident.get("linkedin") or ""), strip(ident.get("github") or ""),
                        strip(ident.get("website") or "")] if x]


def render_html(doc: dict, density) -> str:
    pt, lh, margin, gap = density
    e = html.escape
    parts = [f"<h1>{e(doc['identity'].get('name', ''))}</h1>",
             f"<div class='contact'>{' &nbsp;|&nbsp; '.join(e(x) for x in _contact(doc['identity']))}</div>"]
    if doc["summary"].get("text"):
        parts.append(f"<h2>Summary</h2><p>{e(doc['summary']['text'])}</p>")
    parts.append("<h2>Experience</h2>")
    for r in doc["experience"]:
        parts.append(f"<div class='role'><div class='line'><span><b>{e(r['title'])}</b>, {e(r['company'])}"
                     + (f" <span class='muted'>· {e(r['location'])}</span>" if r.get("location") else "")
                     + f"</span><span class='dates'>{_month(r['start'])} – {_month(r['end'])}</span></div>"
                     + "<ul>" + "".join(f"<li>{e(b['text'])}</li>" for b in r["bullets"]) + "</ul></div>")
    if doc["projects"]:
        parts.append("<h2>Projects</h2>")
        for p in doc["projects"]:
            parts.append(f"<div class='role'><div class='line'><span><b>{e(p['name'])}</b>"
                         + (f" <span class='muted'>· {e(p['subtitle'])}</span>" if p.get("subtitle") else "")
                         + "</span></div><ul>" + "".join(f"<li>{e(b['text'])}</li>" for b in p["bullets"]) + "</ul></div>")
    if doc["skills"]:
        parts.append("<h2>Skills</h2>" + "".join(f"<p class='skill'><b>{e(g['group'])}:</b> {e(', '.join(g['items']))}</p>"
                                                 for g in doc["skills"]))
    if doc["education"]:
        parts.append("<h2>Education</h2>")
        for ed in doc["education"]:
            parts.append(f"<div class='line'><span><b>{e(ed.get('degree', ''))}</b>, {e(ed.get('school', ''))}"
                         + (f" <span class='muted'>· {e(ed['details'])}</span>" if ed.get("details") else "")
                         + f"</span><span class='dates'>{e(ed.get('dates', ''))}</span></div>")
    if doc["certifications"]:
        parts.append("<h2>Certifications</h2><p>" + e("; ".join(doc["certifications"])) + "</p>")
    for o in doc["other"]:
        parts.append(f"<h2>{e(o['title'])}</h2>" + "".join(f"<p>{e(x)}</p>" for x in o["lines"]))
    css = f"""@page{{size:Letter;margin:{margin}in}}*{{box-sizing:border-box}}
body{{margin:0;font-family:Helvetica,Arial,sans-serif;font-size:{pt}pt;line-height:{lh};color:#111}}
h1{{font-size:{pt * 1.9:.1f}pt;margin:0;letter-spacing:.2px}}.contact{{color:#333;margin:2pt 0 {gap - 2}pt}}
h2{{font-size:{pt * 1.08:.1f}pt;text-transform:uppercase;letter-spacing:.8px;border-bottom:.8pt solid #444;
margin:{gap}pt 0 3pt;padding-bottom:1pt}}p{{margin:0 0 2pt}}.role{{margin-bottom:{gap * .55:.1f}pt}}
.line{{display:flex;justify-content:space-between;gap:10pt}}.dates{{white-space:nowrap;color:#222}}.muted{{color:#444}}
ul{{margin:1.5pt 0 0;padding-left:13pt}}li{{margin:0 0 1.2pt}}.skill{{margin-bottom:1.2pt}}"""
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>"
            f"{e(doc['identity'].get('name', 'Resume'))}</title><style>{css}</style></head><body>{''.join(parts)}</body></html>")


def render_md(doc: dict) -> str:
    lines = [f"# {doc['identity'].get('name', '')}", " | ".join(_contact(doc["identity"])), ""]
    if doc["summary"].get("text"):
        lines += ["## Summary", doc["summary"]["text"], ""]
    lines.append("## Experience")
    for r in doc["experience"]:
        lines += ["", f"**{r['title']}**, {r['company']}" + (f" · {r['location']}" if r.get("location") else "")
                  + f" ({_month(r['start'])} – {_month(r['end'])})"] + [f"- {b['text']}" for b in r["bullets"]]
    for p in doc["projects"]:
        lines += ["", f"**{p['name']}**" + (f" · {p['subtitle']}" if p.get("subtitle") else "")] + [f"- {b['text']}" for b in p["bullets"]]
    if doc["skills"]:
        lines += ["", "## Skills"] + [f"- **{g['group']}:** {', '.join(g['items'])}" for g in doc["skills"]]
    if doc["education"]:
        lines += ["", "## Education"] + [f"- {ed.get('degree', '')}, {ed.get('school', '')} {ed.get('dates', '')}".rstrip()
                                         for ed in doc["education"]]
    if doc["certifications"]:
        lines += ["", "## Certifications"] + [f"- {c}" for c in doc["certifications"]]
    for o in doc["other"]:
        lines += ["", f"## {o['title']}"] + [f"- {x}" for x in o["lines"]]
    return "\n".join(lines) + "\n"


def render_txt(doc: dict) -> str:
    """Roughly what an ATS parser sees: plain text, no formatting."""
    t = render_md(doc)
    t = re.sub(r"^#+ ", "", t, flags=re.M).replace("**", "")
    return t


def _pdf(browser, html_text: str, path: Path) -> int:
    import pdfplumber
    page = browser.new_page()
    page.set_content(html_text, wait_until="load")
    page.pdf(path=str(path), format="Letter", prefer_css_page_size=True, print_background=False)
    page.close()
    with pdfplumber.open(path) as pdf:
        return len(pdf.pages)


def _launch(p):
    try:
        return p.chromium.launch(channel="chrome", headless=True)
    except Exception:  # noqa: BLE001  Chrome not installed: Playwright's own Chromium
        return p.chromium.launch(headless=True)


def fit_and_render(doc: dict, path: Path, max_pages: int) -> list[str]:
    """Render to PDF within max_pages. Returns the bullets that had to be dropped (least relevant first)."""
    from playwright.sync_api import sync_playwright
    dropped = []
    with sync_playwright() as p:
        browser = _launch(p)
        for d in DENSITIES:
            if _pdf(browser, render_html(doc, d), path) <= max_pages:
                browser.close()
                return dropped
        for _ in range(40):
            victim = _trim_target(doc)
            if victim is None:
                break
            dropped.append(f"{victim['where']}: {victim['bullet']['text']}")
            if _pdf(browser, render_html(doc, DENSITIES[-1]), path) <= max_pages:
                break
        browser.close()
    return dropped


def _trim_target(doc: dict) -> dict | None:
    """Drop one bullet: projects first, then the last (least relevant) bullet of whichever role has
    the most, preferring older roles. Every role keeps at least one bullet, the newest two."""
    for p in reversed(doc["projects"]):
        b = p["bullets"].pop()
        if not p["bullets"]:
            doc["projects"].remove(p)
        return {"where": p["id"], "bullet": b}
    floor = lambda i: 2 if i == 0 else 1
    cands = [(len(r["bullets"]) - floor(i), i) for i, r in enumerate(doc["experience"]) if len(r["bullets"]) > floor(i)]
    if not cands:
        return None
    _, i = max(cands)
    r = doc["experience"][i]
    return {"where": f"{r['id']}#{len(r['bullets'])}", "bullet": r["bullets"].pop()}


# ---------------------------------------------------------------- reports
def _analysis_md(job: Job, target: str, draft: dict) -> str:
    a = draft["analysis"]
    lines = [f"# {job.title} at {job.company}", f"Judged as: {target} · role type: {a['role_type']}", "",
             "## Core themes", "| Theme | Importance | Coverage | Evidence |", "|---|---|---|---|"]
    lines += [f"| {t['theme']} | {t['importance']} | {t['coverage']} | {', '.join(t['evidence']) or '—'} |"
              for t in a["core_themes"]]
    lines += ["", "## ATS keywords", ", ".join(a["keywords"]), "", "## Gaps"] + [f"- {g}" for g in a["gaps"] or ["none"]]
    lines += ["", "## Questions that would strengthen this application"] + [f"- {q}" for q in a["questions"] or ["none"]]
    lines += ["", "## What changed"] + [f"- {c}" for c in draft.get("changes") or []]
    return "\n".join(lines) + "\n"


def _questions_md(job: Job, draft: dict, n_direct: int) -> str:
    a = draft["analysis"]
    return (f"# Before tailoring for {job.title} at {job.company}\n\n"
            f"Only {n_direct} of this role's core themes have direct evidence on your base resume "
            f"(at least {MIN_DIRECT_THEMES} needed), so no resume was generated.\n\n"
            "Options: add the missing experience to your base resume (then `jsb resume parse`), "
            "or run `jsb tailor <#> --anyway`.\n\n## Gaps\n" + "\n".join(f"- {g}" for g in a["gaps"])
            + "\n\n## Questions\n" + "\n".join(f"- {q}" for q in a["questions"]) + "\n")


def _audit_md(doc: dict, res: dict, issues: list[dict], trimmed: list[str]) -> str:
    src = {b["id"]: b["text"] for b in base.all_bullets(res)}
    src["SUMMARY"] = res.get("summary") or ""
    fixed = sum(1 for _, it, _ in _items(doc) if it.get("repaired") and not it.get("reverted"))
    reverted = sum(1 for _, it, _ in _items(doc) if it.get("reverted"))
    lines = ["# Truth audit", "",
             f"- Bullets: {sum(len(r['bullets']) for r in doc['experience'])} experience, "
             f"{sum(len(p['bullets']) for p in doc['projects'])} project",
             f"- Repaired by Claude: {fixed} · reverted to your original wording: {reverted}",
             f"- Dropped to fit the page: {len(trimmed)}", ""]
    if issues:
        lines += ["## Findings", "| Where | Severity | Check | Detail |", "|---|---|---|---|"]
        lines += [f"| {i['where']} | {i['severity']} | {i['kind']} | {i['detail']} |" for i in issues]
        lines.append("")
    lines.append("## Every bullet and its sources")
    for where, item, _ in _items(doc):
        tag = " *(repaired)*" if item.get("repaired") else ""
        tag = " *(your original wording)*" if item.get("reverted") else tag
        lines += ["", f"**{where}**{tag}: {item['text']}"]
        lines += [f"  - `{s}` {src.get(s, '(unknown)')}" for s in item["sources"]]
    if trimmed:
        lines += ["", "## Dropped to fit the page"] + [f"- {t}" for t in trimmed]
    if doc.get("changes"):
        lines += ["", "## Changes Claude made"] + [f"- {c}" for c in doc["changes"]]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- cover letter
def write_cover_letter(job: Job, res: dict, out: Path, model: str, pdf_name: str) -> Path:
    prompt = COVER_PROMPT.format(truth=TRUTH_RULES, title=job.title, company=job.company,
                                 description=job.description[:12000], resume=base.as_text(res))
    letter = ask_json(prompt, COVER_SCHEMA, model)
    src = {b["id"]: b["text"] for b in base.all_bullets(res)}
    problems = []
    for i, para in enumerate(letter["paragraphs"], 1):
        para["text"] = re.sub(r"\s*[—–]\s*", ", ", para["text"])
        cited = " ".join(src.get(s, "") for s in para["sources"])
        new = sorted(n for n in _nums(para["text"]) if n not in _nums(cited))
        if new:
            problems.append(f"paragraph {i}: numbers {', '.join(new)} aren't in its sources")
    if problems:   # one retry with the problems spelled out
        letter = ask_json(prompt + "\n\nA previous draft failed these checks; avoid them:\n" + "\n".join(problems),
                          COVER_SCHEMA, model)
    ident = res["identity"]
    e = html.escape
    body = "".join(f"<p>{e(p['text'])}</p>" for p in letter["paragraphs"])
    page = (f"<!doctype html><html><head><meta charset='utf-8'><style>@page{{size:Letter;margin:0.9in}}"
            f"body{{font-family:Helvetica,Arial,sans-serif;font-size:11pt;line-height:1.45;color:#111}}"
            f"h1{{font-size:16pt;margin:0}}.c{{color:#333;margin-bottom:22pt}}p{{margin:0 0 11pt}}</style></head><body>"
            f"<h1>{e(ident.get('name', ''))}</h1><div class='c'>{' | '.join(e(x) for x in _contact(ident))}</div>"
            f"<p>{date.today().strftime('%B %-d, %Y')}</p><p>Dear {e(job.company)} hiring team,</p>{body}"
            f"<p>Sincerely,<br>{e(ident.get('name', ''))}</p></body></html>")
    (out / "cover_letter.md").write_text("\n\n".join(p["text"] for p in letter["paragraphs"]) + "\n\nSources: "
                                         + "; ".join(", ".join(p["sources"]) for p in letter["paragraphs"]) + "\n")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = _launch(p)
        _pdf(browser, page, out / pdf_name)
        browser.close()
    return out / pdf_name


def find_pdf(output_rel: str) -> Path | None:
    folder = ROOT / output_rel
    pdfs = sorted(p for p in folder.glob("*_Resume.pdf")) or sorted(folder.glob("*.pdf"))
    return pdfs[0] if pdfs else None
