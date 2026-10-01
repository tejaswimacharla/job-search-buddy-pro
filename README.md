# Job Search Buddy Pro

A fork of [job-search-buddy](https://github.com/tejaswimacharla/job-search-buddy) that adds a data
science / analytics / data engineering search track, and tailors resumes itself from one base
resume (no separate Resume Tailoring Assistant needed).

Finds SDE / SWE / SRE / AI engineering roles **and data science / applied science / analytics /
data engineering roles**, ranks them against your **base resume** the way Jobright's
recommendations do, writes a tailored, fact-checked resume PDF for the good ones, then opens the
application with the standard fields pre-filled so you can review and submit.

```
scrape (public ATS APIs) → filter → score (rules) → rerank (Claude) → tailor + truth audit → assisted apply → tracker
```

## Quick start

```bash
uv sync                          # once
./jsb.sh resume use ~/Downloads/my_resume.pdf   # once (PDF, DOCX, Markdown or text); Claude reads it
./jsb.sh daily --rerank 25       # scrape ~120 boards, score, have Claude re-rank the top 25, open the dashboard
./jsb.sh list                    # ranked list in the terminal
./jsb.sh show 19                 # score breakdown + Claude's take for job #19
./jsb.sh tailor 19               # tailored, fact-checked resume PDF for it (~2-3 min)
./jsb.sh apply 19                # open the form in Chrome, pre-filled, resume attached; you submit
```

Optional: `alias jsb="$PWD/jsb.sh"` in your `~/.zshrc`.

## Search tracks (SWE + Data Science)

Roles are searched in **tracks**, set in `config/search.yaml` under `tracks` and switched on with
`active_tracks: [swe, ds]`. Each scraped title goes to the first active track that claims it.

| | `swe` | `ds` |
|---|---|---|
| Titles | SWE, SDE, backend, platform, SRE, ML/AI engineer, MTS, DevOps | Data scientist, applied / ML / decision / product scientist, product & growth analytics, experimentation, ranking & recommendations, analytics engineer, **data engineer / data engineering, data platform / infrastructure / pipeline / warehouse / lakehouse engineer, big data, ETL, DataOps** |
| Excluded | anything with "scientist", and data platform / infrastructure / pipeline engineer titles (go to `ds`) | research scientist, wet-lab/clinical sciences, BI/reporting analyst, actuarial, solutions specialists / consultants |
| Role types | SRE, AI/agent infra, backend, full stack, DevOps, forward deployed | data engineering, analytics engineering, ML / applied science, experimentation / causal, product analytics |
| Extra skills scored | — | statistics, A/B testing, causal inference, Bayesian, forecasting, ML modeling, scikit-learn, GBMs, deep learning, NLP, ranking/recsys, pandas, R, SAS/Stata, product metrics, BI tools, Snowflake, optimization, data viz; for data engineering: lakehouse / table formats, orchestration, data modeling, CDC / ingestion, data quality, stream processing |
| Claude's rubric | senior SWE recruiter | DS recruiter: decides whether the role is ML/applied, product analytics, data engineering or analytics engineering, and weighs evidence for that kind of role |
| Judged as | Senior Software Engineer | Senior Data Scientist; Senior Data Engineer / Senior Analytics Engineer for those role types (`family_targets`) |
| Apply criteria | global `apply_criteria` | global, overridden by `tracks.ds.apply_criteria` (default: pay >$180K, apply 72%+, stretch 62%+) |

A generic SWE title on a data team ("Senior Software Engineer, Data Platform") stays in `swe`;
only titles that are themselves data-engineering titles move to `ds`.

The DS skills are only scored on DS-track jobs, so SWE scores are exactly what they were
before tracks existed. The track is also passed to `tailor`, so the resume leads with modeling,
experimentation and analysis evidence on a DS resume, and with pipelines, data volumes and
the storage/processing stack on a data engineering one (still only what your base resume supports).

```bash
./jsb.sh list --track ds              # DS roles meeting the DS criteria
./jsb.sh rerank --track ds --top 15   # Claude re-ranks DS roles with the DS rubric
./jsb.sh daily --rerank 25 --track ds # scrape everything, re-rank only DS
```

The dashboard has a **Track** filter; the criteria line and KPIs follow the selected track.
To search only data science, set `active_tracks: [ds]`. To add a track (say `mle` or `quant`),
copy a block under `tracks`; role families and the Claude rubric fall back to the SWE ones
unless you add entries to `ROLE_FAMILIES` in `jsb/match.py` and `GUIDANCE` in `jsb/llm.py`.

Matching reads the same base resume for every track. If your evidence is mostly engineering,
expect DS rule scores to run high and Claude's DS scores to be blunt about missing modeling or
statistics work. Adding that work to your base resume is what moves them.

## Schedule

A Claude desktop scheduled task, **job-search-daily**, runs `./jsb.sh daily --rerank 25 --no-open`
at 8 AM and 4 PM and posts a summary of qualifying roles. It never tailors or applies. Manage it
from **Scheduled** in the Claude app sidebar; it runs while the app is open, and a missed run
happens on next launch. (A macOS launchd job doesn't work here: macOS blocks background jobs from
reading `~/Documents`, where both projects live.)

## Commands

| Command | What it does |
|---|---|
| `scrape [--source greenhouse …]` | Pull every posting from the boards in `config/search.yaml`, keep roles that pass title/location/age filters, score them, and mark removed postings closed |
| `resume [show\|parse\|use <file>] [--full]` | Set your base resume, see what was read from it (roles, bullet IDs, which skills a bullet backs up), or re-parse it |
| `score` | Re-score everything, e.g. after you update your base resume |
| `rerank --top N [--model sonnet] [--track ds]` | Claude reads the full JD against your evidence and returns score, verdict, gaps and dealbreakers. Uses your local `claude` login |
| `list [--min 70] [--status tailored] [--track ds]` | Ranked list (AI score is used when present) |
| `show <#> [--full]` | Why it matched: evidenced skills, exposure-only skills, missing skills, flags |
| `add <url>` | Import one job from a Greenhouse/Lever/Ashby/Jobright/LinkedIn/company URL |
| `tailor <#> [--cover-letter] [--anyway]` | Tailor your base resume to the job: JD analysis, rewrite, truth audit, one-page PDF (see below) |
| `apply <#>` | Assisted apply (see below) |
| `mark <#> <status>` | `new`, `shortlisted`, `tailored`, `prefilled`, `submitted`, `skipped` |
| `block` / `unblock` / `blocked` | Manage the do-not-apply company list (see below) |
| `report` | Rebuild and open the dashboard, `reports/matches.html`: postings scanned per run, relevant roles, new today, strong matches, median match and pay, the match-score distribution, top companies, role types, locations, a base-pay histogram, your application pipeline, and a sortable, filterable jobs table (match %, Claude score, company, role, location, pay, posted, status) |
| `daily [--rerank N]` | scrape → rerank → report |

## Where jobs come from

- **Greenhouse, Ashby, Lever, SmartRecruiters**: their official public job-board JSON APIs.
  No login or HTML scraping. About 120 companies are preconfigured (Anthropic, OpenAI,
  Databricks, Stripe, Snowflake, Coinbase, Cloudflare, Datadog, Cursor, Sierra, Decagon,
  Cresta, Commure, and others).
- **BambooHR, Workable, Rippling**: their public careers feeds. These are common at smaller
  startups; descriptions are fetched only for roles that pass the title and location filters.
- **Workday** (NVIDIA, Salesforce, Adobe, Cisco, Intel, PayPal, Capital One, and others):
  the careers site's own search endpoint, queried with every active track's `workday_queries`
  (SWE/SRE/ML engineer, plus data scientist / applied scientist / analytics engineer).
- **LinkedIn and Jobright**: `add <url>` for individual postings only. Bulk-crawling either
  one needs your logged-in session and breaks their terms. That risks getting your LinkedIn
  account restricted, which is the last thing you want mid-search. Keep using Jobright's feed
  to discover roles; paste the good ones in with `add`.
- **Adding a company**: find its board token in the careers URL
  (`job-boards.greenhouse.io/<token>`, `jobs.ashbyhq.com/<token>`, `jobs.lever.co/<token>`)
  and add it to the matching list in `config/search.yaml`.

### Company lists

```bash
./jsb.sh discover forbes            # Forbes America's Best Startup Employers (current year)
./jsb.sh discover forbes --year 2025
```

This pulls the list from Forbes' data API. For each company it looks for a job-board link on
the company's own site first, then tries likely board names (for example, from the website
domain). Results go to `config/companies_discovered.yaml` with Forbes rank, industry, HQ and
headcount, and are merged into every scrape. Set `skip: true` on an entry to drop it. A board
linked from the company's own site is kept even with no openings today; a guessed board name
is only kept if it has jobs. Companies on custom career sites or unsupported boards (Paylocity,
Breezy, ADP, SuccessFactors and others) are recorded with `ats: null`; add their postings with
`jsb add <url>`. `jsb discover forbes --only-missing` re-checks just those.

