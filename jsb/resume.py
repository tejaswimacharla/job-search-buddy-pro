"""Your base resume: find it, read its text, and parse it (once, cached) into a structured profile.

Every bullet gets a stable ID (E1.B3 = role 1, bullet 3; P2.B1 = project 2, bullet 1). Tailored
resumes must cite these IDs for every bullet, which is what makes the truth audit possible.
"""
from __future__ import annotations

import hashlib
import json
import re
import zipfile
from pathlib import Path

from .config import DATA_DIR, ROOT, RESUME_DIR

SUPPORTED = (".pdf", ".docx", ".md", ".txt", ".tex")
CACHE = DATA_DIR / "resume_profile.json"


def candidates() -> list[Path]:
    """Resume files in resume/ (its README isn't one)."""
    if not RESUME_DIR.exists():
        return []
    return sorted(p for p in RESUME_DIR.glob("*")
                  if p.is_file() and p.suffix.lower() in SUPPORTED and p.name.lower() != "readme.md")


def base_path(cfg: dict) -> Path:
    if cfg["resume"].get("base"):
        p = (ROOT / cfg["resume"]["base"]).resolve()
        if not p.exists():
            raise SystemExit(f"Base resume not found: {p}. Fix `resume.base` in config/search.yaml.")
        return p
    found = candidates()
    if len(found) == 1:
        return found[0]
    if not found:
        raise SystemExit(f"No base resume yet. Put one in {RESUME_DIR}/ (PDF, DOCX, Markdown or text), "
                         f"or run `jsb resume use <file>`.")
    raise SystemExit(f"Several files in {RESUME_DIR}/: {', '.join(p.name for p in found)}. "
                     f"Set `resume.base` in config/search.yaml to pick one.")


def extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
            # "LinkedIn" / "GitHub" are often link text whose URL isn't printed; keep the targets.
            links = dict.fromkeys(h["uri"] for page in pdf.pages for h in page.hyperlinks
                                  if (h.get("uri") or "").startswith("http"))
        if links:
            text += "\n\nLinks in the document: " + " ".join(links)
    elif ext == ".docx":
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf8")
        paras = re.findall(r"<w:p[ >].*?</w:p>", xml, re.S)
        text = "\n".join("".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", p)) for p in paras)
    else:
        text = path.read_text()
    text = text.replace(" ", " ")
    if len(text.strip()) < 200:
        raise SystemExit(f"Couldn't read much text from {path.name} ({len(text.strip())} characters). "
                         "If it's a scanned PDF, export a text-based PDF, DOCX or Markdown version instead.")
    return text


PARSE_SCHEMA = {
    "type": "object",
    "properties": {
        "identity": {"type": "object", "properties": {
            k: {"type": "string"} for k in ("name", "email", "phone", "linkedin", "github", "website", "location")}},
        "summary": {"type": "string"},
        "experience": {"type": "array", "items": {"type": "object", "properties": {
            "company": {"type": "string"}, "title": {"type": "string"}, "location": {"type": "string"},
            "start": {"type": "string", "description": "YYYY-MM (YYYY-01 if only a year is given)"},
            "end": {"type": "string", "description": "YYYY-MM, or 'present'"},
            "bullets": {"type": "array", "items": {"type": "string"}}},
            "required": ["company", "title", "start", "end", "bullets"]}},
        "projects": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "subtitle": {"type": "string"},
            "bullets": {"type": "array", "items": {"type": "string"}}}, "required": ["name", "bullets"]}},
        "skills": {"type": "array", "items": {"type": "object", "properties": {
            "group": {"type": "string"}, "items": {"type": "array", "items": {"type": "string"}}},
            "required": ["group", "items"]}},
        "education": {"type": "array", "items": {"type": "object", "properties": {
            "school": {"type": "string"}, "degree": {"type": "string"}, "dates": {"type": "string"},
            "details": {"type": "string"}}, "required": ["school", "degree"]}},
        "certifications": {"type": "array", "items": {"type": "string"}},
        "other": {"type": "array", "items": {"type": "object", "properties": {
            "title": {"type": "string"}, "lines": {"type": "array", "items": {"type": "string"}}},
            "required": ["title", "lines"]}},
    },
    "required": ["identity", "experience", "skills", "education"],
}

PARSE_PROMPT = """Convert this resume into structured data. Copy text exactly; do not reword,
summarize, merge, fix or add anything.

- identity: as written. linkedin/github as full URLs if the resume gives a handle or path.
- experience: every role, newest first. If one company lists several titles, make one entry per
  title. Each bullet is one achievement line exactly as written (join lines a PDF wrapped, drop
  the bullet glyph).
- projects: only if the resume has a projects section.
- skills: the skills section, keeping its groups (use "Skills" if it has none). One item per
  skill; split "AWS (EC2, S3)" into "AWS", "EC2", "S3".
- education, certifications: as written. Anything else (awards, publications, patents) goes in
  `other`.
- Use "" for anything not on the resume.

Resume text:
{text}
"""


