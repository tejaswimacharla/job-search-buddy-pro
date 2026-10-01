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
{guidance}
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
credential, degree or domain the candidate clearly lacks, far more years than they have). Keep
each list item under 12 words.

# Candidate (target: {target}; {years} years total experience)
{candidate_note}
{profile}

# Job: {title} at {company} ({location})
{description}
"""

# Track-specific judging notes, so a data science role isn't scored by an SWE rubric.
GUIDANCE = {
    "swe": """- Score the match on what the role is really about: its domain, the kind of systems it builds,
  seniority, and its primary stack. Don't treat every tool named in the posting as a requirement.
- Give real credit for transferable experience. A strong engineer who has done the same kind of
  work with a different tool is a good match: AWS CDK for Terraform, AWS for GCP/Azure, Java/Kotlin
  for another JVM or typed language, one message queue or datastore for another, internal
  platforms for similar products. Treat these as small gaps, not missing requirements.
- Skills under "Listed in the skills section only" were very likely used day to day in those
  roles (e.g. EC2, S3, CloudWatch for a multi-year AWS engineer); count them as working familiarity.
- The big gaps are domain or specialty ones a transferable skill can't cover: e.g. a role that is
  mostly research/model training, mobile, frontend, hardware, or a regulated domain the posting
  requires prior experience in.""",

    "ds": """- This is a data science / applied science / analytics role. First decide which kind it is,
  because they reward different evidence:
  * ML / applied science (building, shipping and evaluating models: ranking, recommendations,
    NLP, forecasting, LLM applications): production ML, model evaluation, feature pipelines and
    strong Python/SQL count heavily. Engineering depth (serving, pipelines, scale) is a real
    strength here, not a side skill.
  * Product analytics / experimentation (metrics, A/B tests, causal inference, stakeholder
    insight): look for evidence of experiment design, statistics, SQL analysis and influencing
    product decisions. Building the systems others analyze is related but not the same work;
    treat it as a moderate gap unless the candidate has analysis/decision work on record.
  * Data engineering (batch and streaming pipelines, Spark/Flink/Kafka, warehouses and
    lakehouses, orchestration, data modeling, data quality): backend and distributed-systems
    experience transfers strongly, especially building pipelines, event-driven services or
    storage at scale. Real gaps are heavy dimensional modeling for BI, or a specific stack the
    posting insists on with nothing similar on record; one warehouse or orchestrator for another
    is a small gap.
  * Analytics engineering (dbt, SQL modeling, metrics layers, BI): SQL and data-modeling
    evidence matter most; pipeline engineering is related but not the core.
- Give real credit for transferable experience: one ML framework for another, Spark for another
  distributed engine, BigQuery for Snowflake, R for Python in analysis work, an LLM evaluation
  harness for an offline model-evaluation framework, metric dashboards built for a service for
  product metrics work.
- Skills under "Listed in the skills section only" were very likely used day to day; count
  them as working familiarity.
- Judge seniority by years of data/ML/analysis work, not total engineering years. A senior
  engineer moving into DS may fit mid-level DS better; say so in the gaps instead of skipping.
- The big gaps a transferable skill can't cover: a required PhD or publication record, deep
  statistics (causal inference, Bayesian modeling, survey methods) with no evidence, a regulated
  or specialist domain (biostatistics, clinical, actuarial, quant finance), or work that is
  mostly research rather than applied.""",
}


def rerank(job: Job, profile: Profile, model: str = "sonnet", runs: int | None = None,
           track: str = "swe", family: str | None = None) -> dict:
    """Score `runs` times in parallel and average them. A single run varies by up to ~15 points,
    which is too noisy for a 75% / 85% application cutoff. The explanation comes from the run
    closest to the average."""
    import concurrent.futures as cf
    from .config import load_config
    cfg = load_config()
    if runs is None:
        runs = int(cfg.get("rerank_runs", 2))
    from .config import track_target
    target = track_target(cfg, track, family)
    with cf.ThreadPoolExecutor(runs) as ex:
        results = [f.result() for f in [ex.submit(_rerank_once, job, profile, model, track, target)
                                        for _ in range(runs)]]
    scores = sorted(r["score"] for r in results)
    avg = round(sum(scores) / len(scores))
    best = dict(min(results, key=lambda r: abs(r["score"] - avg)))
    best["score"] = avg
    best["verdict"] = "apply" if avg >= 75 else "maybe" if avg >= 60 else "skip"
    best["runs"] = scores
    return best


def _rerank_once(job: Job, profile: Profile, model: str, track: str = "swe",
                 target: str = "Senior Software Engineer") -> dict:
    from .config import candidate_note, load_config
    prompt = PROMPT.format(guidance=GUIDANCE.get(track, GUIDANCE["swe"]), target=target,
                           candidate_note=candidate_note(load_config(), profile),
                           years=profile.years_experience, profile=profile.summary_for_llm,
                           title=job.title, company=job.company, location=job.location,
                           description=job.description[:12000])
    return ask_json(prompt, SCHEMA, model)


def ask_json(prompt: str, schema: dict, model: str = "sonnet", timeout: int = 300) -> dict:
    """One structured-output call through the local `claude` CLI (your Claude Code login), no tools."""
    proc = subprocess.run(
        ["claude", "-p", "--output-format", "json", "--tools", "", "--model", model,
         "--no-session-persistence", "--json-schema", json.dumps(schema)],
        input=prompt, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout[:500])
    out = json.loads(proc.stdout)
    if out.get("is_error") or not out.get("structured_output"):
        raise RuntimeError(out.get("result", "no structured output"))
    return out["structured_output"]