## Apply criteria

Set in `config/search.yaml` under `apply_criteria` (currently: posted in the last **72 hours**,
top of base pay **above $200K**, posting asks for **2–7 years**). `list`, `rerank` and the dashboard
default to jobs that meet them, and `tailor` / `apply` refuse anything outside them unless you
pass `--force` (`list --all` and `rerank --all` show everything).

- **Posted:** uses the board's own publish timestamp. Greenhouse reports the original publish
  date, so a reposted role counts from when it first went up.
- **Pay:** the top of the posted base range must be above $200K. Set `pay_basis: bottom` to
  require the whole range above it. Postings with no pay range are excluded
  (`include_unlisted_pay`).
- **Years:** every years figure in the required sections (not "preferred") must fall between
  2 and 7. For example, "5+ years" passes and "8+ years" fails. Postings that don't state
  years are kept (`include_unlisted_years`).
- **Citizenship / clearance:** roles requiring US citizenship, a security clearance or public
  trust are excluded (`exclude_citizenship_required`). "US citizens or permanent residents"
  wording is only flagged, not excluded.
- **No visa sponsorship:** postings that say they won't, can't, or may not sponsor H-1B or other
  visas ("unable to sponsor", "without sponsorship", "may not… support future H-1B sponsorship")
  are excluded (`exclude_no_sponsorship`). Export-license wording and "for roles outside the US"
  don't count.
