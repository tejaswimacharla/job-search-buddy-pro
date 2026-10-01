"""Local HTML dashboard (reports/matches.html): scrape stats, match metrics, breakdowns, and the jobs table.

Everything is rendered client-side from one embedded JSON blob, so the filters at the top
re-compute every chart and the table together.
"""
from __future__ import annotations

import json
from datetime import date

from . import blocklist, layoffs, pay
from .config import REPORTS_DIR, load_config, track_criteria
from .criteria import applications_used, check_row, hours_since, match_tier


def _data(conn) -> dict:
    cfg = load_config()
    crit = cfg["apply_criteria"]
    blocked = blocklist.load()
    lay = layoffs.index(crit.get("exclude_recent_layoffs_months") or 6)
    jobs = []
    for r in conn.execute("""SELECT rowid, * FROM jobs WHERE closed=0 AND score > 0 AND status != 'skipped'
                             ORDER BY COALESCE(llm_score, score) DESC""").fetchall():
        det = json.loads(r["score_detail"] or "{}")
        track = det.get("track") or "swe"
        tcrit = track_criteria(cfg, track)
        llm = json.loads(r["llm_detail"] or "null") or {}
        p = pay.parse(r["compensation"], r["description"])
        b = blocklist.match(r["company"], blocked)
        jobs.append({
            "blocked": blocklist.describe(b) if b else None,
            "id": r["rowid"], "company": r["company"], "title": r["title"], "location": r["location"] or "",
            "loc": det.get("location_class", "unknown"), "family": det.get("family", "Other"), "track": track,
            "score": round(r["score"]), "ai": r["llm_score"], "verdict": llm.get("verdict"),
            "why": llm.get("why"), "runs": llm.get("runs"), "gaps": llm.get("gaps", []), "dealbreakers": llm.get("dealbreakers", []),
            "payLo": p[0] if p else None, "payHi": p[1] if p else None,
            "posted": r["posted_at"] or r["first_seen"], "firstSeen": r["first_seen"], "status": r["status"],
            "ats": r["ats"], "url": r["url"], "apply": r["apply_url"] or r["url"],
            "matched": det.get("matched", []), "partial": det.get("partial", []), "missing": det.get("missing", []),
            "flags": det.get("flags", []), "years": det.get("required_years"),
            "fails": check_row(tcrit, r, det), "hours": hours_since(r["posted_at"]), "tier": match_tier(tcrit, r),
            "layoffs": [{"date": e["date"], "n": e.get("employees"), "src": e["source"], "url": e.get("url"),
                         "where": (e.get("where") or "")[:90], "ok": e["confirmed"]} for e in lay.lookup(r["company"])[:4]],
            "layoffChecked": layoffs.norm(r["company"]) in lay.researched,
        })
    runs = [dict(r) for r in conn.execute("SELECT * FROM scrape_runs ORDER BY ts DESC LIMIT 14").fetchall()]
    status_all = dict(conn.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall())
    caps = [{"company": l["company"], "used": len(applications_used(l)), "max": l["max_applications"],
             "window": l["window_days"], "above": l["above_match"]} for l in crit.get("company_limits") or []]
    tracks = {t: cfg["tracks"][t]["label"] for t in cfg["active_tracks"]}
    return {"jobs": jobs, "runs": runs, "statusAll": status_all, "today": date.today().isoformat(), "crit": crit,
            "caps": caps, "tracks": tracks, "critByTrack": {t: track_criteria(cfg, t) for t in tracks},
            "overrides": {t: cfg["tracks"][t].get("apply_criteria") or {} for t in tracks}}


