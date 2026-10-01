"""Read the candidate profile live from the Resume Tailoring Assistant knowledge base.

Nothing about the candidate is duplicated in this project: identity comes from
config/candidate_profile.md, experience from knowledge_base/experience_records/, and
skill evidence from knowledge_base/skill_evidence/skill_evidence_matrix.md.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

DEPTH_WEIGHT = {"strong": 1.0, "working-strong": 0.85, "working": 0.7}
EXPOSURE_WEIGHT = 0.3  # declared or unconfirmed: counts a little for matching, never for a resume


@dataclass
class SkillRow:
    text: str        # the whole row, lower-cased, used for alias matching
    weight: float
    eligible: bool


@dataclass
class Profile:
    identity: dict[str, str]
    years_experience: float
    skill_rows: list[SkillRow]
    exposure_text: str             # skills declared on old resumes but never demonstrated
    narrative_text: str            # role bundles + experience records, for theme matching
    summary_for_llm: str
    experiences: list[dict] = field(default_factory=list)


def load_profile(rta: Path) -> Profile:
    identity = _identity(rta / "config" / "candidate_profile.md")
    experiences = _experiences(rta / "knowledge_base" / "experience_records")
    rows, exposure = _skills(rta / "knowledge_base" / "skill_evidence" / "skill_evidence_matrix.md")
    bundles = sorted((rta / "knowledge_base" / "role_bundles").glob("*.md"))
    narrative = "\n".join(p.read_text() for p in bundles)
    narrative += "\n".join(e["body"] for e in experiences)
    return Profile(
        identity=identity,
        years_experience=_years(experiences),
        skill_rows=rows,
        exposure_text=exposure.lower(),
        narrative_text=narrative.lower(),
        summary_for_llm=_llm_summary(rta, identity, experiences, bundles),
        experiences=experiences,
    )


def _identity(path: Path) -> dict[str, str]:
    text = path.read_text()
    section = text.split("## Identity", 1)[1].split("\n## ", 1)[0]
    out = {}
    for m in re.finditer(r"^- ([A-Za-z /]+):\s*(.+)$", section, re.M):
        key = m.group(1).strip().lower().split("/")[0].strip()
        val = m.group(2).strip()
        if not val.lower().startswith(("none", "[tbd")):
            out[key] = val
    name = out.get("name", "")
    if name:
        out["first_name"], _, out["last_name"] = name.partition(" ")
    return out


def _experiences(folder: Path) -> list[dict]:
    exps = []
    for p in sorted(folder.glob("*.md")):
        if p.name.startswith("TEMPLATE"):
            continue
        text = p.read_text()
        fm = dict(re.findall(r"^(\w+):\s*(.+)$", text.split("\n---", 2)[0] if text.startswith("---") else text[:800], re.M))
        exps.append({"company": fm.get("company", ""), "title": fm.get("title", ""),
                     "start": fm.get("start_date", ""), "end": fm.get("end_date", ""), "body": text})
    exps.sort(key=lambda e: e["start"], reverse=True)
    return exps


def _years(exps: list[dict]) -> float:
    months = 0
    for e in exps:
        try:
            sy, sm = map(int, e["start"].split("-")[:2])
            if e["end"].strip().lower() == "present":
                ey, em = date.today().year, date.today().month
            else:
                ey, em = map(int, e["end"].split("-")[:2])
            months += max(0, (ey - sy) * 12 + (em - sm) + 1)
        except ValueError:
            continue
    return round(months / 12, 1)


def _skills(path: Path) -> tuple[list[SkillRow], str]:
    text = path.read_text()
    rows = []
    for line in text.splitlines():
        if not line.startswith("|") or line.startswith("|---") or line.startswith("| Skill"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 6:
            continue
        depth, eligible = cells[4].lower(), cells[5].lower().startswith("true")
        weight = next((w for k, w in DEPTH_WEIGHT.items() if depth.startswith(k)), EXPOSURE_WEIGHT)
        rows.append(SkillRow(text=" ".join(cells[:3]).lower(), weight=weight if eligible else EXPOSURE_WEIGHT,
                             eligible=eligible))
    note = text.split("**Note:**", 1)[1] if "**Note:**" in text else ""
    return rows, note


def _llm_summary(rta: Path, identity: dict, exps: list[dict], bundles: list[Path]) -> str:
    """Candidate brief for LLM re-ranking: each role with its achievements (action, approach,
    outcome, technologies) from the achievement bank, then the skill evidence."""
    kb = rta / "knowledge_base"
    bank = {p.stem.split("-")[0]: p.read_text() for p in (kb / "achievement_bank").glob("*.md")
            if not p.name.startswith("TEMPLATE")}
    parts = []
    for e in exps:
        parts.append(f"\n## {e['title']} — {e['company']} ({e['start']} to {e['end']})")
        text = next((t for k, t in bank.items() if k and k in e["company"].lower().replace(",", "")), "")
        for block in re.split(r"\n## ", text)[1:]:
            head = block.splitlines()[0]
            if not re.match(r"ACH-", head):
                continue
            fields = dict(re.findall(r"^- \*\*(Action|Technical approach|Outcome|Technologies|Scope|Ownership level)[^:]*:\*\*\s*(.+?)(?=\n- \*\*|\n\n|\Z)",
                                     block, re.S | re.M))
            clean = {k: re.sub(r"\s+", " ", v).strip()[:320] for k, v in fields.items()}
            line = f"- {re.sub(r'[(（].*?(resolved|NEW|enriched|merged).*?[)）]', '', head).strip(' —-')}"
            for k in ("Action", "Technical approach", "Outcome", "Scope", "Ownership level", "Technologies"):
                if clean.get(k):
                    line += f"\n    {k}: {clean[k]}"
            parts.append(line)
    matrix = (kb / "skill_evidence" / "skill_evidence_matrix.md").read_text()
    rows = [[c.strip() for c in l.strip("|").split("|")] for l in matrix.splitlines()
            if l.startswith("| ") and not l.startswith("| Skill") and not l.startswith("|---")]
    strong = [f"{r[0]} ({r[4]}, {r[2]})" for r in rows if len(r) > 5 and r[5].lower().startswith("true")]
    weak = [r[0] for r in rows if len(r) > 5 and not r[5].lower().startswith("true") and "rejected" not in r[5].lower()]
    note = matrix.split("**Note:**", 1)[1] if "**Note:**" in matrix else ""
    declared = re.search(r"\(e\.g\.,?(.*?)\)", note, re.S)
    parts.append("\n## Skills with documented evidence\n" + ", ".join(strong))
    parts.append("\n## Also listed on the candidate's resumes (day-to-day usage likely, not individually documented)\n"
                 + ", ".join(weak) + (", " + re.sub(r"\s+", " ", declared.group(1)).strip() if declared else ""))
    return "\n".join(parts)