- **Match, in two tiers:** **apply** at 75%+ (`min_match`) and **stretch** at 65–74%
  (`stretch_match`, roles with one clear gap but related experience). Both qualify; `list` and
  the dashboard show them separately. This uses Claude's score once a job is re-ranked,
  otherwise the rule score; `tailor` and `apply` re-rank first if needed.
- **Company caps** (`company_limits`): OpenAI and Anthropic allow 6 applications per 180 days,
  so they need a match above 85%. `apply` refuses once a cap is used up, even with `--force`.
  Applications made outside this tool go under `already_applied` so they count.
- **Recent layoffs:** companies with layoffs in the last 6 months are excluded
  (`exclude_recent_layoffs_months`). See below.

## Recent layoffs (PERM-friendliness)

Before filing PERM, an employer must notify workers it laid off in the same or a related
occupation in the area within the previous 6 months. Many employers pause PERM after layoffs,
so companies with recent layoffs are excluded. That's deliberately stricter than the rule,
which only covers the same occupation and area.

Where the data comes from:
1. **California and Washington WARN notices**, official state data refreshed on every
   `scrape`: California EDD (spreadsheet plus prior-year PDF) and Washington ESD (Seattle,
   Bellevue, Redmond). WARN only covers larger layoffs at a single site (50+ in California).