CSS = r"""
:root{color-scheme:light;--surface-0:#f4f4f1;--surface-1:#fcfcfb;--line:#e4e3de;--grid:#ecebe7;
--text-primary:#0b0b0b;--text-secondary:#52514e;--text-muted:#7a7974;--series-1:#2a78d6;--series-1-soft:#86b6ef;
--good:#0ca30c;--critical:#d03b3b;--chip:#efeee9;--chip-good-bg:#e5f4e5;--chip-good:#0a6e0a;--chip-warn-bg:#fcf0d7;
--chip-warn:#8a5a00;--chip-bad-bg:#fbe7e7;--chip-bad:#a32828}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--surface-0:#111110;--surface-1:#1a1a19;
--line:#2d2d2a;--grid:#262624;--text-primary:#fff;--text-secondary:#c3c2b7;--text-muted:#8f8e87;--series-1:#3987e5;
--series-1-soft:#184f95;--chip:#262624;--chip-good-bg:#16301a;--chip-good:#6fd37a;--chip-warn-bg:#33270f;--chip-warn:#f0b35a;
--chip-bad-bg:#3a1a1a;--chip-bad:#ff8f8f}}
:root[data-theme="dark"]{color-scheme:dark;--surface-0:#111110;--surface-1:#1a1a19;--line:#2d2d2a;--grid:#262624;
--text-primary:#fff;--text-secondary:#c3c2b7;--text-muted:#8f8e87;--series-1:#3987e5;--series-1-soft:#184f95;--chip:#262624;
--chip-good-bg:#16301a;--chip-good:#6fd37a;--chip-warn-bg:#33270f;--chip-warn:#f0b35a;--chip-bad-bg:#3a1a1a;--chip-bad:#ff8f8f}
*{box-sizing:border-box}
body{margin:0;background:var(--surface-0);color:var(--text-primary);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,sans-serif}
.wrap{max-width:1240px;margin:auto;padding:24px 16px 64px}
h1{font-size:22px;margin:0}h2{font-size:14px;font-weight:600;margin:0 0 2px}.sub{color:var(--text-secondary)}
.cap{color:var(--text-muted);font-size:12px;margin-bottom:10px}
.filters{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0;position:sticky;top:0;z-index:5;background:var(--surface-0);padding:8px 0}
.filters input,.filters select{padding:7px 10px;border:1px solid var(--line);border-radius:8px;background:var(--surface-1);color:var(--text-primary);font:inherit}
.filters input{flex:1;min-width:200px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(165px,1fr));gap:10px}
.kpi{background:var(--surface-1);border:1px solid var(--line);border-radius:12px;padding:14px}
.kpi .v{font-size:28px;font-weight:650;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.kpi .l{color:var(--text-secondary);font-size:12px}.kpi .d{color:var(--text-muted);font-size:12px;margin-top:2px}
.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:10px;margin-top:10px}
.panel{background:var(--surface-1);border:1px solid var(--line);border-radius:12px;padding:14px;min-width:0}
.s4{grid-column:span 4}.s6{grid-column:span 6}.s8{grid-column:span 8}.s12{grid-column:span 12}
@media (max-width:900px){.s4,.s6,.s8{grid-column:span 12}}
svg{display:block;width:100%;overflow:visible}svg text{fill:var(--text-secondary);font-size:11px}
.axis{stroke:var(--line)}.gridl{stroke:var(--grid)}.bar{fill:var(--series-1)}.bar.soft{fill:var(--series-1-soft)}
.bar:hover,.hit:hover+.bar{opacity:.8}.val{fill:var(--text-primary);font-size:11px;font-variant-numeric:tabular-nums}
.thresh{stroke:var(--text-muted);stroke-dasharray:3 3}.thresh.strong{stroke:var(--text-primary);stroke-width:2;stroke-dasharray:5 3}
#tip{position:fixed;pointer-events:none;background:var(--surface-1);border:1px solid var(--line);border-radius:8px;
padding:6px 9px;font-size:12px;box-shadow:0 4px 14px rgba(0,0,0,.15);display:none;z-index:20;max-width:260px}
#tip b{font-variant-numeric:tabular-nums}
table{width:100%;border-collapse:collapse;font-size:13px}
th{background:var(--surface-1);text-align:left;font-weight:600;color:var(--text-secondary);
padding:8px;border-bottom:1px solid var(--line);cursor:pointer;white-space:nowrap;user-select:none}
th.num,td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
td{padding:8px;border-bottom:1px solid var(--grid);vertical-align:top}
tr.row{cursor:pointer}tr.row:hover td{background:var(--surface-0)}
.pill{display:inline-block;min-width:38px;text-align:center;border-radius:999px;padding:1px 8px;font-weight:600;font-size:12px}
.p-strong{background:var(--chip-good-bg);color:var(--chip-good)}.p-good{background:var(--chip);color:var(--text-primary)}
.p-fair{background:var(--chip-warn-bg);color:var(--chip-warn)}
.chip{display:inline-block;background:var(--chip);border-radius:999px;padding:1px 8px;font-size:12px;margin:2px 2px 0 0}
.c-ok{background:var(--chip-good-bg);color:var(--chip-good)}.c-part{background:var(--chip-warn-bg);color:var(--chip-warn)}
.c-miss{background:var(--chip-bad-bg);color:var(--chip-bad)}
.detail td{background:var(--surface-0)}.detail .why{margin:6px 0;color:var(--text-primary)}
.acts{margin-top:8px;display:flex;gap:6px;flex-wrap:wrap}
.acts a,.acts button{font:inherit;font-size:12px;padding:4px 9px;border-radius:7px;border:1px solid var(--line);
background:var(--surface-1);color:var(--text-primary);cursor:pointer;text-decoration:none}
.muted{color:var(--text-muted)}.more{display:block;margin:12px auto 0;padding:7px 14px;border-radius:8px;border:1px solid var(--line);
background:var(--surface-1);color:var(--text-primary);font:inherit;cursor:pointer}
.runs td,.runs th{position:static;padding:5px 8px}
@media (max-width:700px){.hide-sm{display:none}}
"""

