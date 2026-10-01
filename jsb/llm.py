"""Optional second-pass ranking with Claude, via the local `claude` CLI (uses your Claude Code login)."""
from __future__ import annotations

import json
import subprocess

from .models import Job
from .profile import Profile

SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "minimum": 0, "maximum": 100},
        "verdict": {"type": "string", "enum": ["apply", "maybe", "skip"]},
        "why": {"type": "string"},
        "strengths": {"type": "array", "items": {"type": "string"}},
        "gaps": {"type": "array", "items": {"type": "string"}},
        "dealbreakers": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["score", "verdict", "why", "strengths", "gaps", "dealbreakers"],
}

PROMPT = """You are an experienced technical recruiter deciding whether this candidate is worth
submitting for this job, the way Jobright or a good agency recruiter scores a match.

How to judge:
- Score the match on what the role is really about: its domain, the kind of systems it builds,
  seniority, and its primary stack. Don't treat every tool named in the posting as a requirement.
- Give real credit for transferable experience. A strong engineer who has done the same kind of
  work with a different tool is a good match: AWS CDK for Terraform, AWS for GCP/Azure, Java/Kotlin
  for another JVM or typed language, one message queue or datastore for another, internal
  platforms for similar products. Treat these as small gaps, not missing requirements.
- Skills under "Also listed on the candidate's resumes" were very likely used day to day in those
  roles (e.g. EC2, S3, CloudWatch for a 4.5-year AWS SDE); count them as working familiarity.
- The big gaps are domain or specialty ones a transferable skill can't cover: e.g. a role that is
  mostly research/model training, mobile, frontend, hardware, or a regulated domain the posting
  requires prior experience in.
- Many postings list more than any one person has. Missing some "preferred"/"nice to have" items
  shouldn't move the score much.

Score 0-100 (how strong a match this is to submit):
- 85-100: core of the role matches directly; would clearly get a recruiter screen
- 75-84: good match; the core fits, with gaps that transferable experience largely covers
- 60-74: plausible stretch; a real gap in the role's core, but related experience
- 40-59: weak; the core of the role is a different specialty
- <40: different field or clear mismatch
Verdict: apply = 75+, maybe = 60-74, skip = below 60.
Dealbreakers: only hard blockers stated in the posting (clearance/citizenship, a required
credential or domain the candidate clearly lacks, far more years than they have). Keep each list
item under 12 words.

# Candidate (target: Senior Software Engineer; {years} years total experience)
Based in Sunnyvale, CA. Open to remote (US), Bay Area onsite/hybrid, or relocating anywhere
in the US, so a US location is never a gap or dealbreaker.
{profile}

# Job: {title} at {company} ({location})
{description}
"""


def rerank(job: Job, profile: Profile, model: str = "sonnet", runs: int | None = None) -> dict:
    """Score `runs` times in parallel and average them. A single run varies by up to ~15 points,
    which is too noisy for a 75% / 85% application cutoff. The explanation comes from the run
    closest to the average."""
    import concurrent.futures as cf
    if runs is None:
        from .config import load_config
        runs = int(load_config().get("rerank_runs", 2))
    with cf.ThreadPoolExecutor(runs) as ex:
        results = [f.result() for f in [ex.submit(_rerank_once, job, profile, model) for _ in range(runs)]]
    scores = sorted(r["score"] for r in results)
    avg = round(sum(scores) / len(scores))
    best = dict(min(results, key=lambda r: abs(r["score"] - avg)))
    best["score"] = avg
    best["verdict"] = "apply" if avg >= 75 else "maybe" if avg >= 60 else "skip"
    best["runs"] = scores
    return best


def _rerank_once(job: Job, profile: Profile, model: str) -> dict:
    prompt = PROMPT.format(years=profile.years_experience, profile=profile.summary_for_llm,
                           title=job.title, company=job.company, location=job.location,
                           description=job.description[:12000])
    proc = subprocess.run(
        ["claude", "-p", "--output-format", "json", "--tools", "", "--model", model,
         "--no-session-persistence", "--json-schema", json.dumps(SCHEMA)],
        input=prompt, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout[:500])
    out = json.loads(proc.stdout)
    if out.get("is_error") or not out.get("structured_output"):
        raise RuntimeError(out.get("result", "no structured output"))
    return out["structured_output"]
