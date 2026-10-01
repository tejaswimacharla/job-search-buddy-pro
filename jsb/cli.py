from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import subprocess
import sys
import textwrap
from datetime import date

from . import blocklist, db, layoffs, report
from .config import load_config, track_criteria, track_target
from .criteria import check_row
from .match import Matcher, label
from .profile import load_profile
from .sources import ashby, bamboohr, greenhouse, lever, rippling, session, smartrecruiters, workable, workday
from .sources.url import import_url


ALL_SOURCES = ["greenhouse", "ashby", "lever", "workday", "smartrecruiters", "bamboohr", "workable", "rippling"]


def _ctx():
    cfg = load_config()
    profile = load_profile(cfg["rta_path"])
    return cfg, profile, Matcher(cfg, profile), db.connect()


# ---------------------------------------------------------------- scrape
def cmd_scrape(args):
    cfg, profile, matcher, conn = _ctx()
    sess = session()
    tasks = []
    wanted = set(args.source or ALL_SOURCES)
    if "greenhouse" in wanted:
        tasks += [("greenhouse", b, lambda b=b: greenhouse.fetch(sess, b)) for b in cfg.get("greenhouse", [])]
    names = cfg.get("company_names", {})
    if "ashby" in wanted:
        tasks += [("ashby", b, lambda b=b: ashby.fetch(sess, b, names.get(b))) for b in cfg.get("ashby", [])]
    if "lever" in wanted:
        tasks += [("lever", b, lambda b=b: lever.fetch(sess, b, names.get(b))) for b in cfg.get("lever", [])]
    if "workday" in wanted:
        tasks += [("workday", t["company"], lambda t=t: workday.fetch(sess, t, cfg["workday_queries"]))
                  for t in cfg.get("workday", [])]
    if "smartrecruiters" in wanted:
        tasks += [("smartrecruiters", c, lambda c=c: smartrecruiters.fetch(sess, c)) for c in cfg.get("smartrecruiters", [])]
    for ats, mod in (("bamboohr", bamboohr), ("workable", workable), ("rippling", rippling)):
        if ats in wanted:
            tasks += [(ats, b, lambda b=b, mod=mod: mod.fetch(sess, b, names.get(b))) for b in cfg.get(ats, [])]

    total = new = kept = 0
    errors = []
    with cf.ThreadPoolExecutor(12) as ex:
        futs = {ex.submit(fn): (ats, name) for ats, name, fn in tasks}
        for fut in cf.as_completed(futs):
            ats, name = futs[fut]
            try:
                jobs = fut.result()
            except Exception as e:  # noqa: BLE001
                errors.append(f"{ats}/{name}: {e}")
                continue
            seen_ids = []
            for job in jobs:
                total += 1
                seen_ids.append(job.id)
                # Only store roles that pass the cheap filters; keeps the DB small and relevant.
                if matcher.prefilter(job):
                    continue
                # List endpoints for these boards omit descriptions; fetch them only for survivors.
                hydrators = {"workday": workday, "smartrecruiters": smartrecruiters, "bamboohr": bamboohr,
                             "rippling": rippling}
                if not job.description and ats in hydrators:
                    hydrators[ats].hydrate(sess, job)
                    if matcher.prefilter(job):   # the detail page can reveal a stale date or non-US location
                        continue
                kept += 1
                new += db.upsert(conn, job)
            # Anything from this board we didn't see this time has been taken down.
            prefix = f"{ats}:"
            if seen_ids and ats in ("greenhouse", "ashby", "lever", "bamboohr", "workable", "rippling"):
                board_ids = {j.id.split(":")[1] for j in jobs}
                for comp in board_ids:
                    ph = ",".join("?" * len(seen_ids))
                    conn.execute(f"UPDATE jobs SET closed=1 WHERE id LIKE ? AND id NOT IN ({ph})",
                                 (f"{prefix}{comp}:%", *seen_ids))
            conn.commit()
    conn.execute("INSERT INTO scrape_runs VALUES (datetime('now','localtime'),?,?,?,?,?)",
                 (len(tasks), total, kept, new, len(errors)))
    conn.commit()
    print(f"Scanned {total} postings from {len(tasks)} boards; {kept} passed role/location/age filters; {new} new.")
    for e in errors:
        print("  warn:", e, file=sys.stderr)
    _score_all(conn, matcher)
    _refresh_warn(cfg, conn, sess)