def parsed(cfg: dict, refresh: bool = False, quiet: bool = False) -> dict:
    """The structured base resume. Re-parsed only when the file changes (or with refresh=True)."""
    path = base_path(cfg)
    text = extract_text(path)
    sha = hashlib.sha256(text.encode()).hexdigest()
    if not refresh and CACHE.exists():
        cached = json.loads(CACHE.read_text())
        if cached.get("sha") == sha:
            return cached["resume"]
    from .llm import ask_json
    if not quiet:
        print(f"Reading your base resume ({path.name}) with Claude, once per version (~30s)...")
    raw = ask_json(PARSE_PROMPT.format(text=text), PARSE_SCHEMA, cfg["resume"]["model"], timeout=600)
    res = _with_ids(raw)
    res["source"] = {"file": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
                     "text": text, "not_verbatim": _not_verbatim(res, text)}
    DATA_DIR.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps({"sha": sha, "resume": res}, indent=1))
    return res


def _with_ids(raw: dict) -> dict:
    """Give every role, project and bullet a stable ID and tie each skill to the bullets that use it."""
    res = dict(raw)
    res["experience"] = [dict(e, id=f"E{i}", bullets=[{"id": f"E{i}.B{j}", "text": b.strip()}
                                                      for j, b in enumerate(e.get("bullets") or [], 1) if b.strip()])
                         for i, e in enumerate(raw.get("experience") or [], 1)]
    res["projects"] = [dict(p, id=f"P{i}", bullets=[{"id": f"P{i}.B{j}", "text": b.strip()}
                                                    for j, b in enumerate(p.get("bullets") or [], 1) if b.strip()])
                       for i, p in enumerate(raw.get("projects") or [], 1)]
    bullets = all_bullets(res)
    res["skill_evidence"] = {item: [b["id"] for b in bullets if skill_rx(item).search(b["text"])]
                             for g in res.get("skills") or [] for item in g["items"]}
    return res


def all_bullets(res: dict) -> list[dict]:
    return [b for sec in ("experience", "projects") for e in res.get(sec) or [] for b in e["bullets"]]


def skill_rx(name: str) -> re.Pattern:
    """Whole-word, case-insensitive match for a skill name ("Go" won't match "good", "C++" works)."""
    return re.compile(rf"(?<![\w+#.]){re.escape(name.strip())}(?![\w+#])", re.I)


def _squash(s: str) -> str:
    return re.sub(r"[^a-z0-9%$]", "", s.lower())


def _not_verbatim(res: dict, text: str) -> list[str]:
    """Bullet IDs whose text isn't in the source file (the parse is supposed to copy exactly)."""
    src = _squash(text)
    return [b["id"] for b in all_bullets(res) if _squash(b["text"])[:60] not in src]


def as_text(res: dict, with_ids: bool = True) -> str:
    """The parsed resume as plain text, for Claude's prompts."""
    tag = (lambda x: f"[{x['id']}] ") if with_ids else (lambda x: "")
    out = []
    if res.get("summary"):
        out.append(f"SUMMARY\n{res['summary']}")
    out.append("EXPERIENCE")
    for e in res["experience"]:
        out.append(f"\n{tag(e)}{e['title']} | {e['company']} | {e.get('location', '')} | {e['start']} to {e['end']}")
        out += [f"  - {tag(b)}{b['text']}" for b in e["bullets"]]
    if res.get("projects"):
        out.append("\nPROJECTS")
        for p in res["projects"]:
            out.append(f"\n{tag(p)}{p['name']}" + (f" | {p['subtitle']}" if p.get("subtitle") else ""))
            out += [f"  - {tag(b)}{b['text']}" for b in p["bullets"]]
    out.append("\nSKILLS")
    out += [f"  {g['group']}: {', '.join(g['items'])}" for g in res.get("skills") or []]
    out.append("\nEDUCATION")
    out += [f"  {ed['degree']}, {ed['school']} {ed.get('dates', '')} {ed.get('details', '')}".rstrip()
            for ed in res.get("education") or []]
    if res.get("certifications"):
        out.append("\nCERTIFICATIONS\n  " + "; ".join(res["certifications"]))
    for o in res.get("other") or []:
        out.append(f"\n{o['title'].upper()}\n" + "\n".join(f"  {ln}" for ln in o["lines"]))
    return "\n".join(out)
