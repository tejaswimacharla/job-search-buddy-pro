"""Pull an annual base-pay range out of a posting (structured field first, then the JD text)."""
from __future__ import annotations

import re

_NUM = r"\d{2,3}(?:,\d{3})+(?:\.\d+)?|\d{2,3}(?:\.\d+)?\s?[kK]"
RANGE = re.compile(rf"\$\s?({_NUM})\s*(?:USD\s*)?(?:-|–|—|to)\s*\$?\s?({_NUM})")


def _val(s: str) -> float:
    s = s.replace(",", "").strip()
    return float(s[:-1].strip()) * 1000 if s[-1] in "kK" else float(s)


def parse(compensation: str, description: str) -> tuple[int, int] | None:
    """Returns (min, max) in USD/year. With several geo-zone ranges, takes the highest zone
    (usually the Bay Area / NYC band)."""
    best = None
    for text in (compensation or "", description or ""):
        for m in RANGE.finditer(text):
            lo, hi = _val(m.group(1)), _val(m.group(2))
            if 50_000 <= lo <= hi <= 1_500_000 and (best is None or hi > best[1]):
                best = (int(lo), int(hi))
        if best:
            return best
    return None
