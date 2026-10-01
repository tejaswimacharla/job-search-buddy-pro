"""The candidate profile used for matching, built from your base resume (see resume.py).

Nothing about the candidate is duplicated in this project's config: identity, experience and
skills all come from the parsed resume, cached in data/resume_profile.json.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from . import resume

EXPOSURE_WEIGHT = 0.3   # skills mentioned only in passing; counts a little for matching
# How much a skill counts, by how many resume bullets show it in use.
EVIDENCE_WEIGHT = {0: 0.5, 1: 0.85}   # 0 = only in the skills section; 2+ bullets = 1.0


@dataclass
class SkillRow:
    text: str        # skill name, lower-cased, used for alias matching
    weight: float
    eligible: bool


@dataclass
class Profile:
    identity: dict[str, str]
    years_experience: float
    skill_rows: list[SkillRow]
    exposure_text: str             # kept for the matcher; empty for a resume-based profile
    narrative_text: str            # every bullet + summary, for theme matching
    summary_for_llm: str
    experiences: list[dict] = field(default_factory=list)
    resume: dict = field(default_factory=dict)


def load_profile(cfg: dict) -> Profile:
    res = resume.parsed(cfg)
    ident = {k: v for k, v in (res.get("identity") or {}).items() if v}
    if ident.get("name"):
        ident["first_name"], _, ident["last_name"] = ident["name"].partition(" ")
    rows = [SkillRow(text=skill.lower(), weight=EVIDENCE_WEIGHT.get(len(ids), 1.0), eligible=True)
            for skill, ids in (res.get("skill_evidence") or {}).items()]
    narrative = "\n".join([res.get("summary") or ""] + [b["text"] for b in resume.all_bullets(res)])
    exps = [{"company": e["company"], "title": e["title"], "start": e["start"], "end": e["end"],
             "body": "\n".join(b["text"] for b in e["bullets"])} for e in res["experience"]]
    return Profile(identity=ident, years_experience=_years(exps), skill_rows=rows, exposure_text="",
                   narrative_text=narrative.lower(), summary_for_llm=_llm_summary(res),
                   experiences=exps, resume=res)


def _years(exps: list[dict]) -> float:
    """Total months across roles, counting overlapping roles once."""
    months: set[int] = set()
    for e in exps:
        try:
            sy, sm = map(int, e["start"].split("-")[:2])
            if e["end"].strip().lower() in ("present", "current", ""):
                ey, em = date.today().year, date.today().month
            else:
                ey, em = map(int, e["end"].split("-")[:2])
        except ValueError:
            continue
        months.update(range(sy * 12 + sm, ey * 12 + em + 1))
    return round(len(months) / 12, 1)


def _llm_summary(res: dict) -> str:
    """Candidate brief for Claude's re-rank: the resume, then which skills have bullets behind them."""
    ev = res.get("skill_evidence") or {}
    shown = [s for s, ids in ev.items() if ids]
    listed = [s for s, ids in ev.items() if not ids]
    return (resume.as_text(res, with_ids=False)
            + "\n\n## Skills shown in resume bullets\n" + ", ".join(shown)
            + "\n\n## Listed in the skills section only (day-to-day usage likely, no bullet shows it)\n"
            + ", ".join(listed))
