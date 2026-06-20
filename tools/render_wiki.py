"""Render a wiki markdown page (with mermaid diagrams) to a PNG via Playwright.

Local-viewing helper for comparison/screenshots — NOT part of the shipped tool.
Usage: python tools/render_wiki.py <page.md> <out.png>
"""

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

HTML = """<!doctype html><html><head><meta charset="utf-8">
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; max-width: 900px;
         margin: 0 auto; padding: 32px 40px; color: #1f2328; line-height: 1.55; }}
  h1 {{ border-bottom: 2px solid #d0d7de; padding-bottom: .3em; }}
  h2 {{ border-bottom: 1px solid #d0d7de; padding-bottom: .2em; margin-top: 1.6em; }}
  code {{ background: #f6f8fa; padding: .15em .35em; border-radius: 4px; font-size: 90%; }}
  pre code {{ display: block; padding: 12px; overflow-x: auto; }}
  a {{ color: #0969da; text-decoration: none; }}
  blockquote {{ border-left: 4px solid #d4a72c; background: #fff8e1; margin: 0;
                padding: 8px 16px; color: #5a4a00; }}
  table {{ border-collapse: collapse; }} td, th {{ border: 1px solid #d0d7de; padding: 6px 12px; }}
  .mermaid {{ background: #fbfcfd; border: 1px solid #d0d7de; border-radius: 8px;
              padding: 16px; margin: 16px 0; }}
</style></head><body><div id="content"></div>
<script>
  const md = {md};
  // turn ```mermaid blocks into <div class=mermaid> before marked
  const html = marked.parse(md, {{ gfm: true }});
  document.getElementById('content').innerHTML = html;
  // marked renders ```mermaid as <pre><code class="language-mermaid">; convert them
  document.querySelectorAll('code.language-mermaid').forEach(el => {{
    const d = document.createElement('div'); d.className = 'mermaid';
    d.textContent = el.textContent; el.closest('pre').replaceWith(d);
  }});
  mermaid.initialize({{ startOnLoad: true, theme: 'neutral' }});
</script></body></html>"""


def render(md_path: str, out_path: str) -> None:
    md = Path(md_path).read_text(encoding="utf-8")
    html = HTML.format(md=json.dumps(md))
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1000, "height": 1400},
                                device_scale_factor=2)
        page.set_content(html, wait_until="networkidle")
        page.wait_for_timeout(2500)  # let mermaid finish drawing
        page.screenshot(path=out_path, full_page=True)
        browser.close()
    print(f"rendered {md_path} → {out_path}")


if __name__ == "__main__":
    render(sys.argv[1], sys.argv[2])
