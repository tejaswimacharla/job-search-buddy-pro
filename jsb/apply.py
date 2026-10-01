"""Assisted apply: open the application form in Chrome, pre-fill the standard fields,
attach the tailored resume, then hand control to you.

This never clicks Submit. Questions about work authorization, sponsorship, salary,
demographics, and free-text answers are left for you on purpose: the RTA profile
deliberately doesn't store some of them, and answering them wrong can't be undone.
"""
from __future__ import annotations

import re
from pathlib import Path

from .config import ROOT
from .profile import Profile

PROFILE_DIR = ROOT / ".browser-profile"

# label regex -> profile key. First match wins, so order matters.
FIELD_RULES: list[tuple[str, str]] = [
    (r"\bfirst\b.*\bname\b|given name|preferred first", "first_name"),
    (r"\blast\b.*\bname\b|surname|family name", "last_name"),
    (r"^\s*(full )?name\s*\*?$|your name|_systemfield_name|legal name", "name"),
    (r"e-?mail", "email"),
    (r"phone|mobile", "phone"),
    (r"linkedin", "linkedin"),
    (r"github", "github"),
    (r"current (company|employer)|^\s*org\s*$|company name|most recent(ly)? (worked|employer|company)|"
     r"current or most recent (employer|company)|where (do|did) you (currently )?work", "current_company"),
    (r"current (job )?title|^title$", "current_title"),
    (r"^\s*(location|city|current location)\b|where are you (located|based)", "location"),
]
SKIP = re.compile(r"authori[sz]|sponsor|visa|citizen|gender|race|ethnic|veteran|disab|hispanic|"
                  r"salary|compensation|pronoun|how did you hear|referr|cover letter|website|portfolio", re.I)

COLLECT_JS = """
() => {
  const out = [];
  const labelFor = (el) => {
    const bits = [];
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) bits.push(l.innerText); }
    const wrap = el.closest('label'); if (wrap) bits.push(wrap.innerText);
    const lb = el.getAttribute('aria-labelledby');
    if (lb) lb.split(' ').forEach(id => { const n = document.getElementById(id); if (n) bits.push(n.innerText); });
    ['aria-label', 'placeholder', 'name', 'id'].forEach(a => { if (el.getAttribute(a)) bits.push(el.getAttribute(a)); });
    if (!bits.length || bits.every(b => /^[\\w\\[\\]-]+$/.test(b))) {
      // Ashby/Lever: label is a sibling in the same field container.
      const box = el.closest('[class*="field"], [class*="question"], li, fieldset');
      const l = box && box.querySelector('label, legend, [class*="label"]'); if (l) bits.unshift(l.innerText);
    }
    return bits.join(' | ').replace(/\\s+/g, ' ').trim().slice(0, 200);
  };
  document.querySelectorAll('input, textarea').forEach((el, i) => {
    const t = (el.type || 'text').toLowerCase();
    if (['hidden', 'submit', 'button', 'checkbox', 'radio'].includes(t)) return;
    if (t !== 'file' && el.offsetParent === null) return;
    el.setAttribute('data-jsb-idx', String(i));
    const combo = el.getAttribute('role') === 'combobox' || el.hasAttribute('aria-autocomplete') || el.readOnly;
    out.push({idx: i, type: t, label: labelFor(el), required: el.required || el.getAttribute('aria-required') === 'true',
              filled: !!el.value, combo});
  });
  return out;
}
"""


def _values(profile: Profile) -> dict[str, str]:
    ident = profile.identity
    cur = profile.experiences[0] if profile.experiences else {}
    return {
        "first_name": ident.get("first_name", ""), "last_name": ident.get("last_name", ""),
        "name": ident.get("name", ""), "email": ident.get("email", ""), "phone": ident.get("phone", ""),
        "linkedin": ident.get("linkedin", ""), "github": ident.get("github", ""),
        "location": ident.get("location", ""),
        "current_company": cur.get("company", "") if str(cur.get("end", "")).lower() == "present" else "",
        "current_title": cur.get("title", "") if str(cur.get("end", "")).lower() == "present" else "",
    }