JS = r"""
const D = JSON.parse(document.getElementById('data').textContent);
const $ = s => document.querySelector(s), esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const LOC = {bay_area:'Bay Area', remote_us:'Remote (US)', remote_unclear:'Remote (region unclear)', us:'Other US', unknown:'Unknown'};
const match = j => j.ai ?? j.score;
const tier = s => s >= 82 ? 'Strong' : s >= 70 ? 'Good' : s >= 55 ? 'Fair' : 'Weak';
const mid = j => (j.payLo + j.payHi) / 2;
const money = v => v == null ? '—' : '$' + Math.round(v/1000) + 'K';
const median = a => { if (!a.length) return null; const s = [...a].sort((x,y)=>x-y), m = s.length>>1; return s.length%2 ? s[m] : (s[m-1]+s[m])/2; };
const ageDays = d => d ? Math.round((new Date(D.today) - new Date(d.slice(0,10))) / 864e5) : null;

// ---------- filters
const fams = [...new Set(D.jobs.map(j => j.family))].sort();
$('#trk').innerHTML += Object.entries(D.tracks).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join('');
if (Object.keys(D.tracks).length < 2) $('#trk').style.display = 'none';
$('#fam').innerHTML += fams.map(f => `<option>${esc(f)}</option>`).join('');
$('#locf').innerHTML += Object.entries(LOC).map(([k,v]) => `<option value="${k}">${v}</option>`).join('');
let sortKey = 'match', sortDir = -1, shown = 60, open = new Set();
function filtered() {
  const critOnly = $('#crit').value !== 'no', applyOnly = $('#crit').value === 'apply';
  const trk = $('#trk').value;
  const q = $('#q').value.toLowerCase(), fam = $('#fam').value, loc = $('#locf').value, min = +$('#min').value, st = $('#st').value;
  return D.jobs.filter(j => (!applyOnly || j.tier === 'apply') && (!critOnly || (!j.fails.length && !j.blocked && (j.status !== 'submitted' || st === 'submitted'))) && match(j) >= min && (!trk || j.track === trk) && (!fam || j.family === fam) && (!loc || j.loc === loc) && (!st || j.status === st) &&
    (!q || (j.company + ' ' + j.title + ' ' + j.location).toLowerCase().includes(q)));
}

// ---------- tooltip
const tip = $('#tip');
document.addEventListener('mousemove', e => {
  const t = e.target.closest('[data-tip]');
  if (!t) { tip.style.display = 'none'; return; }
  tip.innerHTML = t.dataset.tip; tip.style.display = 'block';
  const x = Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 8);
  tip.style.left = x + 'px'; tip.style.top = (e.clientY + 14) + 'px';
});

// ---------- charts (plain SVG)
function hbar(el, rows, {fmt = v => v, tipf} = {}) {
  if (!rows.length) { el.innerHTML = '<div class="muted">No data for these filters</div>'; return; }
  const W = el.clientWidth || 360, lw = Math.min(150, W * .38), bh = 16, gap = 8, h = rows.length * (bh + gap);
  const max = Math.max(...rows.map(r => r.v)), sx = v => (W - lw - 40) * v / max;
  el.innerHTML = `<svg viewBox="0 0 ${W} ${h}" height="${h}" role="img">` + rows.map((r, i) => {
    const y = i * (bh + gap), w = Math.max(2, sx(r.v)), tipTxt = esc(tipf ? tipf(r) : `<b>${fmt(r.v)}</b> ${esc(r.k)}`);
    return `<text x="${lw - 8}" y="${y + bh/2 + 4}" text-anchor="end">${esc(r.k.length > 22 ? r.k.slice(0,21)+'…' : r.k)}</text>
      <rect x="${lw}" y="${y}" width="${w}" height="${bh}" rx="4" class="bar"/>
      <text class="val" x="${lw + w + 6}" y="${y + bh/2 + 4}">${fmt(r.v)}</text>
      <rect x="0" y="${y - gap/2}" width="${W}" height="${bh + gap}" fill="transparent" data-tip="${tipTxt}"/>`;
  }).join('') + '</svg>';
}
function vhist(el, bins, {thresholds = [], xfmt = v => v, tipf} = {}) {
  const W = el.clientWidth || 500, H = 170, pb = 22, pt = 14, n = bins.length, bw = W / n;
  const max = Math.max(1, ...bins.map(b => b.v)), sy = v => (H - pb - pt) * v / max;
  let s = `<svg viewBox="0 0 ${W} ${H}" height="${H}" role="img">`;
  [.5, 1].forEach(f => { const y = H - pb - (H - pb - pt) * f; s += `<line class="gridl" x1="0" x2="${W}" y1="${y}" y2="${y}"/><text x="0" y="${y - 3}">${Math.round(max * f)}</text>`; });
  bins.forEach((b, i) => {
    const h = sy(b.v), x = i * bw + 1;
    if (b.v) s += `<path class="bar${b.soft ? ' soft' : ''}" d="M${x},${H-pb} v${-(h-4)} q0,-4 4,-4 h${bw-10} q4,0 4,4 v${h-4} z"/>`;
    s += `<rect x="${i*bw}" y="${pt}" width="${bw}" height="${H-pb-pt}" fill="transparent" data-tip="${esc(tipf(b))}"/>`;
    if (i % Math.ceil(n / 8) === 0) s += `<text x="${x}" y="${H - 6}">${xfmt(b.lo)}</text>`;
  });
  thresholds.forEach(([v, label, exact]) => {
    let x;
    if (exact) {   // position within the bin, not snapped to a bin edge
      const step = bins.length > 1 ? bins[1].lo - bins[0].lo : 1;
      x = Math.max(0, Math.min(W, (v - bins[0].lo) / step * bw));
    } else {
      const i = bins.findIndex(b => b.lo >= v); if (i < 0) return; x = i * bw;
    }
    s += `<line class="thresh${exact ? " strong" : ""}" x1="${x}" x2="${x}" y1="${pt - 6}" y2="${H - pb}"/><text x="${x + 4}" y="${pt}">${label}</text>`;
  });
  el.innerHTML = s + `<line class="axis" x1="0" x2="${W}" y1="${H-pb}" y2="${H-pb}"/></svg>`;
}
const countBy = (arr, key) => { const m = {}; arr.forEach(j => { const k = key(j); (m[k] ||= []).push(j); }); return m; };

// ---------- render
function render() {
  const J = filtered(), last = D.runs[0] || {};
  const strong = J.filter(j => match(j) >= 82).length, paid = J.filter(j => j.payHi);
  const newToday = J.filter(j => j.firstSeen === D.today).length;
  const trk = $('#trk').value, C = D.critByTrack[trk] || D.crit;
  const eligJ = D.jobs.filter(j => !j.fails.length && !j.blocked && j.status !== 'submitted' && (!trk || j.track === trk)),
    elig = eligJ.length, nApply = eligJ.filter(j => j.tier === 'apply').length, nb = D.jobs.filter(j => j.blocked && (!trk || j.track === trk)).length;
  $('#critline').textContent = `Criteria: posted in the last ${C.max_posted_hours}h · top of base pay above $${C.min_base_pay/1000}K · asks ${C.years_min}–${C.years_max} years`
    + (C.exclude_citizenship_required ? ' · no citizenship/clearance' : '') + (C.exclude_recent_layoffs_months ? ` · no layoffs in ${C.exclude_recent_layoffs_months} months` : '')
    + (C.min_match ? ` · match ${C.min_match}%+ (stretch ${C.stretch_match}%+)` : '') + D.caps.map(c => ` · ${c.company} above ${c.above}%`).join('')
    + (trk ? '' : Object.entries(D.overrides).filter(([, o]) => Object.keys(o).length)
        .map(([t, o]) => ` · ${t.toUpperCase()} track: ` + Object.entries(o).map(([k, v]) => `${k.replace(/_/g, ' ')} ${v}`).join(', ')).join(''));
  const kpis = [
    [`${nApply} + ${elig - nApply}`, 'Apply + stretch', `meet every rule · apply ${C.min_match}%+, stretch ${C.stretch_match}–${C.min_match - 1}%${nb ? ` · ${nb} at blocked companies excluded` : ''}`],
    [new Set(D.jobs.filter(j => j.layoffs.some(e => e.ok)).map(j => j.company)).size, 'Companies with recent layoffs',
     `${new Set(D.jobs.filter(j => j.layoffChecked).map(j => j.company)).size} companies web-researched · rest CA + WA WARN only`],
    [last.scanned?.toLocaleString() ?? '—', 'Postings scanned', last.boards ? `${last.boards} boards · last run ${last.ts.slice(5,16)}` : 'no scrape recorded'],
    [J.length.toLocaleString(), 'Roles shown', $('#crit').value === 'yes' ? 'meeting apply criteria + filters' : 'after role / location filters'],
    [newToday, 'New today', `first seen ${D.today}`],
    [strong, 'Strong matches', `score 82+ · ${J.filter(j => match(j) >= 70).length} are Good or better`],
    [J.length ? Math.round(median(J.map(match))) + '%' : '—', 'Median match', `${J.filter(j => j.ai != null).length} re-ranked by Claude`],
    [money(median(paid.map(mid))), 'Median base pay', `midpoint of each posted range · pay listed on ${J.length ? Math.round(100 * paid.length / J.length) : 0}% of roles`],
    [(D.statusAll.tailored || 0) + (D.statusAll.prefilled || 0), 'Resumes tailored', `${D.statusAll.submitted || 0} submitted`],
    ...D.caps.map(c => [`${c.max - c.used}/${c.max}`, `${c.company} applications left`, `in ${c.window} days · only matches above ${c.above}%`]),
  ];
  $('#kpis').innerHTML = kpis.map(([v, l, d]) => `<div class="kpi"><div class="v">${v}</div><div class="l">${l}</div><div class="d">${d}</div></div>`).join('');

  const sb = []; for (let lo = 40; lo < 100; lo += 5) { const js = J.filter(j => match(j) >= lo && match(j) < lo + 5 + (lo === 95 ? 1 : 0)); sb.push({lo, v: js.length, soft: lo < 70}); }
  vhist($('#c-score'), sb, {thresholds: [[70, 'Good'], [82, 'Strong']], xfmt: v => v + '%', tipf: b => `<b>${b.v}</b> roles scored ${b.lo}–${b.lo + 4}%`});

  const byCo = Object.entries(countBy(J, j => j.company)).map(([k, js]) => ({k, v: js.length, avg: Math.round(js.reduce((a, j) => a + match(j), 0) / js.length), top: Math.max(...js.map(match))}))
    .sort((a, b) => b.v - a.v || b.avg - a.avg).slice(0, 15);
  hbar($('#c-company'), byCo, {tipf: r => `<b>${r.v}</b> roles at ${esc(r.k)}<br>avg match ${r.avg}% · best ${r.top}%`});

  const withAvg = m => Object.entries(m).map(([k, js]) => ({k, v: js.length, avg: Math.round(js.reduce((a, j) => a + match(j), 0) / js.length)})).sort((a, b) => b.v - a.v);
  hbar($('#c-family'), withAvg(countBy(J, j => j.family)), {tipf: r => `<b>${r.v}</b> ${esc(r.k)} roles · avg match ${r.avg}%`});
  hbar($('#c-loc'), withAvg(countBy(J, j => LOC[j.loc] || j.loc)), {tipf: r => `<b>${r.v}</b> in ${esc(r.k)} · avg match ${r.avg}%`});

  const mids = paid.map(mid), pmed = median(mids);
  const pb = []; for (let lo = 100000; lo < 500000; lo += 25000) { const n = mids.filter(v => v >= lo && (v < lo + 25000 || lo === 475000)).length; pb.push({lo, v: n}); }
  vhist($('#c-pay'), pb, {xfmt: v => '$' + v / 1000 + 'K', thresholds: pmed ? [[pmed, `Median ${money(pmed)}`, true]] : [],
    tipf: b => `<b>${b.v}</b> roles with a range midpoint of ${money(b.lo)}–${money(b.lo + 24999)}${b.lo === 475000 ? '+' : ''}`});
  $('#pay-cap').textContent = `Midpoint of each posted base range, ${paid.length} of ${J.length} roles; dashed line = median. Where a posting lists several geo zones, the highest zone is used (usually Bay Area / NYC).`;

  const order = ['new', 'shortlisted', 'tailored', 'prefilled', 'submitted'];
  hbar($('#c-pipe'), order.map(k => ({k, v: J.filter(j => j.status === k).length})).filter(r => r.v || r.k !== 'prefilled'), {tipf: r => `<b>${r.v}</b> roles ${r.k}`});

  $('#runs').innerHTML = D.runs.length ? `<table class="runs"><tr><th>Run</th><th class="num">Boards</th><th class="num">Scanned</th><th class="num">Relevant</th><th class="num">New</th><th class="num">Errors</th></tr>` +
    D.runs.map(r => `<tr><td>${esc(r.ts.slice(0,16))}</td><td class="num">${r.boards}</td><td class="num">${r.scanned.toLocaleString()}</td><td class="num">${r.kept}</td><td class="num">${r.new}</td><td class="num">${r.errors}</td></tr>`).join('') + '</table>'
    : '<div class="muted">No runs yet — run <code>jsb scrape</code>.</div>';

  renderTable(J);
}

const COLS = [['match', 'Match', 1], ['ai', 'Claude', 1], ['company', 'Company'], ['title', 'Role'], ['location', 'Location', 0, 1], ['payHi', 'Pay (base)', 1], ['posted', 'Posted', 1, 1], ['status', 'Status', 0, 1]];
function renderTable(J) {
  const val = (j, k) => k === 'match' ? match(j) : k === 'posted' ? -(j.hours ?? 1e9) : j[k];
  J = [...J].sort((a, b) => { const x = val(a, sortKey), y = val(b, sortKey); return x == null ? 1 : y == null ? -1 : (x > y ? 1 : x < y ? -1 : 0) * sortDir; });
  $('#count').textContent = `${J.length} roles`;
  const head = '<tr>' + COLS.map(([k, l, num, sm]) => `<th data-k="${k}" class="${num ? 'num' : ''} ${sm ? 'hide-sm' : ''}">${l}${sortKey === k ? (sortDir < 0 ? ' ↓' : ' ↑') : ''}</th>`).join('') + '</tr>';
  const body = J.slice(0, shown).map(j => {
    const m = match(j), cls = m >= 82 ? 'p-strong' : m >= 70 ? 'p-good' : 'p-fair', age = ageDays(j.posted);
    let row = `<tr class="row" data-id="${j.id}"><td class="num"><span class="pill ${cls}" title="${tier(m)}">${Math.round(m)}%</span></td>
      <td class="num">${j.ai != null ? Math.round(j.ai) + (j.verdict ? ` <span class="muted">${esc(j.verdict)}</span>` : '') : '<span class="muted">—</span>'}</td>
      <td>${esc(j.company)}${!j.fails.length && !j.blocked ? ` <span class="chip ${j.tier === 'apply' ? 'c-ok' : 'c-part'}">${j.tier}</span>` : ''}${j.blocked ? `<div><span class="chip c-miss">⛔ Blocked: ${esc(j.blocked)}</span></div>` : ''}</td><td>${esc(j.title)}${j.fails.length ? ` <span class="chip c-part" title="outside apply criteria">${esc(j.fails.join(' · '))}</span>` : ''}${j.flags.length ? ` <span class="chip c-miss">⚠ ${esc(j.flags.join(' · '))}</span>` : ''}</td>
      <td class="hide-sm">${esc(LOC[j.loc] || '')}<div class="muted">${esc(j.location.slice(0, 40))}</div></td>
      <td class="num">${j.payHi ? money(j.payLo) + '–' + money(j.payHi) : '<span class="muted">—</span>'}</td>
      <td class="num hide-sm">${j.hours == null ? '—' : j.hours < 72 ? Math.round(j.hours) + 'h' : Math.round(j.hours / 24) + 'd'}</td><td class="hide-sm">${esc(j.status)}</td></tr>`;
    if (open.has(j.id)) row += `<tr class="detail"><td colspan="8">
      <div>${j.matched.map(s => `<span class="chip c-ok">${esc(s)}</span>`).join('')}${j.partial.map(s => `<span class="chip c-part" title="exposure only">${esc(s)}</span>`).join('')}${j.missing.map(s => `<span class="chip c-miss" title="no evidence">${esc(s)}</span>`).join('')}</div>
      ${j.why ? `<div class="why"><b>Claude${j.runs ? ` (runs ${j.runs.join('/')})` : ''}:</b> ${esc(j.why)}</div>` : '<div class="muted" style="margin-top:6px">Not re-ranked yet: <code>jsb rerank</code></div>'}
      ${j.gaps.length ? `<div class="muted">Gaps: ${esc(j.gaps.join('; '))}</div>` : ''}
      ${j.dealbreakers.length ? `<div style="color:var(--critical)">Dealbreakers: ${esc(j.dealbreakers.join('; '))}</div>` : ''}
      ${j.years ? `<div class="muted">Asks for ${j.years}+ years</div>` : ''}
      ${j.layoffs.length ? `<div style="margin-top:6px"><b>Recent layoffs:</b> ${j.layoffs.map(e => `${esc(e.date)}${e.n ? ` · ${e.n} people` : ''} · ${esc(e.src)}${e.where ? ` · ${esc(e.where)}` : ''}${e.ok ? '' : ' · <span class="chip c-part">unconfirmed, not excluding</span>'}${e.url ? ` <a href="${esc(e.url)}" target="_blank" rel="noopener">source</a>` : ''}`).join('<br>')}</div>`
        : `<div class="muted" style="margin-top:6px">${j.layoffChecked ? 'No layoffs found in the last 6 months (web research + CA/WA WARN)' : 'No layoffs in CA/WA WARN; web research not run yet — <code>jsb layoffs research</code>'}</div>`}
      <div class="acts"><a href="${esc(j.url)}" target="_blank" rel="noopener">View posting</a>
        <button data-cmd="jsb tailor ${j.id}">Copy: jsb tailor ${j.id}</button><button data-cmd="jsb apply ${j.id}">Copy: jsb apply ${j.id}</button>
        <button data-cmd="jsb mark ${j.id} skipped">Copy: skip</button><span class="muted">#${j.id} · ${esc(j.ats)}</span></div></td></tr>`;
    return row;
  }).join('');
  $('#tbl').innerHTML = head + body;
  $('#more').style.display = J.length > shown ? '' : 'none';
}

$('#tbl').addEventListener('click', e => {
  const th = e.target.closest('th');
  if (th) { const k = th.dataset.k; sortDir = sortKey === k ? -sortDir : (['company', 'title', 'location', 'status'].includes(k) ? 1 : -1); sortKey = k; renderTable(filtered()); return; }
  const b = e.target.closest('button[data-cmd]');
  if (b) { navigator.clipboard.writeText(b.dataset.cmd); const t = b.textContent; b.textContent = 'Copied'; setTimeout(() => b.textContent = t, 1200); return; }
  const tr = e.target.closest('tr.row');
  if (tr && !e.target.closest('a')) { const id = +tr.dataset.id; open.has(id) ? open.delete(id) : open.add(id); renderTable(filtered()); }
});
$('#more').onclick = () => { shown += 60; renderTable(filtered()); };
['#q', '#crit', '#trk', '#fam', '#locf', '#min', '#st'].forEach(s => $(s).addEventListener('input', () => { shown = 60; render(); }));
let rt; addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(render, 150); });
render();
"""