2. **Claude web research** on each company with qualifying jobs: news, company announcements,
   layoffs.fyi, and other states' WARN notices. Every event must cite a source URL. Results
   are cached for 7 days. `daily` researches up to 25 companies per run. `tailor` and `apply`
   research a company automatically if it hasn't been checked, so nothing is applied to
   unchecked.
3. **`config/layoffs.yaml`**, your own `entries` (layoffs you've heard about) and `exceptions`
   (e.g. a recruiter confirmed PERM filings continue).

```bash
./jsb.sh layoffs              # companies in your job list with recent layoffs
./jsb.sh layoffs research     # web-check companies that have qualifying jobs
./jsb.sh layoffs show "Cisco" # events, dates, headcounts and source links
./jsb.sh layoffs refresh      # re-pull California + Washington WARN
```

Name matching between WARN filings and job boards is exact after removing suffixes like
"Inc"/"LLC", so a filing under a different legal name ("Meta Platforms" vs "Meta") can be
missed. Web research covers those cases.

**Location:** you're open to relocating anywhere in the US, so other US cities score 0.95 on
location against 1.0 for the Bay Area and remote-US roles (`location_weights`). Non-US roles are
filtered out, and Claude's re-rank is told a US location is never a gap.

## Do-not-apply list

`config/blocked_companies.yaml` lists companies that are never tailored for or applied to.
Each entry has a company name, a reason (`manual-override`, `no-sponsorship-h1b`,
`no-perm-support` or `other`), an optional note and a date added.

```bash
./jsb.sh block "Acme" --reason no-sponsorship-h1b --note "recruiter said no transfers"
./jsb.sh block "Block" --reason manual-override --alias "Square" --alias "Cash App"
./jsb.sh blocked                 # show the list
./jsb.sh unblock "Acme"
```

This is a hard stop. `tailor` and `apply` refuse blocked companies even with `--force`, and
`rerank` skips them. `list` hides them (`list --all` shows them tagged `[BLOCKED]`). The
dashboard marks them ⛔ and leaves them out of "Meets apply criteria". Matching ignores case,
punctuation and suffixes like Inc/LLC, and works on whole words: "Chime" covers "Chime
Financial, Inc" but "Meta" doesn't cover "Metabase". Use `--alias` for other brand names.
The file is git-ignored because sponsorship reasons are personal.

## How matching works

Nothing about you is stored in the config. Everything comes from your base resume in `resume/`
(git-ignored), parsed once by Claude into roles, dated bullets, skills, education and contact
details. The parse is cached in `data/resume_profile.json` and redone automatically when the
file changes (`jsb resume parse` forces it). Every bullet gets an ID: `E1.B3` is role 1,
bullet 3.

1. **Hard filters**: title include/exclude regexes (per track, plus a global exclude list), US or remote-US location, posted within
   `max_age_days`.
2. **Rule score (0-100)**:
   - Skill fit (55%): JD skills are weighted by section (required > general > preferred) and
     multiplied by your evidence for each: a skill used in two or more resume bullets counts
     fully, in one bullet 0.85, and listed only in the skills section 0.5. The score also
     includes what share of the JD's bullet lines touch skills your bullets show.
   - Experience vs. required years, plus seniority (18%).
   - Role family (12%), location (10%), recency (5%).
   - Flags for clearance/citizenship requirements, no-sponsorship language, and 5-day onsite.
3. **Claude re-rank**: the rule score is tuned for recall and runs high. Claude scores the job
   the way a recruiter would: on the role's core (domain, kind of systems, seniority, primary
   stack), giving credit for transferable experience (e.g. CDK for Terraform, AWS for GCP). It
   reads your whole resume, plus which skills are backed by bullets and which are only listed.
   Each job is scored twice and the scores averaged (`rerank_runs`), because single runs vary by
   up to ~15 points. It costs about $0.20 per job. Where you're based and willing to work comes
   from `candidate` in `config/search.yaml`.

## Tailoring

`jsb tailor <#>` turns your base resume into one for this job, in about 2-3 minutes:

