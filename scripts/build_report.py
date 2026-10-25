"""Render docs/report.md to docs/report.html and docs/report.pdf (headless Chromium via Playwright).

    pip install markdown playwright && playwright install chromium
    python scripts/build_report.py
"""

import re
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

CSS = """
@page { size: A4; margin: 18mm 16mm 18mm 16mm; }
body { font: 10.5pt/1.5 "Figtree", "Helvetica Neue", Arial, sans-serif; color: #1d1a16; background: #fff; }
h1 { font: 600 24pt/1.15 "Fraunces", Georgia, serif; letter-spacing: -0.02em; margin: 0 0 4mm; }
h2 { font: 600 15pt/1.2 "Fraunces", Georgia, serif; margin: 8mm 0 2mm; border-top: 1px solid #ddd5c8; padding-top: 4mm; }
h3 { font: 600 11.5pt/1.3 "Figtree", Arial, sans-serif; margin: 5mm 0 1.5mm; }
p, li { orphans: 3; widows: 3; }
code { font: 9pt "IBM Plex Mono", Consolas, monospace; background: #f4f0e8; padding: 0 2px; }
table { border-collapse: collapse; width: 100%; font-size: 9pt; margin: 3mm 0; page-break-inside: avoid; }
th, td { border-bottom: 1px solid #e3dccf; padding: 3px 5px; text-align: left; vertical-align: top; }
th { font-weight: 600; background: #f7f3ec; }
img { max-width: 100%; page-break-inside: avoid; }
em { color: #5b5246; }
"""


def main():
    md = (DOCS / "report.md").read_text(encoding="utf-8")
    md = re.sub(r"<!--[A-Z_]+-->", "", md)  # unfilled placeholders never reach the PDF
    # Python-Markdown needs a blank line before a list that follows a paragraph.
    item = re.compile(r"^\s*([-*]|\d+\.) ")
    out = []
    for ln in md.split("\n"):
        prev = out[-1] if out else ""
        if item.match(ln) and prev.strip() and not item.match(prev) and not prev.startswith("  "):
            out.append("")
        out.append(ln)
    md = "\n".join(out)
    body = markdown.markdown(md, extensions=["tables", "fenced_code", "sane_lists"])
    html = (f"<!doctype html><html><head><meta charset='utf-8'><title>Thalweg: technical report</title>"
            f"<link href='https://fonts.googleapis.com/css2?family=Fraunces:wght@600&family=Figtree:wght@400;600"
            f"&family=IBM+Plex+Mono&display=swap' rel='stylesheet'><style>{CSS}</style></head><body>{body}</body></html>")
    out_html = DOCS / "report.html"
    out_html.write_text(html, encoding="utf-8")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page()
        page.goto(out_html.as_uri(), wait_until="networkidle")
        page.pdf(path=str(DOCS / "report.pdf"), format="A4", print_background=True,
                 margin={"top": "18mm", "bottom": "18mm", "left": "16mm", "right": "16mm"},
                 display_header_footer=True, header_template="<span></span>",
                 footer_template="<div style='font-size:8px;width:100%;text-align:center;color:#888'>"
                                 "Thalweg · technical report · <span class='pageNumber'></span>/<span class='totalPages'></span></div>")
        b.close()
    print("wrote", DOCS / "report.pdf")


if __name__ == "__main__":
    main()