def build(conn) -> str:
    data = json.dumps(_data(conn)).replace("</", "<\\/")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Job Search Dashboard</title><style>{CSS}</style></head>
<body><div class="wrap">
<h1>Job search dashboard</h1>
<div class="sub">Roles ranked against your base resume, per search track · generated {date.today().isoformat()}</div>
<div class="cap" id="critline" style="margin-top:6px"></div>
<div class="filters">
 <select id="crit"><option value="yes">Meets criteria (apply + stretch)</option><option value="apply">Apply tier only</option><option value="no">All scored jobs</option></select>
 <input id="q" placeholder="Search company, role, location">
 <select id="trk"><option value="">All tracks</option></select>
 <select id="fam"><option value="">All role types</option></select>
 <select id="locf"><option value="">All locations</option></select>
 <select id="st"><option value="">Any status</option><option>new</option><option>shortlisted</option><option>tailored</option><option>prefilled</option><option>submitted</option></select>
 <select id="min"><option value="0" selected>Any match</option><option value="55">Fair+ (55%)</option><option value="70">Good+ (70%)</option><option value="82">Strong (82%)</option></select>
</div>
<div class="kpis" id="kpis"></div>
<div class="grid">
 <div class="panel s8"><h2>Match score distribution</h2><div class="cap">Claude's score where re-ranked, otherwise the rule score</div><div id="c-score"></div></div>
 <div class="panel s4"><h2>Application pipeline</h2><div class="cap">Where the filtered roles are</div><div id="c-pipe"></div></div>
 <div class="panel s6"><h2>Top companies</h2><div class="cap">Number of matching roles (hover for avg and best match)</div><div id="c-company"></div></div>
 <div class="panel s6"><h2>Base pay (range midpoint)</h2><div class="cap" id="pay-cap"></div><div id="c-pay"></div>
   <h2 style="margin-top:18px">Role type</h2><div class="cap">Number of roles</div><div id="c-family"></div></div>
 <div class="panel s6"><h2>Location</h2><div class="cap">Number of roles</div><div id="c-loc"></div></div>
 <div class="panel s6"><h2>Scrape runs</h2><div class="cap">Most recent first</div><div id="runs"></div></div>
 <div class="panel s12"><h2>Jobs</h2><div class="cap"><span id="count"></span> · click a column to sort, a row for skills and next steps.
   Green = evidenced skill, amber = exposure only, red = no evidence.</div>
   <div style="overflow-x:auto"><table id="tbl"></table></div><button class="more" id="more">Show more</button></div>
</div></div>
<div id="tip"></div>
<script type="application/json" id="data">{data}</script>
<script>{JS}</script></body></html>"""


def write(conn) -> str:
    REPORTS_DIR.mkdir(exist_ok=True)
    path = REPORTS_DIR / "matches.html"
    path.write_text(build(conn))
    return str(path)
