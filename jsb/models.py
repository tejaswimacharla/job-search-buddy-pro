from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Job:
    ats: str                 # greenhouse | lever | ashby | workday | smartrecruiters | manual
    company: str
    ext_id: str
    title: str
    location: str
    url: str
    apply_url: str
    description: str         # plain text
    posted_at: str | None = None   # ISO date if known
    remote: bool = False
    compensation: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        return f"{self.ats}:{self.company.lower().replace(' ', '')}:{self.ext_id}"