def _refresh_warn(cfg, conn, sess) -> None:
    months = cfg["apply_criteria"].get("exclude_recent_layoffs_months") or 0
    if not months:
        return
    since = layoffs.index(months).since
    for name, fn in (("California", layoffs.refresh_ca_warn), ("Washington", layoffs.refresh_wa_warn)):
        try:
            n = fn(sess, conn, since)
            print(f"{name} WARN notices: {n} new in the last {months} months.")
        except Exception as e:  # noqa: BLE001
            print(f"  warn: couldn't refresh {name} WARN data: {e}", file=sys.stderr)
    layoffs._index_cached.cache_clear()


def _score_all(conn, matcher, only_unscored=False):
    q = "SELECT rowid, * FROM jobs WHERE closed=0" + (" AND score IS NULL" if only_unscored else "")
    n = 0
    for row in conn.execute(q).fetchall():
        m = matcher.score(db.row_to_job(row), row["first_seen"])
        conn.execute("UPDATE jobs SET score=?, score_detail=? WHERE id=?",
                     (m.score if m.passed else 0, json.dumps(m.as_dict()), row["id"]))
        n += 1
    conn.commit()
    print(f"Scored {n} open jobs.")


def cmd_score(args):
    cfg, profile, matcher, conn = _ctx()
    print(f"Candidate: {profile.identity.get('name')} · {profile.years_experience} yrs · "
          f"{sum(r.eligible for r in profile.skill_rows)} evidence-backed skills · "
          f"tracks: {', '.join(cfg['active_tracks'])}")
    _score_all(conn, matcher)


def _track(row) -> str:
    return json.loads(row["score_detail"] or "{}").get("track") or "swe"


def _family(row) -> str:
    return json.loads(row["score_detail"] or "{}").get("family") or ""


def _crit(cfg, row) -> dict:
    return track_criteria(cfg, _track(row))


def _fails(cfg, row, ignore_match: bool = False) -> list[str]:
    return check_row(_crit(cfg, row), row, json.loads(row["score_detail"] or "{}"), ignore_match=ignore_match)


def _ensure_rerank(cfg, profile, conn, row):
    """The match gate should use Claude's score, not the keyword score; re-rank first if needed."""
    if row["llm_score"] is not None:
        return row
    from .llm import rerank
    print(f"Getting Claude's match score for {row['company']} · {row['title']} (~20s)...")
    res = rerank(db.row_to_job(row), profile, track=_track(row), family=_family(row))
    conn.execute("UPDATE jobs SET llm_score=?, llm_detail=? WHERE id=?", (res["score"], json.dumps(res), row["id"]))
    conn.commit()
    print(f"  Claude: {res['verdict']} ({res['score']}) — {res['why']}")
    return conn.execute("SELECT rowid, * FROM jobs WHERE id=?", (row["id"],)).fetchone()


def _blocked_exit(row) -> None:
    """Blocklisted companies are a hard stop: no --force. Remove them with `jsb unblock` first."""
    e = blocklist.match(row["company"])
    if e:
        sys.exit(f"Blocked: {row['company']} is on your do-not-apply list ({blocklist.describe(e)}). "
                 f"Run `jsb unblock \"{e['company']}\"` if that's changed.")


def _criteria_line(cfg, track: str | None = None) -> str:
    c = track_criteria(cfg, track)
    line = (f"posted ≤{c['max_posted_hours']}h · top of pay >${c['min_base_pay'] // 1000}K · "
            f"asks {c['years_min']}-{c['years_max']} yrs")
    if c.get("min_match"):
        line += f" · match {c['min_match']}%+" + (f" (stretch {c['stretch_match']}%+)" if c.get("stretch_match") else "")
    for lim in c.get("company_limits") or []:
        line += f" · {lim['company']} >{lim['above_match']}%"
    if c.get("exclude_recent_layoffs_months"):
        line += f" · no layoffs in {c['exclude_recent_layoffs_months']} mo"
    if track is None:
        over = [f"{t}: " + ", ".join(f"{k}={v}" for k, v in cfg["tracks"][t]["apply_criteria"].items())
                for t in cfg["active_tracks"] if cfg["tracks"][t].get("apply_criteria")]
        if over:
            line += " (overrides — " + "; ".join(over) + ")"
    return line


def _ensure_layoff_check(cfg, conn, company: str, sample_title: str) -> None:
    """Research a company's recent layoffs before tailoring/applying if it hasn't been checked lately."""
    months = cfg["apply_criteria"].get("exclude_recent_layoffs_months") or 0
    lc = cfg.get("layoffs", {})
    if not months or not layoffs.needs_research(conn, company, lc.get("research_ttl_days", 7)):
        return
    print(f"Checking {company} for layoffs in the last {months} months (web research, ~30s)...")
    try:
        res = layoffs.research(company, sample_title, layoffs.index(months).since, lc.get("research_model", "sonnet"))
        layoffs.store_research(conn, company, res)
        layoffs._index_cached.cache_clear()
        print(f"  {res['summary']}")
    except Exception as e:  # noqa: BLE001
        sys.exit(f"Couldn't check {company} for layoffs ({e}). Re-run, or add an exception in config/layoffs.yaml.")


