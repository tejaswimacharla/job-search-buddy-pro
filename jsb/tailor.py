"""Hand a job off to the Resume Tailoring Assistant (RTA).

1. Writes the JD into RTA's job_descriptions/incoming/ in the same format as the ones
   you've been saving by hand.
2. Runs Claude Code headlessly inside the RTA project to follow skills/analyze_jd.md
   and skills/make_resume.md, so the RTA truth policy and audit trail apply unchanged.
"""
from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

from .models import Job
from .text import slugify

TAILOR_PROMPT = """You are running non-interactively from job-search-buddy. Read CLAUDE.md and
config/truth_policy.md first; they override everything here.

Job description file: {jd_path}
Target role type: {target}. Lead with the evidence that matters for this kind of role
(for a data science role: modeling, experimentation, analysis and measurable outcomes before
infrastructure detail; for a data engineering role: pipelines, data volumes, latency, data
quality and the storage/processing stack), but only claim what the knowledge base evidences.

1. Follow skills/analyze_jd.md on that file.
2. If config/tailoring_preferences.md says to stop and ask (critical unclear requirement, or
   fewer than 3 direct-match themes), do NOT generate a resume. Write the questions to the
   analyzed folder's clarifying_questions.md and finish with the line
   `JSB_STATUS: needs_input <path to analyzed folder>`.
3. Otherwise follow skills/make_resume.md to build the package in output/{slug}_{today}/,
   then compile tailored_resume.tex with pdflatex into resume_tejaswi_macharla.pdf in that
   folder (run pdflatex twice with -interaction=nonstopmode, delete .aux/.log/.out afterwards).
   Respect the page length in config/formatting_preferences.md.
4. Do not submit anything anywhere, and do not edit application_tracker.md.
5. Finish with exactly one line: `JSB_STATUS: ready output/{slug}_{today}/`
"""


def slug_for(job: Job) -> str:
    return f"{slugify(job.company)}_{slugify(job.title)}"


def write_jd(job: Job, rta: Path) -> Path:
    path = rta / "job_descriptions" / "incoming" / f"{slug_for(job)}.txt"
    header = [
        job.title,
        f"{job.company} - {job.location or 'Location not listed'}" + (" (Remote)" if job.remote else ""),
        f"Source: {job.url}",
    ]
    if job.apply_url and job.apply_url != job.url:
        header.append(f"Apply: {job.apply_url}")
    if job.compensation:
        header.append(f"Pay: {job.compensation}")
    if job.posted_at:
        header.append(f"Posted: {job.posted_at}")
    path.write_text("\n".join(header) + "\n\n" + job.description.strip() + "\n")
    return path


def run_tailoring(job: Job, rta: Path, model: str | None = None, stream=True,
                  target: str | None = None) -> tuple[str, str]:
    """Returns (status, path) where status is 'ready', 'needs_input' or 'error'."""
    jd_path = write_jd(job, rta)
    prompt = TAILOR_PROMPT.format(jd_path=jd_path.relative_to(rta), slug=slug_for(job),
                                  target=target or "Senior Software Engineer",
                                  today=date.today().isoformat())
    cmd = ["claude", "-p", "--permission-mode", "acceptEdits",
           "--allowedTools", "Read,Write,Edit,Glob,Grep,Bash(pdflatex:*),Bash(rm:*),Bash(ls:*),Bash(pdfinfo:*)"]
    if model:
        cmd += ["--model", model]
    proc = subprocess.Popen(cmd, cwd=rta, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    proc.stdin.write(prompt)
    proc.stdin.close()
    lines = []
    for line in proc.stdout:
        lines.append(line)
        if stream:
            print(line, end="")
    proc.wait()
    for line in reversed(lines):
        if line.startswith("JSB_STATUS:"):
            _, status, *rest = line.split()
            out = " ".join(rest)
            # LaTeX leftovers the headless run sometimes can't delete itself.
            for ext in ("aux", "log", "out"):
                for f in (rta / out).glob(f"*.{ext}") if out else []:
                    f.unlink(missing_ok=True)
            return status, out
    return "error", "".join(lines[-20:])


def find_pdf(rta: Path, output_rel: str) -> Path | None:
    folder = rta / output_rel
    pdfs = sorted(folder.glob("*.pdf"))
    return pdfs[0] if pdfs else None