1. **Analyze and draft** (one Claude call): the role's core themes, with how directly your
   resume covers each and which bullets show it; ATS keywords; gaps; questions. Then the
   tailored resume: every role kept in order, bullets reworded and reordered by relevance (the
   least relevant last), skills regrouped for the job, and a summary if it helps. **Every bullet
   and the summary cite the base-resume bullet IDs they come from.**
2. **Truth audit** (code, not Claude). Each bullet must:
   - cite bullets that exist, and only from the same role, so work never moves between jobs
   - use only numbers that appear in those bullets, unchanged (40% can't become 45%, 2x can't
     become 100%)
   - not upgrade ownership: *led / owned / designed / architected* only if a cited bullet says so
   - list only skills that appear on your base resume (others are dropped)
   - follow house style: no em dashes or trailing periods (fixed automatically); filler words and
     first person are flagged
3. **Repair**: failing bullets go back to Claude once, with the problem and the bullets they may
   cite. Anything that still fails is replaced with your original wording. Nothing unverified
   reaches the PDF.
4. **Render**: an ATS-safe, single-column PDF through headless Chrome, fitted to
   `resume.max_pages` (default 1). It tightens spacing first, then drops the least relevant
   bullets (projects first, then older roles; every role keeps at least one bullet).

If fewer than 3 of the role's core themes have direct evidence, it stops before writing anything
and leaves `questions.md` (the job goes back to `shortlisted`). `--anyway` overrides that.
`--cover-letter` (or `resume.cover_letter: true`) adds a short cover letter under the same rules.

Everything lands in `output/<company>_<role>_<date>/` (git-ignored):

| File | What it is |
|---|---|
| `<Name>_Resume.pdf` | the resume `apply` attaches |
| `<Name>_Cover_Letter.pdf` | with `--cover-letter` |
| `analysis.md` | core themes and coverage, ATS keywords, gaps, questions, what changed |
| `audit.md` | every bullet with the source bullets it cites, audit findings, repairs, what was cut to fit |
| `resume.md`, `resume.txt`, `resume.html` | editable Markdown, the plain text an ATS sees, the HTML behind the PDF |
| `job_description.txt`, `draft.json`, `tailored.json` | the JD as tailored against, Claude's raw draft, the final document |

## Assisted apply: why it stops before Submit

`apply` opens the form in Chrome (using a separate profile in `.browser-profile/`, so logins
stick between runs). It fills name, email, phone, LinkedIn, GitHub, location and current
company from your base resume, attaches the tailored PDF (and cover letter, if any), and prints what's left. **It never
clicks Submit.** It deliberately leaves these fields for you:

- work authorization and sponsorship (a resume doesn't hold these)
- EEO / demographic questions
- salary expectations and "why us" style free text
- dropdowns, checkboxes and autocomplete fields

After you submit, answer `y` and the job is marked `submitted` and logged in
`data/application_tracker.md`, a Markdown table you can also edit by hand. Status only changes
on your say-so.

Workday requires a separate candidate account per company, so it's mostly manual there.

## Files

```
config/search.yaml     base resume settings, tracks (swe, ds), locations, filters, company boards
resume/                your base resume (git-ignored)
jsb/sources/           greenhouse, ashby, lever, workday, smartrecruiters, single-URL import
jsb/resume.py          reads and parses the base resume (bullet IDs, skill evidence)
jsb/profile.py         matching profile built from the parsed resume
jsb/match.py           filters + scoring
jsb/llm.py             Claude re-rank (claude -p, structured output)
jsb/tailor.py          JD analysis, tailored draft, truth audit, repair, PDF rendering + page fitting
jsb/apply.py           Playwright pre-fill (never submits)
jsb/tracker.py         writes data/application_tracker.md rows
data/jobs.db           SQLite store (git-ignored)
reports/matches.html   dashboard (git-ignored)
tests/test_fill.py     fills a local fixture form with dummy data
tests/test_tracks.py   title routing and DS scoring with a synthetic profile
tests/test_tailor.py   truth audit, fallback and page fitting with a synthetic resume
output/                tailored resumes (git-ignored)
```