def prefill(apply_url: str, resume_pdf: Path | None, profile: Profile, cover_letter: Path | None = None) -> None:
    from playwright.sync_api import sync_playwright

    values = _values(profile)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(PROFILE_DIR), channel="chrome", headless=False,
                                                   viewport=None, args=["--start-maximized"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(apply_url, wait_until="domcontentloaded")
        try:
            page.wait_for_selector("input, textarea", timeout=15000)  # Ashby renders client-side
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(1500)
        _open_form_if_needed(page)

        filled, todo, resume_done = fill_form(page, values, resume_pdf, cover_letter)

        print("\nPre-filled:")
        for x in filled:
            print(f"  + {x}")
        if not resume_done:
            print("  ! Resume was NOT attached automatically. Attach it manually:", resume_pdf)
        print("\nNeeds your answer (required or skipped on purpose):")
        for x in dict.fromkeys(todo):
            print(f"  - {x}")
        print("  - Also check dropdowns, radio buttons and checkboxes (not auto-filled).")
        print("\nReview everything in the Chrome window and click Submit yourself when it's right.")
        input("Press Enter here once you're done with the form (the browser will close)... ")
        ctx.close()


def fill_form(page, values: dict[str, str], resume_pdf: Path | None, cover_letter: Path | None = None):
    """Fill standard fields on the current page. Returns (filled, todo, resume_attached). Never submits."""
    fields = page.evaluate(COLLECT_JS)
    filled, todo, used_keys = [], [], set()
    resume_done = False
    has_resume_input = any(f["type"] == "file" and re.search(r"resume|\bcv\b", f["label"], re.I) for f in fields)
    for f in fields:
        loc = page.locator(f'[data-jsb-idx="{f["idx"]}"]')
        label = _short_label(f["label"])
        if f["type"] == "file":
            target = None
            if re.search(r"cover", f["label"], re.I):
                target = cover_letter
            elif re.search(r"resume|\bcv\b", f["label"], re.I) or (not has_resume_input and not resume_done):
                target = resume_pdf
            if target and target.exists():
                try:
                    loc.set_input_files(str(target))
                    filled.append(f"{label[:50]} <- {target.name}")
                    resume_done = resume_done or target == resume_pdf
                except Exception as e:  # noqa: BLE001
                    todo.append(f"upload failed ({label[:50]}): {e}")
            continue
        if f["filled"] or f["combo"] or SKIP.search(f["label"]):
            if f["required"] and not f["filled"] and label:
                todo.append(label)
            continue
        key = next((k for rx, k in FIELD_RULES if re.search(rx, label, re.I)), None) \
            or next((k for rx, k in FIELD_RULES if re.search(rx, f["label"], re.I)), None)
        val = values.get(key or "", "")
        if val and key not in used_keys:
            try:
                loc.fill(val)
                used_keys.add(key)
                filled.append(f"{label[:50]} <- {val}")
            except Exception:  # noqa: BLE001
                todo.append(label)
        elif f["required"]:
            todo.append(label)
    return filled, todo, resume_done


def _short_label(label: str) -> str:
    """'Full name ✱ | name' -> 'Full name'"""
    return re.sub(r"[✱*]", "", label.split("|")[0]).strip()


def _open_form_if_needed(page) -> None:
    """Some job pages show the description first and the form behind an 'Apply' button."""
    if page.locator('input[type="file"]').count():
        return
    for text in ["Apply for this job", "Apply Now", "Apply now", "Apply"]:
        btn = page.get_by_role("button", name=text).or_(page.get_by_role("link", name=text))
        if btn.count():
            # Never press anything that could submit a form.
            if btn.first.evaluate("el => el.type === 'submit' || !!el.closest('form')"):
                continue
            try:
                btn.first.click()
                page.wait_for_timeout(2500)
                return
            except Exception:  # noqa: BLE001
                continue
