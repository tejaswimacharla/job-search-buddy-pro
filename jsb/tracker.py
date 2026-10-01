"""Append/update rows in the RTA's application_tracker.md (the format from skills/application_tracker.md)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

HEADER = ("| Company | Role | JD Source | Date Tailored | Resume Variant | Cover Letter | Strongest Matches "
          "| Known Gaps | Follow-up Questions | Status | Interview Notes | Next Actions |")


def _cell(s: str) -> str:
    return (s or "—").replace("|", "/").replace("\n", " ")


def record(rta: Path, *, company: str, role: str, jd_source: str, output_rel: str, matches: list[str],
           gaps: list[str], status: str, next_actions: str) -> None:
    """Status must come from the user (the tracker skill forbids inferring 'submitted')."""
    path = rta / "application_tracker.md"
    lines = path.read_text().splitlines()
    cover = "yes" if list((rta / output_rel).glob("cover_letter*")) else "none"
    row = "| " + " | ".join(_cell(x) for x in [
        company, role, jd_source, date.today().isoformat(), output_rel, cover,
        ", ".join(matches[:4]), ", ".join(gaps[:4]), "see gaps_and_questions.md", status, "—", next_actions,
    ]) + " |"
    for i, line in enumerate(lines):
        if line.startswith(f"| {_cell(company)} | {_cell(role)} |"):
            old = [c.strip() for c in line.strip("|").split("|")]
            new = [c.strip() for c in row.strip("|").split("|")]
            new[10] = old[10] if len(old) > 10 else new[10]   # keep interview notes
            lines[i] = "| " + " | ".join(new) + " |"
            break
    else:
        lines.append(row)
    path.write_text("\n".join(lines) + "\n")
