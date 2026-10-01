from __future__ import annotations

import requests

USER_AGENT = "job-search-buddy/0.1 (personal job search; public job-board APIs)"


def session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s