# ---------------------------------------------------------------- rerank
def cmd_rerank(args):
    from .llm import rerank
    cfg, profile, matcher, conn = _ctx()
    cond = "" if args.force else "AND llm_score IS NULL"
    rows = conn.execute(f"""SELECT rowid, * FROM jobs WHERE closed=0 AND status NOT IN ('skipped','submitted')
                           AND score >= ? {cond} ORDER BY score DESC""", (cfg["min_score"],)).fetchall()
    blocked = blocklist.load()
    rows = [r for r in rows if not blocklist.match(r["company"], blocked)]
    if args.track:
        rows = [r for r in rows if _track(r) == args.track]
    if not args.all:
        # The match gate depends on this re-rank, so don't apply it when choosing candidates.
        rows = [r for r in rows if not _fails(cfg, r, ignore_match=True)]
    rows = rows[:args.top]
    print(f"Re-ranking {len(rows)} jobs with Claude ({args.model})"
          + ("" if args.all else f", apply criteria only: {_criteria_line(cfg, args.track)}") + "...")

    def work(row):
        return row, rerank(db.row_to_job(row), profile, args.model, track=_track(row), family=_family(row))

    with cf.ThreadPoolExecutor(max(1, args.parallel // 2)) as ex:   # each job already runs its scorings in parallel
        for fut in cf.as_completed([ex.submit(work, r) for r in rows]):
            try:
                row, res = fut.result()
            except Exception as e:  # noqa: BLE001
                print("  warn:", str(e)[:200], file=sys.stderr)
                continue
            conn.execute("UPDATE jobs SET llm_score=?, llm_detail=? WHERE id=?", (res["score"], json.dumps(res), row["id"]))
            conn.commit()
            spread = "/".join(map(str, res.get("runs", [])))
            print(f"  #{row['rowid']:<5} {res['score']:>3} {res['verdict']:<6} ({spread:<8}) {_track(row):<3} "
                  f"{row['company']} · {row['title']}")


# ---------------------------------------------------------------- browse
def _ranked(conn, min_score, status=None, limit=50):
    q = """SELECT rowid, *, COALESCE(llm_score, score) AS rank FROM jobs
           WHERE closed=0 AND COALESCE(llm_score, score) >= ?"""
    params = [min_score]
    if status:
        q += " AND status = ?"
        params.append(status)
    else:
        q += " AND status NOT IN ('skipped', 'submitted')"
    q += " ORDER BY rank DESC, posted_at DESC LIMIT ?"
    return conn.execute(q, (*params, limit)).fetchall()


def cmd_list(args):
    cfg, _, _, conn = _ctx()
    rows = _ranked(conn, args.min if args.min is not None else cfg["min_score"], args.status, 10_000)
    blocked = blocklist.load()
    if args.track:
        rows = [r for r in rows if _track(r) == args.track]
    if not args.all:
        rows = [r for r in rows if not _fails(cfg, r) and not blocklist.match(r["company"], blocked)]
        print(f"Meeting apply criteria ({_criteria_line(cfg, args.track)}). Use --all to see everything.\n")
    from .criteria import match_tier
    tier = lambda r: match_tier(_crit(cfg, r), r)
    if not args.all:   # Apply tier first, then Stretch; each keeps its match order
        rows = sorted(rows, key=lambda r: tier(r) != "apply")
    rows = rows[:args.limit]
    shown_tier = None
    multi = len(cfg["active_tracks"]) > 1 and not args.track
    for r in rows:
        if not args.all and tier(r) != shown_tier:
            shown_tier = tier(r)
            if multi:   # each track can set its own bars
                bars = {"apply": "match ≥ each track's min_match", "stretch": "each track's stretch_match to min_match"}
            else:
                c = track_criteria(cfg, args.track or cfg["active_tracks"][0])
                bars = {"apply": f"{c['min_match']}%+", "stretch": f"{c.get('stretch_match')}-{c['min_match'] - 1}%"}
            print({"apply": f"APPLY ({bars['apply']})",
                   "stretch": f"\nSTRETCH ({bars['stretch']}): one clear gap, related experience"}[shown_tier])
        ai = f" AI {int(r['llm_score'])}" if r["llm_score"] is not None else ""
        if args.all and blocklist.match(r["company"], blocked):
            ai += " [BLOCKED]"
        trk = f"{_track(r):<4}" if multi else ""
        print(f"#{r['rowid']:<5} {trk}{int(r['score']):>3}{ai:<7} {label(r['rank']):<6} {r['status']:<11} "
              f"{r['company'][:18]:<18} {r['title'][:50]:<50} {_age(r['posted_at']):>4} {_pay(r):>11}")
    if not args.all:
        n_apply = sum(1 for r in rows if tier(r) == "apply")
        print(f"\n{n_apply} apply + {len(rows) - n_apply} stretch. `jsb show <#>` for details, `jsb report` for the dashboard.")
    else:
        print(f"\n{len(rows)} jobs. `jsb show <#>` for details, `jsb report` for the dashboard.")


def _age(posted):
    from .criteria import hours_since
    h = hours_since(posted)
    return "?" if h is None else f"{int(h)}h" if h < 72 else f"{int(h // 24)}d"


def _pay(r):
    from .pay import parse
    p = parse(r["compensation"], r["description"])
    return f"${p[0] // 1000}-{p[1] // 1000}K" if p else "no pay"


def cmd_show(args):
    _, _, _, conn = _ctx()
    r = db.get(conn, args.id)
    if not r:
        sys.exit("No such job (or ambiguous id).")
    r = conn.execute("SELECT rowid, * FROM jobs WHERE id=?", (r["id"],)).fetchone()
    det = json.loads(r["score_detail"] or "{}")
    print(f"#{r['rowid']} {r['title']} — {r['company']}\n{r['location']} · posted {r['posted_at']} · {r['compensation']}")
    print(f"Track: {det.get('track') or 'swe'} · {det.get('family', '')} · judged as "
          f"{track_target(load_config(), det.get('track') or 'swe', det.get('family'))}")
    print(f"{r['url']}\nStatus: {r['status']}   Score: {r['score']} ({label(r['score'] or 0)})  {det.get('components')}")
    print(f"Matched: {', '.join(det.get('matched', []))}\nExposure only: {', '.join(det.get('partial', []))}")
    print(f"No evidence: {', '.join(det.get('missing', []))}\nFlags: {', '.join(det.get('flags', [])) or 'none'}")
    cfg = load_config()
    fails = _fails(cfg, r)
    print(f"Pay: {_pay(r)} · posted {_age(r['posted_at'])} ago · asks {det.get('required_years') or '?'}"
          f"{'-' + str(det['years_max']) if det.get('years_max') and det.get('years_max') != det.get('required_years') else ''} yrs")
    from .criteria import match_tier as _tier
    print("Apply criteria: " + (f"✓ meets all ({_tier(_crit(cfg, r), r)} tier)" if not fails else "✗ " + ", ".join(fails)))
    b = blocklist.match(r["company"])
    if b:
        print(f"BLOCKED company: {blocklist.describe(b)}")
    from .criteria import applications_used, company_limit, match_score
    print(f"Match used for the gate: {match_score(r):.0f}%" + ("" if r["llm_score"] is not None else " (rule score; Claude re-rank not run yet)"))
    lim = company_limit(_crit(cfg, r), r["company"])
    if lim:
        used = applications_used(lim)
        print(f"{lim['company']} cap: {len(used)}/{lim['max_applications']} applications used in the last "
              f"{lim['window_days']} days; needs match >{lim['above_match']}%")
    _print_layoffs(cfg, r["company"])
    if r["llm_detail"]:
        llm = json.loads(r["llm_detail"])
        spread = f", runs {'/'.join(map(str, llm['runs']))}" if llm.get("runs") else ""
        print(f"\nClaude: {llm['verdict']} ({llm['score']}{spread}) — {llm['why']}")
        for k in ("strengths", "gaps", "dealbreakers"):
            if llm.get(k):
                print(f"  {k}: " + "; ".join(llm[k]))
    if r["rta_output"]:
        print(f"\nTailored package: {r['rta_output']}")
    if args.full:
        print("\n" + textwrap.fill(r["description"] or "", 100, replace_whitespace=False))


def cmd_add(args):
    cfg, profile, matcher, conn = _ctx()
    job = import_url(session(), args.url)
    db.upsert(conn, job)
    m = matcher.score(job)
    if not m.passed:
        print(f"Note: this job fails the '{m.reject_reason}' filter; scoring it anyway since you added it.")
        m = matcher.score(job, force=True)
    conn.execute("UPDATE jobs SET score=?, score_detail=?, status='shortlisted' WHERE id=?",
                 (m.score, json.dumps(m.as_dict()), job.id))
    conn.commit()
    rowid = conn.execute("SELECT rowid FROM jobs WHERE id=?", (job.id,)).fetchone()[0]
    print(f"Added #{rowid}: {job.title} — {job.company} · {m.track} track · score {m.score} ({label(m.score)})")
    if job.ats in ("jobright", "linkedin") and job.apply_url == job.url:
        print("  This came from an aggregator; `jsb apply` will open that page and you'll click through to the company form.")


def cmd_mark(args):
    _, _, _, conn = _ctx()
    r = db.get(conn, args.id)
    if not r:
        sys.exit("No such job.")
    db.set_status(conn, r["id"], args.status)
    print(f"{r['company']} · {r['title']} -> {args.status}")


# ---------------------------------------------------------------- tailor / apply
def cmd_tailor(args):
    from .tailor import run_tailoring
    cfg, profile, _, conn = _ctx()
    r = db.get(conn, args.id)
    if not r:
        sys.exit("No such job.")
    _blocked_exit(r)
    _ensure_layoff_check(cfg, conn, r["company"], r["title"])
    if not _fails(cfg, r, ignore_match=True):
        r = _ensure_rerank(cfg, profile, conn, r)
    fails = _fails(cfg, r)
    if fails and not args.force:
        sys.exit(f"Skipping: outside apply criteria ({', '.join(fails)}). Pass --force to tailor anyway.")
    job = db.row_to_job(r)
    if len(job.description) < 300:
        sys.exit("This posting has almost no description text; add it with `jsb add <url>` from the company page first.")
    print(f"Tailoring for {job.company} · {job.title} in {cfg['rta_path']} (this takes a few minutes)...\n")
    target = track_target(cfg, _track(r), _family(r))
    status, path = run_tailoring(job, cfg["rta_path"], model=args.model, stream=not args.quiet, target=target)
    if status == "ready":
        db.set_status(conn, r["id"], "tailored", rta_output=path)
        print(f"\nResume package ready: {cfg['rta_path'] / path}\nNext: review it, then `jsb apply {args.id}`.")
    elif status == "needs_input":
        db.set_status(conn, r["id"], "shortlisted", notes=f"needs input: {path}")
        print(f"\nThe tailoring assistant needs your input first: {cfg['rta_path'] / path}/clarifying_questions.md")
    else:
        print("\nTailoring didn't finish cleanly. Last output:\n" + path)


def cmd_apply(args):
    from .apply import prefill
    from .tailor import find_pdf
    from . import tracker
    cfg, profile, _, conn = _ctx()
    r = db.get(conn, args.id)
    if not r:
        sys.exit("No such job.")
    _blocked_exit(r)
    _ensure_layoff_check(cfg, conn, r["company"], r["title"])
    if not _fails(cfg, r, ignore_match=True):
        r = _ensure_rerank(cfg, profile, conn, r)
    fails = _fails(cfg, r)
    # A company's application cap is a hard stop, like the do-not-apply list.
    capped = [f for f in fails if "cap reached" in f]
    if capped:
        sys.exit(f"Not applying: {capped[0]}. Oldest application drops out of the window first.")
    if fails and not args.force:
        sys.exit(f"Skipping: outside apply criteria ({', '.join(fails)}). Pass --force to apply anyway.")
    rta = cfg["rta_path"]
    pdf = find_pdf(rta, r["rta_output"]) if r["rta_output"] else None
    if not pdf and not args.no_resume:
        sys.exit(f"No tailored resume yet. Run `jsb tailor {args.id}` first (or pass --no-resume).")
    cover = next(iter(sorted((rta / r["rta_output"]).glob("cover_letter*.pdf"))), None) if r["rta_output"] else None
    print(f"Opening {r['apply_url']}\nResume: {pdf}")
    if r["ats"] == "workday":
        print("Workday needs a candidate account per company; sign in yourself. Only fields on the first page get pre-filled.")
    prefill(r["apply_url"], pdf, profile, cover)
    db.set_status(conn, r["id"], "prefilled")
    ans = input("Did you submit the application? [y/N] ").strip().lower()
    if ans == "y":
        det = json.loads(r["score_detail"] or "{}")
        db.set_status(conn, r["id"], "submitted", notes=f"submitted {date.today().isoformat()}")
        if r["rta_output"]:
            tracker.record(rta, company=r["company"], role=r["title"], jd_source=r["url"], output_rel=r["rta_output"],
                           matches=det.get("matched", []), gaps=det.get("missing", []), status="submitted",
                           next_actions="follow up in ~1 week")
        print("Marked submitted and logged in the RTA application_tracker.md.")
    else:
        print("Left as 'prefilled'. Run `jsb mark <#> submitted` later if you submit it.")


# ---------------------------------------------------------------- blocklist
def cmd_block(args):
    try:
        entry, new = blocklist.add(args.company, args.reason, args.note or "", args.alias)
    except ValueError as e:
        sys.exit(str(e))
    _, _, _, conn = _ctx()
    hits = [r for r in conn.execute("SELECT DISTINCT company FROM jobs").fetchall() if blocklist.match(r[0], [entry])]
    print(f"{'Blocked' if new else 'Updated'}: {entry['company']} — {blocklist.describe(entry)}")
    if hits:
        print("  Matches companies in your job list: " + ", ".join(sorted(h[0] for h in hits)))
    else:
        print("  (No jobs from this company in the current list; it'll apply when they show up.)")


def cmd_unblock(args):
    print(f"Removed {args.company}." if blocklist.remove(args.company) else f"{args.company} wasn't on the list.")


def cmd_blocked(args):
    entries = blocklist.load()
    if not entries:
        print("No blocked companies. Add one: jsb block \"Company\" --reason no-sponsorship-h1b")
        return
    w = max(len(e["company"]) for e in entries)
    for e in sorted(entries, key=lambda e: e["company"].lower()):
        aka = f"  [also: {', '.join(e['aliases'])}]" if e.get("aliases") else ""
        print(f"{e['company']:<{w}}  {e['reason']:<24} {e.get('added', ''):<11} {e.get('note', '')}{aka}")
    print(f"\n{len(entries)} companies · {blocklist.PATH}")


# ---------------------------------------------------------------- layoffs
def _print_layoffs(cfg, company: str) -> None:
    months = cfg["apply_criteria"].get("exclude_recent_layoffs_months") or 6
    idx = layoffs.index(months)
    n = layoffs.norm(company)
    if n in idx.exceptions:
        print(f"Layoffs: ignored for {company} — exception: {idx.exceptions[n].get('reason', '')}")
        return
    ev = idx.lookup(company)
    res = idx.researched.get(n)
    if not ev:
        print(f"Layoffs (last {months} mo): none found" + ("" if res else " in CA/WA WARN; web research not run yet"))
    else:
        print(f"Layoffs (last {months} mo):")
        for e in ev:
            who = f"{e['employees']} people" if e.get("employees") else "count n/a"
            tag = "" if e["confirmed"] else " [UNCONFIRMED - not excluding]"
            print(f"  {e['date']}  {who:<12} {e['source']:<8} {(e.get('where') or '')[:70]}  {e.get('url') or ''}{tag}")
    if res:
        print(f"  Research note: {res['summary']}")


def _research_targets(cfg, conn) -> list[tuple[str, str]]:
    """Companies with at least one job passing every *other* criterion, best score first."""
    blocked = blocklist.load()
    seen, out = set(), []
    for r in _ranked(conn, cfg["min_score"], None, 10_000):
        n = layoffs.norm(r["company"])
        if n in seen or blocklist.match(r["company"], blocked):
            continue
        crit = dict(_crit(cfg, r), exclude_recent_layoffs_months=0)
        if not check_row(crit, r, json.loads(r["score_detail"] or "{}"), ignore_match=True):
            seen.add(n)
            out.append((r["company"], r["title"]))
    return out


def cmd_layoffs(args):
    cfg, _, _, conn = _ctx()
    months = cfg["apply_criteria"].get("exclude_recent_layoffs_months") or 6
    lc = cfg.get("layoffs", {})
    if args.action == "refresh":
        _refresh_warn(cfg, conn, session())
    elif args.action == "research":
        targets = [(args.company, args.company)] if args.company else _research_targets(cfg, conn)
        todo = [t for t in targets if args.company or args.force
                or layoffs.needs_research(conn, t[0], lc.get("research_ttl_days", 7))]
        todo = todo[:args.limit]
        print(f"Researching {len(todo)} companies for layoffs since {layoffs.index(months).since} "
              f"({len(targets) - len(todo)} already checked recently)...")
        since = layoffs.index(months).since

        def work(t):
            return t, layoffs.research(t[0], t[1], since, lc.get("research_model", "sonnet"))

        with cf.ThreadPoolExecutor(4) as ex:
            for fut in cf.as_completed([ex.submit(work, t) for t in todo]):
                try:
                    (company, _), res = fut.result()
                except Exception as e:  # noqa: BLE001
                    print(f"  warn: {str(e)[:150]}", file=sys.stderr)
                    continue
                layoffs.store_research(conn, company, res)
                mark = "LAYOFFS" if res["had_layoffs"] else "clear  "
                print(f"  {mark}  {company:<22} {res['summary'][:110]}")
        layoffs._index_cached.cache_clear()
    elif args.action == "show":
        if not args.company:
            sys.exit("Usage: jsb layoffs show \"Company\"")
        _print_layoffs(cfg, args.company)
    else:  # list: companies in your job list with recent layoffs
        idx = layoffs.index(months)
        companies = sorted({r[0] for r in conn.execute("SELECT DISTINCT company FROM jobs WHERE closed=0 AND score>0")})
        hits = [(c, idx.summary(c)) for c in companies if idx.summary(c)]
        checked = sum(1 for c in companies if layoffs.norm(c) in idx.researched)
        for c, s in hits:
            print(f"{c:<24} {s}")
        soft = [c for c in companies if not idx.summary(c) and idx.unconfirmed(c)]
        if soft:
            print("\nUnconfirmed reports only (not excluded): " + ", ".join(soft))
        print(f"\n{len(hits)} of {len(companies)} companies in your job list had layoffs since {idx.since} "
              f"({checked} web-researched; the rest are checked against CA + WA WARN only). "
              f"Exceptions: config/layoffs.yaml")


# ---------------------------------------------------------------- discover
FORBES_API = "https://www.forbes.com/forbesapi/org/americas-best-startup-employers/{year}/position/true.json"


def cmd_discover(args):
    from .discover import resolve, save_discovered
    sess = session()
    sess.headers["User-Agent"] = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/128 Safari/537.36"
    year = args.year or date.today().year
    r = sess.get(FORBES_API.format(year=year), params={"limit": 2000}, timeout=60)
    orgs = (r.json().get("organizationList") or {}).get("organizationsLists") or [] if r.ok else []
    if not orgs:
        sys.exit(f"Couldn't load Forbes' {year} list (HTTP {r.status_code}). Try --year {year - 1}.")
    orgs = sorted(orgs, key=lambda o: o.get("rank") or 9999)[:args.limit]
    print(f"Forbes America's Best Startup Employers {year}: {len(orgs)} companies. Finding their job boards...")

    from .discover import load_discovered
    key = f"forbes-best-startup-employers-{year}"
    known = {e["company"]: e for e in (load_discovered().get("sources") or {}).get(key, [])}

    def work(o):
        prev = known.get(o["organizationName"])
        if args.only_missing and prev and (prev.get("ats") or prev.get("skip")):
            return prev
        res = resolve(sess, o["organizationName"], o.get("webSite") or "")
        if prev and prev.get("skip"):
            res.update(skip=True, note=prev.get("note", ""))
        res.update({"forbes_rank": o.get("rank"), "industry": o.get("industry"),
                    "hq": ", ".join(x for x in [o.get("city"), o.get("state")] if x),
                    "employees": o.get("employees"), "website": o.get("webSite")})
        return res

    out = []
    with cf.ThreadPoolExecutor(16) as ex:
        for i, res in enumerate(ex.map(work, orgs), 1):
            out.append(res)
            if i % 50 == 0:
                print(f"  {i}/{len(orgs)} checked, {sum(1 for x in out if x['ats'])} boards found")
    save_discovered(key, out)
    found = [x for x in out if x["ats"]]
    by = {}
    for x in found:
        by[x["ats"]] = by.get(x["ats"], 0) + 1
    print(f"\nFound boards for {len(found)} of {len(out)} companies: "
          + ", ".join(f"{k} {v}" for k, v in sorted(by.items(), key=lambda kv: -kv[1])))
    print(f"Saved to config/companies_discovered.yaml. Not found: {len(out) - len(found)} "
          f"(custom career sites; add them with `jsb add <job url>`).")
    print("Run `./jsb.sh scrape` to pull their jobs.")


# ---------------------------------------------------------------- report / daily
def cmd_report(args):
    _, _, _, conn = _ctx()
    path = report.write(conn)
    print(f"Wrote {path}")
    if not args.no_open:
        subprocess.run(["open", path])


def cmd_daily(args):
    cmd_scrape(argparse.Namespace(source=None))
    cfg = load_config()
    if cfg["apply_criteria"].get("exclude_recent_layoffs_months") and not args.no_research:
        cmd_layoffs(argparse.Namespace(action="research", company=None, force=False,
                                       limit=cfg.get("layoffs", {}).get("research_per_run", 25)))
    if args.rerank:
        cmd_rerank(argparse.Namespace(top=args.rerank, model=args.model, parallel=4, force=False, all=False,
                                      track=args.track))
    cmd_report(argparse.Namespace(no_open=args.no_open))


def main(argv=None):
    p = argparse.ArgumentParser(prog="jsb", description="Job search buddy: scrape → match → tailor → assisted apply")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scrape", help="Fetch jobs from all configured boards and score them")
    s.add_argument("--source", action="append", choices=ALL_SOURCES)
    s.set_defaults(fn=cmd_scrape)

    sub.add_parser("score", help="Re-score all open jobs (after your knowledge base changes)").set_defaults(fn=cmd_score)

    s = sub.add_parser("rerank", help="Second-pass ranking of the top jobs with Claude")
    s.add_argument("--top", type=int, default=20)
    s.add_argument("--model", default="sonnet")
    s.add_argument("--parallel", type=int, default=4)
    s.add_argument("--force", action="store_true", help="Re-rank jobs that already have an AI score")
    s.add_argument("--all", action="store_true", help="Include jobs outside the apply criteria")
    s.add_argument("--track", help="Only this track, e.g. swe or ds")
    s.set_defaults(fn=cmd_rerank)

    s = sub.add_parser("list", help="List ranked matches")
    s.add_argument("--min", type=float)
    s.add_argument("--status", choices=db.STATUSES)
    s.add_argument("--limit", type=int, default=40)
    s.add_argument("--all", action="store_true", help="Include jobs outside the apply criteria")
    s.add_argument("--track", help="Only this track, e.g. swe or ds")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("show", help="Show one job's match breakdown")
    s.add_argument("id")
    s.add_argument("--full", action="store_true", help="Print the full description")
    s.set_defaults(fn=cmd_show)

    s = sub.add_parser("add", help="Import a single job URL (Greenhouse/Lever/Ashby/Jobright/LinkedIn/company page)")
    s.add_argument("url")
    s.set_defaults(fn=cmd_add)

    s = sub.add_parser("mark", help="Set a job's status")
    s.add_argument("id")
    s.add_argument("status", choices=db.STATUSES)
    s.set_defaults(fn=cmd_mark)

    s = sub.add_parser("tailor", help="Run the Resume Tailoring Assistant on a job")
    s.add_argument("id")
    s.add_argument("--model")
    s.add_argument("--quiet", action="store_true")
    s.add_argument("--force", action="store_true", help="Ignore the apply criteria")
    s.set_defaults(fn=cmd_tailor)

    s = sub.add_parser("apply", help="Open the application in Chrome and pre-fill it (you review and submit)")
    s.add_argument("id")
    s.add_argument("--no-resume", action="store_true")
    s.add_argument("--force", action="store_true", help="Ignore the apply criteria")
    s.set_defaults(fn=cmd_apply)

    s = sub.add_parser("block", help="Add a company to the do-not-apply list")
    s.add_argument("company")
    s.add_argument("--reason", required=True,
                   help=f"{', '.join(blocklist.REASONS)} (short forms: no-perm, h1b, manual, in-progress)")
    s.add_argument("--note", help="Free-text detail, e.g. who told you / which team")
    s.add_argument("--alias", action="append", help="Other names this company posts under (repeatable)")
    s.set_defaults(fn=cmd_block)

    s = sub.add_parser("unblock", help="Remove a company from the do-not-apply list")
    s.add_argument("company")
    s.set_defaults(fn=cmd_unblock)

    sub.add_parser("blocked", help="Show the do-not-apply list").set_defaults(fn=cmd_blocked)

    s = sub.add_parser("layoffs", help="Recent-layoff checks (PERM-friendliness)")
    s.add_argument("action", choices=["list", "refresh", "research", "show"], nargs="?", default="list",
                   help="list: companies with recent layoffs · refresh: pull CA WARN · "
                        "research: web-check companies with qualifying jobs · show: one company")
    s.add_argument("company", nargs="?")
    s.add_argument("--limit", type=int, default=25)
    s.add_argument("--force", action="store_true", help="Re-research even if checked recently")
    s.set_defaults(fn=cmd_layoffs)

    s = sub.add_parser("discover", help="Import companies from Forbes' America's Best Startup Employers list")
    s.add_argument("source", choices=["forbes"])
    s.add_argument("--year", type=int)
    s.add_argument("--limit", type=int, default=2000)
    s.add_argument("--only-missing", action="store_true", help="Only re-check companies with no board found yet")
    s.set_defaults(fn=cmd_discover)

    s = sub.add_parser("report", help="Write and open the HTML dashboard")
    s.add_argument("--no-open", action="store_true")
    s.set_defaults(fn=cmd_report)

    s = sub.add_parser("daily", help="scrape + optional Claude rerank + report")
    s.add_argument("--rerank", type=int, default=0, metavar="N", help="Re-rank the top N with Claude")
    s.add_argument("--model", default="sonnet")
    s.add_argument("--no-open", action="store_true")
    s.add_argument("--no-research", action="store_true", help="Skip the layoff web research step")
    s.add_argument("--track", help="Only re-rank this track's jobs (scrape and report cover all active tracks)")
    s.set_defaults(fn=cmd_daily)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
