"""Run: .venv/bin/python tests/test_tracks.py  — title routing and scoring per track, with a
synthetic profile (no RTA knowledge base needed)."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jsb.config import load_config, track_criteria  # noqa: E402
from jsb.match import Matcher  # noqa: E402
from jsb.models import Job  # noqa: E402
from jsb.profile import Profile, SkillRow  # noqa: E402

cfg = load_config()
profile = Profile(
    identity={"name": "Test User"}, years_experience=6.0,
    skill_rows=[SkillRow(text=t, weight=1.0, eligible=True) for t in
                ("python", "sql", "aws", "kubernetes", "distributed systems", "llms", "statistics",
                 "a/b testing experimentation", "pandas numpy", "scikit-learn", "machine learning model")],
    exposure_text="", narrative_text="", summary_for_llm="")
m = Matcher(cfg, profile)

ROUTING = {
    "Senior Software Engineer, Backend": "swe",
    "Site Reliability Engineer": "swe",
    "Machine Learning Engineer": "swe",
    "Software Engineer, Data Science Platform": "swe",
    "Senior Data Scientist": "ds",
    "Data Scientist, Product Analytics": "ds",
    "Applied Scientist II": "ds",
    "Staff Machine Learning Scientist": "ds",
    "Senior Analytics Engineer": "ds",
    "Data Engineer": "ds",
    "Senior Data Engineer, Streaming": "ds",
    "Staff Data Platform Engineer": "ds",
    "Data Infrastructure Engineer": "ds",
    "Big Data Engineer": "ds",
    "ETL Developer": "ds",
    "Data Engineering Lead": "ds",
    "Senior Software Engineer, Data Platform": "swe",   # SWE title on a data team stays swe
    "Platform Engineer": "swe",
    "Data Engineering Solutions Specialist": None,      # pre-sales
    "Data Engineering Manager": None,
    "Senior Product Analyst": "ds",
    "Experimentation Scientist": "ds",
    "Research Scientist": None,           # excluded from both tracks
    "Data Science Manager": None,         # global exclude
    "Data Science Intern": None,
    "Clinical Data Scientist": None,
    "Business Intelligence Analyst": None,
    "Account Executive": None,
}
fails = [(t, want, m.track_for(t)) for t, want in ROUTING.items() if m.track_for(t) != want]
for t, want, got in fails:
    print(f"FAIL routing {t!r}: want {want}, got {got}")

ds_jd = """About the role
We're hiring a Senior Data Scientist to own experimentation for our growth team.

Requirements
- 4+ years of experience in data science or analytics
- Strong SQL and Python (pandas, NumPy, scikit-learn)
- Deep knowledge of statistics, hypothesis testing and A/B testing
- Experience with causal inference methods such as difference-in-differences
- Build predictive models and dashboards in Tableau for product KPIs

Nice to have
- Bayesian methods, R
"""
job = Job(ats="test", company="Acme", ext_id="1", title="Senior Data Scientist, Growth",
          location="San Francisco, CA", url="u", apply_url="u", description=ds_jd,
          posted_at=date.today().isoformat())
res = m.score(job)
checks = [
    (res.passed, "DS job passes prefilter"),
    (res.track == "ds", f"track is ds (got {res.track})"),
    (res.family.startswith("DS ·"), f"DS family (got {res.family})"),
    (res.required_years == 4, f"reads '4+ years of experience in data science' (got {res.required_years})"),
    ("Causal inference" in res.missing, f"causal inference reported missing (missing={res.missing})"),
    ("A/B testing / experimentation" in res.matched, f"A/B testing matched (matched={res.matched})"),
    ("R" not in res.matched, "R not falsely matched"),
]
forced = m.score(Job(ats="test", company="Acme", ext_id="2", title="Quant Researcher (Statistics)",
                     location="NYC, NY", url="u", apply_url="u", description=ds_jd), force=True)
checks.append((forced.passed and forced.track == "ds", f"hand-added unmatched title guesses ds (got {forced.track})"))
dc = track_criteria(cfg, "ds")
checks.append((dc["min_base_pay"] == 180000 and dc["max_posted_hours"] == cfg["apply_criteria"]["max_posted_hours"],
               "ds criteria override pay and inherit the rest"))
checks.append((track_criteria(cfg, "swe") == cfg["apply_criteria"], "swe criteria unchanged"))
checks.append(("data scientist" in cfg["workday_queries"] and "software engineer" in cfg["workday_queries"],
               "workday queries cover both tracks"))
de_jd = """Requirements
- 5+ years of experience in data engineering
- Build batch and streaming pipelines with Spark, Kafka and Airflow
- Data modeling (star schema) in Snowflake; Iceberg lakehouse experience
- Change data capture with Debezium; data quality checks with Great Expectations
- Strong Python and SQL
"""
de = m.score(Job(ats="test", company="Acme", ext_id="3", title="Senior Data Engineer", location="Remote - US",
                 url="u", apply_url="u", description=de_jd, posted_at=date.today().isoformat()))
from jsb.config import track_target  # noqa: E402
checks += [
    (de.track == "ds" and de.family == "DS · Data engineering", f"DE family (got {de.track}/{de.family})"),
    (de.required_years == 5, f"reads '5+ years of experience in data engineering' (got {de.required_years})"),
    ({"Data modeling", "CDC / ingestion", "Data quality", "Lakehouse / table formats"} <= set(de.matched + de.partial + de.missing),
     f"DE skills detected ({de.matched + de.missing})"),
    (track_target(cfg, "ds", de.family) == "Senior Data Engineer", "DE judged as Senior Data Engineer"),
    (track_target(cfg, "ds", res.family) == "Senior Data Scientist", "DS judged as Senior Data Scientist"),
    ("data engineer" in cfg["workday_queries"], "workday searches data engineer"),
]
for ok, msg in checks:
    if not ok:
        fails.append(msg)
        print("FAIL", msg)

print(f"score={res.score} family={res.family} matched={res.matched}")
print("OK" if not fails else f"{len(fails)} failures")
sys.exit(1 if fails else 0)
