"""Run: .venv/bin/python tests/test_fill.py  — fills a LOCAL fixture with dummy values."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from playwright.sync_api import sync_playwright  # noqa: E402

from jsb.apply import fill_form  # noqa: E402

here = Path(__file__).parent
dummy = {"first_name": "Test", "last_name": "User", "name": "Test User", "email": "test@example.com",
         "phone": "555-0100", "linkedin": "https://linkedin.com/in/test", "github": "", "location": "Nowhere",
         "current_company": "ExampleCo", "current_title": "Engineer"}
pdf = here / "dummy.pdf"
pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    page = b.new_page()
    page.goto((here / "fixture_form.html").as_uri())
    filled, todo, resume = fill_form(page, dummy, pdf, None)
    vals = page.evaluate("() => Object.fromEntries([...document.querySelectorAll('input:not([type=file]),textarea')].map((e,i)=>[e.id||e.name||i, e.value]))")
    files = page.evaluate("() => [...document.querySelectorAll('input[type=file]')].map(e => (e.name||e.id) + '=' + e.files.length)")
    b.close()
pdf.unlink()
print("filled:", *filled, sep="\n  ")
print("todo:", *todo, sep="\n  ")
print("values:", vals)
print("files:", files)
assert vals["first_name"] == "Test" and vals["last_name"] == "User" and vals["name"] == "Test User"
assert vals["phone"] == "555-0100" and vals["li"].startswith("https://linkedin") and vals["org"] == "ExampleCo"
assert vals["auth"] == "" and vals["sp"] == "" and vals["why"] == "", "sensitive/free-text fields must stay empty"
assert "_systemfield_resume=1" in files and "autofill=0" in files, files
assert vals["4"] == "", "combobox left alone"
print("OK")
