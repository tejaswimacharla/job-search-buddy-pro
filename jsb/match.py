"""Jobright-style matching: hard filters, then a weighted 0-100 score with an explanation."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from .models import Job
from .profile import EXPOSURE_WEIGHT, Profile

# canonical skill -> regex (case-insensitive unless listed in CASE_SENSITIVE)
SKILLS: dict[str, str] = {
    # languages
    "Python": r"\bpython\b", "Java": r"\bjava\b(?!script)", "Kotlin": r"\bkotlin\b",
    "Go": r"\bgolang\b|\bGo\b(?![- ]to\b)", "C++": r"c\+\+", "Rust": r"\brust\b", "TypeScript": r"\btypescript\b",
    "JavaScript": r"\bjavascript\b", "Scala": r"\bscala\b", "C#": r"\bc#", "Ruby": r"\bruby\b",
    "SQL": r"\bsql\b",
    # cloud / infra
    "AWS": r"\baws\b|amazon web services", "GCP": r"\bgcp\b|google cloud", "Azure": r"\bazure\b",
    "Kubernetes": r"\bkubernetes\b|\bk8s\b|\beks\b|\bgke\b", "Docker": r"\bdocker\b|\bcontainer(s|ized|ization)?\b",
    "Terraform": r"\bterraform\b", "AWS CDK": r"\baws cdk\b|\bcdk\b", "CloudFormation": r"cloudformation",
    "Ansible": r"\bansible\b", "Jenkins": r"\bjenkins\b", "CI/CD": r"\bci/cd\b|\bci ?/ ?cd\b|continuous (integration|delivery|deployment)|\bci pipelines?\b",
    "Linux": r"\blinux\b", "Observability": r"observability|\bprometheus\b|\bgrafana\b|opentelemetry|\bmonitoring\b",
    "On-call / incidents": r"on-?call|incident (response|management)|postmortem",
    "Chaos / load testing": r"chaos (engineering|testing)|load testing|capacity planning",
    "Multi-region": r"multi-?region",
    # data
    "Kafka": r"\bkafka\b", "Redis": r"\bredis\b|elasticache", "DynamoDB": r"dynamodb", "PostgreSQL": r"postgres",
    "MySQL": r"\bmysql\b", "NoSQL": r"\bnosql\b", "Spark": r"\bspark\b", "Elasticsearch/OpenSearch": r"elasticsearch|opensearch",
    "Cassandra": r"cassandra", "Airflow": r"\bairflow\b", "Flink": r"\bflink\b",
    # backend
    "Distributed systems": r"distributed systems?", "Microservices": r"micro-?services?",
    "gRPC": r"\bgrpc\b", "REST APIs": r"\brest(?= ?apis?\b)|\brestful\b|api design|\bapis?\b",
    "Caching": r"\bcach(e|ing)\b", "Rate limiting": r"rate[- ]limit|throttl",
    "Scalability / reliability": r"scalab|high[- ]availability|reliab|\bslos?\b|\bslas?\b",
    "Event-driven": r"event[- ]driven|\bqueues?\b|pub/?sub|\bsqs\b",
    # AI
    "LLMs": r"\bllms?\b|large language model|generative ai|\bgenai\b",
    "RAG": r"\brag\b|retrieval[- ]augmented", "AI agents": r"\bagent(s|ic)?\b",
    "Tool calling / MCP": r"tool[- ]?(use|calling)|function calling|\bmcp\b|model context protocol",
    "AWS Bedrock": r"bedrock", "Vector search": r"vector (search|database|db|store)|embeddings?",
    "LLM evaluation": r"\bevals?\b|evaluation (pipeline|framework|harness)|llm evaluation",
    "Prompt engineering": r"prompt engineering", "Fine-tuning": r"fine-?tun",
    "PyTorch": r"pytorch", "TensorFlow": r"tensorflow", "Model serving / inference": r"\binference\b|model serving|\bvllm\b|triton",
    "MLOps": r"\bmlops\b|ml (platform|infrastructure|pipelines?)", "LangChain": r"langchain|langgraph",
    "OpenAI/Anthropic APIs": r"openai api|anthropic api|claude api",
    "GPU / CUDA": r"\bcuda\b|\bgpus?\b",
    # outside the candidate's core; present so mismatches show up as missing
    "React": r"\breact(\.js|js)?\b(?! to)", "Frontend / UI": r"front-?end|\bcss\b|\bhtml\b|design systems?|\bui components?",
    "Node.js": r"node\.?js", "Spring": r"\bspring( boot)?\b", "iOS / Android": r"\bios\b|\bswift\b|\bandroid\b",
    "Data warehousing / ETL": r"\bdbt\b|bigquery|data warehouse|\betl\b|redshift",
    "Security engineering": r"threat (detection|model)|vulnerabilit|\bappsec\b|cryptograph|zero trust",
    "Networking": r"tcp/ip|\bbgp\b|network(ing)? protocols|load balanc",
    "Compilers / systems programming": r"compiler|\bkernel\b|\bllvm\b",
}
# Only scored on data science track jobs, so SWE scores are unchanged by them.
DS_SKILLS: dict[str, str] = {
    "Statistics": r"\bstatistic(s|al)\b|hypothesis test|regression (analysis|models?)|\bglms?\b|significance test",
    "A/B testing / experimentation": r"a/b test|\bab test|experiment(ation|s)? (design|platform|framework|analysis)|"
                                     r"online experiment|controlled experiment|\bexperimentation\b",
    "Causal inference": r"causal (inference|analysis|model|ml|impact)|difference[- ]in[- ]differences|\bdiff[- ]in[- ]diff|"
                        r"propensity|synthetic control|instrumental variable|uplift model",
    "Bayesian methods": r"\bbayesian\b|\bmcmc\b|\bpymc\b|\bstan\b",
    "Forecasting / time series": r"forecast|time[- ]series",
    "Machine learning (modeling)": r"machine learning model|predictive model|\bclassification\b|\bclustering\b|"
                                   r"supervised|unsupervised|model (training|development|building)|feature engineering",
    "scikit-learn": r"scikit|sklearn", "XGBoost / GBMs": r"xgboost|lightgbm|catboost|gradient[- ]boost",
    "Deep learning": r"deep learning|neural net", "NLP": r"\bnlp\b|natural language processing|text classification",
    "Recommender / ranking": r"recommend(er|ation) (system|model)s?|\branking (model|system)s?|personalization|search relevance",
    "Pandas / NumPy": r"\bpandas\b|\bnumpy\b|\bscipy\b|\bpolars\b", "Jupyter": r"jupyter|notebooks?\b",
    "R": r"\bR\b(?!\s?&|\+\+|-\d)|\brstudio\b|\btidyverse\b|\bggplot",
    "SAS / Stata / MATLAB": r"\bsas\b|\bstata\b|\bmatlab\b",
    "Product analytics / metrics": r"product analytics|\bkpis?\b|metric(s)? (design|definition|framework)|north star|"
                                   r"funnel|retention|cohort analysis|user behavior|growth analytics",
    "BI / dashboards": r"tableau|looker|power ?bi|\bmode analytics\b|\bsuperset\b|dashboard",
    "Snowflake": r"snowflake",
    # data engineering
    "Lakehouse / table formats": r"lakehouse|\biceberg\b|delta lake|\bhudi\b|\bparquet\b",
    "Orchestration (Dagster/Prefect)": r"\bdagster\b|\bprefect\b|\bluigi\b|workflow orchestration|\borchestrat(ion|or) (tool|framework)s?",
    "Data modeling": r"data model(l)?ing|dimensional model|star schema|\bkimball\b|slowly changing",
    "CDC / ingestion": r"change data capture|\bcdc\b|debezium|fivetran|airbyte|data ingestion",
    "Data quality": r"data quality|great expectations|data contracts?|data observability|data lineage",
    "Stream processing": r"stream processing|streaming (data|pipelines?)|real[- ]time (data|pipelines?)|kinesis|spark streaming",
    "Optimization / OR": r"operations research|linear programming|\bmixed[- ]integer\b|optimization (model|problem)s?",
    "Data visualization": r"data visuali[sz]ation|matplotlib|seaborn|plotly",
}
TRACK_SKILLS = {"swe": set(SKILLS), "ds": set(SKILLS) | set(DS_SKILLS)}
CASE_SENSITIVE = {"Go", "R"}
# Candidate-side text is lower-cased, so case-sensitive JD patterns need a lower-case twin.
# Anchored to the start of the skill-matrix row so "go" in prose ("go-live") doesn't count.
CANDIDATE_OVERRIDE = {"Go": r"^(go|golang)\b", "R": r"^(r|r language|rstudio|tidyverse)\b"}

# Skills so generic they shouldn't dominate the score when matched.
LOW_SIGNAL = {"REST APIs", "Scalability / reliability", "Caching", "SQL", "Docker", "Linux", "Jupyter",
              "BI / dashboards", "Data visualization"}

PREFERRED_HDR = re.compile(r"^\s*(#+\s*)?(preferred|nice[- ]to[- ]have|bonus|pluses|it'?s a plus|"
                           r"ideally|additional qualifications|preferred qualifications)", re.I | re.M)
REQUIRED_HDR = re.compile(r"^\s*(#+\s*)?(requirements|required|minimum qualifications|basic qualifications|"
                          r"what you('ll)? (need|bring)|you have|who you are|qualifications)", re.I | re.M)
BOILERPLATE = re.compile(r"^\s*(#+\s*)?(.{0,40}equal (employment )?opportunity|we are an equal|reasonable accommodation|"
                         r"benefits|pay transparency|compensation|about us|about the company)", re.I | re.M)
YEARS = re.compile(r"(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?(?:\+\s*)?years?(?: of)?(?: professional| relevant| industry| hands-on)?"
                   r"(?: software| engineering| backend| experience| work| data| analytics| quantitative| statistical| machine learning| applied)", re.I)
FLAGS = {
    "Requires US citizenship / clearance": r"security clearance|\b(ts/sci|top secret|secret clearance|public trust)\b|"
                                           r"(?<!non-)(?<!non )\b(u\.?s\.?|united states) citizen(ship|s)?\b(?! or)|"
                                           r"citizenship (is )?required|must be (a )?(u\.?s\.?|united states) (citizen|person)|"
                                           r"clearance[- ]eligible|eligible (to obtain|for) (a |an )?(u\.?s\.? )?(government |security )?clearance|"
                                           r"(ability|able) to obtain (and maintain )?(a |an )?(u\.?s\.? )?(government |security |secret )?clearance",
    "States no visa sponsorship": (
        r"(not|unable to|cannot|can't|will not|won't|may not|do(es)? not)( be able to)? (provide|offer|support|sponsor)"
        r"( (future|new|any|employment|work))?( (visa|h-?1b|immigration|work authorization))?( sponsorship| sponsor)?\b"
        r"(?<!support)(?<!provide)(?<!offer)"
        r"|sponsorship (is |will )?(not|n't) (be )?(available|provided|offered|possible)"
        r"|(must|should|will) not (now or in the future )?require (visa |immigration |employment )?sponsorship"
        r"|without (the need for |requiring )?(current or future |visa |employer )?sponsorship(?! for an export)"
        r"|not (be )?able to employ candidates[^\n]{0,180}visa"
        r"|(not|unable to)[^\n.]{0,40}support future h-?1b"
        r"|no (visa|h-?1b|immigration) sponsorship|not eligible for (visa|immigration) sponsorship"),
    "Onsite 5 days": r"(5|five) days (a|per) week (in|on)[- ]?(the )?(office|site)|fully on-?site",
}
# Per track: (family, title regex, fit). First match wins; titles matching none get 0.7.
ROLE_FAMILIES = {"swe": [
    ("SRE / Reliability", r"site reliability|\bsre\b|reliability|production engineer", 1.0),
    ("AI / Agent infra", r"\b(ai|ml|llm|genai|agent\w*|applied ai|inference)\b", 1.0),
    ("Backend / Distributed", r"backend|back-end|distributed|platform|infrastructure|systems engineer|cloud platform", 1.0),
    ("Full stack", r"full[- ]?stack", 0.75),
    ("DevOps / Cloud", r"devops|cloud engineer", 0.8),
    ("Forward deployed", r"forward deployed", 0.7),
    ("Software Engineer", r"software|\bsde\b|\bswe\b|member of technical staff", 0.9),
], "ds": [
    ("DS · Data engineering", r"data (engineer|platform|infrastructure|pipeline|reliability|integration|warehouse|ops)|"
                              r"\bdataops\b|big data|\betl\b|\belt\b|lakehouse|streaming data", 0.95),
    ("DS · Analytics engineering", r"analytics engineer", 0.9),
    ("DS · ML / Applied science", r"applied scientist|machine learning|\bml\b|\bai\b|ranking|recommend|personaliz|"
                                  r"relevance|modeling|nlp", 1.0),
    ("DS · Experimentation / Causal", r"experiment|causal|inference|econometric|statistician|decision scien", 0.95),
    ("DS · Product analytics", r"product|growth|marketing|analytics|analyst", 0.85),
    ("DS · Data Scientist", r"data scien", 0.95),
]}


US_STATES = ("alabama alaska arizona arkansas california colorado connecticut delaware florida georgia hawaii "
             "idaho illinois indiana iowa kansas kentucky louisiana maine maryland massachusetts michigan "
             "minnesota mississippi missouri montana nebraska nevada ohio oklahoma oregon pennsylvania "
             "tennessee texas utah vermont virginia wisconsin wyoming").split() + [
             "new hampshire", "new jersey", "new mexico", "new york", "north carolina", "north dakota",
             "rhode island", "south carolina", "south dakota", "west virginia", "district of columbia"]
US_CODE = re.compile(r"(?:^|[,;(]\s*|\bUS[,-]\s*)(A[LKZR]|C[AOT]|D[EC]|FL|GA|HI|I[DLNA]|K[SY]|LA|M[EDAINSOT]|N[EVHJMYCD]|"
                     r"O[HKR]|PA|RI|S[CD]|T[NX]|UT|V[TA]|W[AVIY])\b")
US_WORD = re.compile(r"\bUSA?\b|\bU\.S\.|united states|\bamericas?\b|north america", re.I)
NON_US = re.compile(r"\b(bucharest|romania|budapest|hungary|noida|gurugram|gurgaon|pune|chennai|mumbai|delhi|india|"
                    r"malaysia|penang|kuala lumpur|portugal|greece|athens|sweden|austria|vienna|switzerland|z[uü]rich|"
                    r"saudi|riyadh|dubai|uae|vietnam|vnm|ho chi minh|nottingham|sheffield|eng|england|scotland|"
                    r"philippines|manila|taiwan|taipei|china|shanghai|beijing|hong kong|czech|prague|belgium|"
                    r"netherlands|denmark|norway|finland|italy|milan|rome|costa rica|colombia|bogot[aá]|argentina|"
                    r"buenos aires|chile|peru|nigeria|kenya|south africa|egypt|turkey|istanbul|indonesia|jakarta|"
                    r"thailand|bangkok|new zealand|serbia|belgrade|ukraine|kyiv|lithuania|estonia|latvia|croatia|"
                    r"bulgaria|sofia|slovakia|luxembourg|cyprus|gbp|ontario|british columbia|quebec)\b", re.I)


def _flag_hit(name: str, rx: str, desc: str) -> bool:
    for m in re.finditer(rx, desc, re.I):
        if name == "States no visa sponsorship":
            # "...unable to sponsor ... for roles outside the US" isn't about US roles.
            after = desc[m.end():m.end() + 90].lower()
            if re.search(r"outside (of )?(the )?(united states|us\b|u\.s\.)", after):
                continue
        return True
    return False


@dataclass
class Match:
    score: float
    passed: bool
    reject_reason: str = ""
    family: str = ""
    track: str = ""
    location_class: str = ""
    required_years: int | None = None
    years_max: int | None = None
    matched: list[str] = field(default_factory=list)
    partial: list[str] = field(default_factory=list)     # exposure-only evidence
    missing: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    components: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return self.__dict__


class Matcher:
    def __init__(self, cfg: dict, profile: Profile):
        self.cfg, self.profile = cfg, profile
        comp = lambda pats: [re.compile(p, re.I) for p in pats]
        self.exc = comp(cfg["title_exclude"])
        # (name, include, exclude, seniority stretch) for each active track, in priority order
        self.tracks = [(n, comp(t["title_include"]), comp(t["title_exclude"]), comp(t["seniority_stretch"]))
                       for n in cfg["active_tracks"] for t in [cfg["tracks"][n]]]
        self.skill_re = {k: re.compile(v, 0 if k in CASE_SENSITIVE else re.I) for k, v in {**SKILLS, **DS_SKILLS}.items()}
        self.cand = self._candidate_weights()

    # ---- candidate side -------------------------------------------------------
    def _candidate_weights(self) -> dict[str, float]:
        w: dict[str, float] = {}
        for skill, rx in self.skill_re.items():
            if skill in CANDIDATE_OVERRIDE:
                rx = re.compile(CANDIDATE_OVERRIDE[skill], re.I)
            eligible = [r.weight for r in self.profile.skill_rows if r.eligible and rx.search(r.text)]
            if eligible:
                w[skill] = max(eligible)
            elif any(rx.search(r.text) for r in self.profile.skill_rows) or rx.search(self.profile.exposure_text):
                w[skill] = EXPOSURE_WEIGHT
            elif skill not in CANDIDATE_OVERRIDE and rx.search(self.profile.narrative_text):
                w[skill] = 0.6   # demonstrated in experience records / role bundles, not in the matrix
        return w

    # ---- filters --------------------------------------------------------------
    def track_for(self, title: str) -> str | None:
        """The first active track that wants this title, or None."""
        if any(r.search(title) for r in self.exc):
            return None
        return next((n for n, inc, exc, _ in self.tracks
                     if any(r.search(title) for r in inc) and not any(r.search(title) for r in exc)), None)

    def guess_track(self, title: str) -> str:
        """For jobs you add by hand whose title no track claims: pick the closest one."""
        ds = re.search(r"scien|analy|statist|data", title, re.I)
        names = [n for n, *_ in self.tracks]
        return "ds" if ds and "ds" in names else names[0]

    def title_ok(self, title: str) -> bool:
        return self.track_for(title) is not None

    def location_class(self, job: Job) -> str:
        raw = job.location or ""
        loc = raw.lower()
        has = lambda markers: any(re.search(rf"(?<![a-z]){re.escape(m)}(?![a-z])", loc) for m in markers)
        if has(self.cfg["preferred_locations"]):
            return "bay_area"
        us = (has(self.cfg["us_markers"]) or bool(US_WORD.search(raw)) or bool(US_CODE.search(raw))
              or any(re.search(rf"\b{st}\b", loc) for st in US_STATES))
        if not us and NON_US.search(raw):
            return "non_us"
        remote = job.remote or "remote" in loc
        if remote and (us or not has(self.cfg["non_us_markers"])):
            return "remote_us" if us or not loc.replace("remote", "").strip(" -,;()") else "remote_unclear"
        if us:
            return "us"
        if has(self.cfg["non_us_markers"]):
            return "non_us"
        return "unknown"

    def age_days(self, job: Job, first_seen: str | None = None) -> int | None:
        d = job.posted_at or first_seen
        try:
            return (date.today() - date.fromisoformat(d[:10])).days if d else None
        except ValueError:
            return None

    def prefilter(self, job: Job, first_seen: str | None = None, check_title: bool = True) -> str:
        """Cheap checks that need no description. Returns a reject reason or ''."""
        if check_title and not self.title_ok(job.title):
            return "title"
        if self.location_class(job) == "non_us":
            return "location"
        age = self.age_days(job, first_seen)
        if age is not None and age > self.cfg["max_age_days"]:
            return "stale"
        return ""

    # ---- scoring --------------------------------------------------------------
    def score(self, job: Job, first_seen: str | None = None, force: bool = False) -> Match:
        """force: score even if the title/age filters reject it (for jobs you added by hand)."""
        reason = "" if force else self.prefilter(job, first_seen)
        if reason:
            return Match(score=0, passed=False, reject_reason=reason)
        track = self.track_for(job.title) or self.guess_track(job.title)
        stretch = next((s for n, *_, s in self.tracks if n == track), [])

        desc = job.description or ""
        jd_skills = self._jd_skills(desc, track)
        num = den = 0.0
        matched, partial, missing = [], [], []
        for skill, jw in sorted(jd_skills.items(), key=lambda kv: -kv[1]):
            if skill in LOW_SIGNAL:
                jw *= 0.4
            cw = self.cand.get(skill, 0.0)
            num += jw * cw
            den += jw
            (matched if cw >= 0.6 else partial if cw > 0 else missing).append(skill)
        skill_score = num / den if den else 0.5
        if len(jd_skills) < 3:
            skill_score = 0.5 * skill_score + 0.2   # thin JD: pull toward neutral
        # Keyword overlap alone is fooled by JDs about unfamiliar work that happen to mention
        # Python or "agents". Also check what share of the JD's bullet lines touch evidenced skills.
        coverage = self._line_coverage(desc, track)
        if coverage is not None:
            skill_score = 0.6 * skill_score + 0.4 * min(1.0, coverage / 0.6)

        family, fam_score = next(((n, s) for n, rx, s in ROLE_FAMILIES.get(track, ROLE_FAMILIES["swe"])
                                  if re.search(rx, job.title, re.I)), ("Other", 0.7))

        t = job.title.lower()
        if re.search(r"senior staff|\bprincipal\b", t):
            level = 0.35
        elif any(r.search(t) for r in stretch):
            level = 0.6
        elif re.search(r"\bsenior\b|\bsr\.?\b|\biii\b|\bl5\b|\be5\b", t):
            level = 1.0
        elif re.search(r"\bii\b|\bl4\b|\bmid\b", t):
            level = 0.75
        else:
            level = 0.85
        cap = 75 if level <= 0.35 else 100

        yrs, yrs_max = self._required_years(desc)
        if yrs is None and job.extra.get("min_years"):
            yrs = yrs_max = int(job.extra["min_years"])
        have = self.profile.years_experience
        if yrs is None:
            exp = 0.85
        elif yrs <= have:
            exp = 1.0 if yrs >= have - 5 else 0.8   # far below: likely under-levelled
        elif yrs <= have + 2:
            exp = 0.65
        else:
            exp = 0.3
        exp = 0.5 * exp + 0.5 * level

        loc_class = self.location_class(job)
        loc = self.cfg.get("location_weights", {}).get(loc_class, 0.6)

        age = self.age_days(job, first_seen)
        rec = 0.7 if age is None else 1.0 if age <= 3 else 0.9 if age <= 7 else 0.75 if age <= 14 else 0.6

        flags = [name for name, rx in FLAGS.items() if _flag_hit(name, rx, desc)]
        comps = {"skills": skill_score, "role": fam_score, "experience": exp, "location": loc, "recency": rec}
        total = 100 * (0.55 * skill_score + 0.12 * fam_score + 0.18 * exp + 0.10 * loc + 0.05 * rec)
        if "Requires US citizenship / clearance" in flags:
            total -= 10
        total = min(total, cap)
        return Match(score=round(total, 1), passed=True, family=family, track=track, location_class=loc_class,
                     required_years=yrs, years_max=yrs_max, matched=matched, partial=partial, missing=missing[:8],
                     flags=flags, components={k: round(v, 2) for k, v in comps.items()})

    def _jd_skills(self, desc: str, track: str = "swe") -> dict[str, float]:
        """Weight each skill by where it appears: required 1.0, general 0.7, preferred 0.5."""
        weights: dict[str, float] = {}
        wanted = TRACK_SKILLS.get(track, TRACK_SKILLS["swe"])
        for section, w in self._sections(desc):
            for skill, rx in self.skill_re.items():
                if skill in wanted and rx.search(section):
                    weights[skill] = max(weights.get(skill, 0), w)
        return weights

    def _line_coverage(self, desc: str, track: str = "swe") -> float | None:
        wanted = TRACK_SKILLS.get(track, TRACK_SKILLS["swe"])
        strong = [self.skill_re[k] for k, w in self.cand.items() if w >= 0.6 and k not in LOW_SIGNAL and k in wanted]
        lines = [ln for sec, _ in self._sections(desc) for ln in sec.splitlines()
                 if ln.lstrip().startswith(("-", "*", "•")) and len(ln) > 25]
        if len(lines) < 5:
            return None
        return sum(any(rx.search(ln) for rx in strong) for ln in lines) / len(lines)

    @staticmethod
    def _sections(desc: str) -> list[tuple[str, float]]:
        marks = sorted([(m.start(), 1.0) for m in REQUIRED_HDR.finditer(desc)] +
                       [(m.start(), 0.5) for m in PREFERRED_HDR.finditer(desc)] +
                       [(m.start(), 0.0) for m in BOILERPLATE.finditer(desc)])
        # Leading "About <Company>" blurbs are marketing, not requirements.
        out, pos, w = [], 0, 0.7
        intro = re.match(r"\s*(#+\s*)?about (us|the (company|team)|[A-Z][\w.&-]*)\s*\n", desc)
        if intro:
            nxt = re.search(r"\n\s*\n.*\n\s*\n", desc[intro.end():], re.S)
            pos = intro.end() + (nxt.end() if nxt else 0)
        for start, nw in marks:
            out.append((desc[pos:start], w))
            pos, w = start, nw
        out.append((desc[pos:], w))
        return [(s, w) for s, w in out if w > 0 and s.strip()]

    @classmethod
    def _required_years(cls, desc: str) -> tuple[int | None, int | None]:
        """(smallest, largest) years-of-experience figure in the requirement sections."""
        text = "\n".join(sec for sec, w in cls._sections(desc) if w >= 0.7)   # ignore "preferred"
        vals = [int(m.group(1)) for m in YEARS.finditer(text) if 0 < int(m.group(1)) <= 20]
        return (min(vals), max(vals)) if vals else (None, None)


def label(score: float) -> str:
    return "Strong" if score >= 82 else "Good" if score >= 70 else "Fair" if score >= 55 else "Weak"
