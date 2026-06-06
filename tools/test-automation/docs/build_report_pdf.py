#!/usr/bin/env python3
"""Build QGC_UxAS_MixedFleet_Report.pdf from the matching markdown file."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import markdown
from weasyprint import HTML, CSS

DOC_DIR = Path(__file__).resolve().parent
MD_FILE = DOC_DIR / "QGC_UxAS_MixedFleet_Report.md"
PDF_FILE = DOC_DIR / "QGC_UxAS_MixedFleet_Report.pdf"

CSS_TEMPLATE = """
@page {
    size: A4;
    margin: 22mm 18mm 22mm 18mm;
    @top-right { content: "QGC × OpenUxAS Mixed-Fleet Report"; font-family: "Noto Sans CJK KR", sans-serif; font-size: 9pt; color: #888; }
    @bottom-right { content: counter(page) " / " counter(pages); font-family: "Noto Sans CJK KR", sans-serif; font-size: 9pt; color: #888; }
}

html, body {
    font-family: "Noto Sans CJK KR", "Noto Sans", sans-serif;
    font-size: 10pt;
    line-height: 1.45;
    color: #1f2328;
}

h1 {
    font-size: 22pt;
    margin-top: 0;
    margin-bottom: 0.2em;
    border-bottom: 2px solid #d0d7de;
    padding-bottom: 0.25em;
    page-break-before: always;
}
h1:first-of-type { page-break-before: avoid; }

h2 {
    font-size: 15pt;
    margin-top: 1.4em;
    margin-bottom: 0.4em;
    border-bottom: 1px solid #d0d7de;
    padding-bottom: 0.2em;
    page-break-after: avoid;
}

h3 {
    font-size: 12pt;
    margin-top: 1.1em;
    margin-bottom: 0.3em;
    color: #1f2328;
    page-break-after: avoid;
}

p, li { font-size: 10pt; }
a { color: #0969da; text-decoration: none; }

code {
    font-family: "Noto Sans Mono", "DejaVu Sans Mono", monospace;
    background: #f6f8fa;
    padding: 1px 4px;
    border-radius: 3px;
    font-size: 9pt;
}

pre {
    background: #0d1117;
    color: #e6edf3;
    padding: 8px 10px;
    border-radius: 5px;
    line-height: 1.32;
    font-size: 8.5pt;
    overflow-wrap: anywhere;
    page-break-inside: avoid;
}
pre code { background: transparent; color: inherit; padding: 0; font-size: 8.5pt; }

table {
    border-collapse: collapse;
    width: 100%;
    margin: 0.6em 0 1em 0;
    font-size: 9.5pt;
    page-break-inside: avoid;
}
th, td { border: 1px solid #d0d7de; padding: 4px 8px; text-align: left; vertical-align: top; }
th { background: #f6f8fa; font-weight: 600; }

blockquote {
    border-left: 3px solid #d0d7de;
    padding-left: 10px;
    color: #57606a;
    margin: 0.6em 0;
}

hr { border: none; border-top: 1px solid #d0d7de; margin: 1.4em 0; }
ul, ol { margin-left: 1.2em; padding-left: 0.6em; }
li { margin: 0.15em 0; }

.cover {
    text-align: center;
    padding-top: 60mm;
    page-break-after: always;
}
.cover h1 {
    font-size: 26pt;
    border: none;
    page-break-before: avoid;
}
.cover .subtitle { font-size: 13pt; color: #57606a; margin-top: 0.5em; }
.cover .meta { margin-top: 14mm; font-size: 10pt; color: #57606a; }
"""


def main() -> int:
    if not MD_FILE.exists():
        print(f"ERROR: {MD_FILE} not found", file=sys.stderr)
        return 1

    md_text = MD_FILE.read_text(encoding="utf-8")
    md = markdown.Markdown(extensions=[
        "fenced_code", "tables", "codehilite", "toc", "sane_lists",
    ], extension_configs={
        "codehilite": {"noclasses": True, "pygments_style": "monokai"},
    })
    body_html = md.convert(md_text)

    cover_html = f"""
    <div class="cover">
      <h1>QGC × OpenUxAS<br>혼합 편대 통합 작업 보고서</h1>
      <div class="subtitle">멀티콥터 × 고정익 × OpenUxAS LMCP/ZeroMQ</div>
      <div class="meta">
        Build: {time.strftime('%Y-%m-%d %H:%M:%S')}<br>
        tools/test-automation/docs/
      </div>
    </div>
    """
    html_doc = f"""<!DOCTYPE html>
<html lang="ko">
<head><meta charset="UTF-8"><title>QGC × OpenUxAS Report</title></head>
<body>
{cover_html}
{body_html}
</body>
</html>"""

    print(f"rendering -> {PDF_FILE}")
    HTML(string=html_doc, base_url=str(DOC_DIR)).write_pdf(
        str(PDF_FILE), stylesheets=[CSS(string=CSS_TEMPLATE)])
    size_kb = os.path.getsize(PDF_FILE) / 1024.0
    print(f"OK: {PDF_FILE}  ({size_kb:.1f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
