"""Run: .venv/bin/python tests/test_tailor.py  — truth audit, fallback and page fitting with a
synthetic resume (no Claude calls; rendering needs Chrome or Playwright's Chromium)."""
import copy
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jsb import resume as base  # noqa: E402
from jsb.tailor import assemble, audit, fallback, fit_and_render  # noqa: E402

res = base._with_ids({
    "identity": {"name": "Test User", "email": "test@example.com"},
    "summary": "",
    "experience": [
        {"company": "NewCo", "title": "Engineer", "start": "2023-01", "end": "present",
         "bullets": ["Contributed to a payments API in Go serving 2M requests per day",
                     "Reduced p99 latency by 40% with Redis caching"]},
        {"company": "OldCo", "title": "Engineer", "start": "2019-01", "end": "2022-12",
         "bullets": ["Led migration of 6 services to Kubernetes", "Wrote Python tooling for on-call"]},
    ],
    "skills": [{"group": "Skills", "items": ["Go", "Redis", "Kubernetes", "Python"]}],
    "education": [{"school": "State U", "degree": "BS Computer Science"}],
})
ok = []


def check(cond, msg):
    ok.append(cond)
    print(("ok  " if cond else "FAIL") + " " + msg)


draft = {
    "summary": {"text": "", "sources": []},
    "experience": [
        {"id": "E1", "bullets": [
            {"text": "Led the payments API in Go serving 2M requests per day", "sources": ["E1.B1"]},   # verb upgrade
            {"text": "Cut p99 latency by 45% with Redis caching", "sources": ["E1.B2"]},              # changed number
            {"text": "Migrated 6 services to Kubernetes", "sources": ["E2.B1"]},                      # other role's work
            {"text": "Reduced p99 latency by 40% with Redis caching — fast.", "sources": ["E1.B2"]}, # style only
        ]},
        {"id": "E2", "bullets": [{"text": "Led migration of 6 services to Kubernetes", "sources": ["E2.B1"]},
                                 {"text": "Built tooling", "sources": ["E9.B9"]}]},                  # unknown source
    ],
    "skills": [{"group": "Core", "items": ["Go", "Kubernetes", "Terraform"]}],                        # Terraform not on resume
}
doc = assemble(draft, res)
issues = audit(doc, res, years=7)
kinds = {(i["where"], i["kind"]) for i in issues}
check(("E1#1", "ownership upgrade") in kinds, "flags 'contributed to' upgraded to 'led'")
check(("E1#2", "number not in source") in kinds, "flags 40% changed to 45%")
check(("E1#3", "cross-role source") in kinds, "flags a bullet citing another role")
check(("E2#2", "unknown source") in kinds, "flags an unknown source ID")
check(("E2#1", "ownership upgrade") not in kinds, "allows 'led' when the source says led")
check(not any(i["where"] == "E1#4" for i in issues), "style-only bullet passes")
check(doc["experience"][0]["bullets"][3]["text"] == "Reduced p99 latency by 40% with Redis caching, fast",
      "em dash and trailing period fixed in place")
check(doc["skills"][0]["items"] == ["Go", "Kubernetes"], "drops skills that aren't on the resume")

fallback(doc, res, [i for i in issues if i["severity"] == "fix"])
e1 = doc["experience"][0]["bullets"]
check(e1[0]["text"] == res["experience"][0]["bullets"][0]["text"] and e1[0]["reverted"], "failed bullet reverts to original")
check(not any(b["sources"] == ["E2.B1"] for b in e1), "cross-role bullet doesn't survive in E1")
check(all(i["severity"] != "fix" for i in audit(copy.deepcopy(doc), res, 7)), "document passes the audit after fallback")

# A long resume must still fit on one page, dropping the least relevant bullets of older roles first.
long = copy.deepcopy(doc)
filler = ("Implemented a service component with careful testing and staged rollout across several teams and "
          "regions, coordinating dependencies and documenting the operational runbooks for on-call engineers")
for r in long["experience"]:
    r["bullets"] = [{"text": f"{filler} ({k})", "sources": []} for k in range(20)]
with tempfile.TemporaryDirectory() as d:
    dropped = fit_and_render(long, Path(d) / "r.pdf", 1)
    import pdfplumber
    with pdfplumber.open(Path(d) / "r.pdf") as pdf:
        check(len(pdf.pages) == 1 and dropped, f"overlong resume fitted to 1 page ({len(dropped)} bullets dropped)")
n_new, n_old = len(long["experience"][0]["bullets"]), len(long["experience"][1]["bullets"])
check(n_new >= n_old and dropped[0].startswith("E2#"), f"older role trimmed first ({n_new} vs {n_old} bullets kept)")

print("OK" if all(ok) else f"{ok.count(False)} failures")
sys.exit(0 if all(ok) else 1)
