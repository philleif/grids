"""Visual capture -- renders artifacts to screenshots using Playwright.

Supports SVG, HTML, and LaTeX (via pdf2svg fallback) artifacts.
Uses headless Chromium to produce pixel-accurate PNG screenshots
that can be fed to a vision LLM for critique.
"""

import base64
import os
import tempfile
from pathlib import Path

from rich.console import Console

console = Console(stderr=True)


def _get_browser():
    """Lazy-import and launch Playwright browser."""
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    return pw, browser


def capture_svg(svg_content: str, output_path: str, width: int = 1200, height: int = 900) -> str:
    """Render SVG string to PNG screenshot.

    Returns the path to the saved PNG file.
    """
    pw, browser = _get_browser()
    try:
        page = browser.new_page(viewport={"width": width, "height": height})

        html = f"""<!DOCTYPE html>
<html>
<head>
<style>
  body {{ margin: 0; padding: 20px; background: #fff; display: flex; justify-content: center; align-items: flex-start; }}
  svg {{ max-width: 100%; height: auto; }}
</style>
</head>
<body>
{svg_content}
</body>
</html>"""

        page.set_content(html, wait_until="networkidle")
        page.wait_for_timeout(500)

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        page.screenshot(path=output_path, full_page=True)

        page.close()
        return output_path
    finally:
        browser.close()
        pw.stop()


def capture_html(html_content: str, output_path: str, width: int = 1200, height: int = 900) -> str:
    """Render HTML string to PNG screenshot."""
    pw, browser = _get_browser()
    try:
        page = browser.new_page(viewport={"width": width, "height": height})
        page.set_content(html_content, wait_until="networkidle")
        page.wait_for_timeout(500)

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        page.screenshot(path=output_path, full_page=True)

        page.close()
        return output_path
    finally:
        browser.close()
        pw.stop()


def capture_file(file_path: str, output_path: str, width: int = 1200, height: int = 900) -> str:
    """Render a file (SVG, HTML) to PNG screenshot."""
    path = Path(file_path)
    content = path.read_text(encoding="utf-8")

    if path.suffix == ".svg":
        return capture_svg(content, output_path, width, height)
    elif path.suffix in (".html", ".htm"):
        return capture_html(content, output_path, width, height)
    else:
        raise ValueError(f"Unsupported file type: {path.suffix}. Use .svg or .html")


def capture_latex(tex_path: str, output_path: str, width: int = 1200, height: int = 900) -> str | None:
    """Compile LaTeX to PDF, then capture the PDF as PNG via Playwright.

    Returns the PNG path, or None on failure.
    """
    from grids.typeset.engine import compile_tex

    pdf_path = compile_tex(tex_path, os.path.dirname(tex_path), verbose=False)
    if not pdf_path:
        return None

    # Render PDF in browser via an HTML wrapper
    pdf_abs = os.path.abspath(pdf_path)
    html = f"""<!DOCTYPE html>
<html>
<head>
<style>
  body {{ margin: 0; padding: 20px; background: #fff; display: flex; justify-content: center; }}
  embed {{ width: 100%; height: 90vh; }}
</style>
</head>
<body>
<embed src="file://{pdf_abs}" type="application/pdf" />
</body>
</html>"""

    return capture_html(html, output_path, width, height)


def capture_artifact(artifact: dict, output_dir: str, width: int = 1200, height: int = 900) -> str | None:
    """Capture an artifact dict (from coder.py) to PNG.

    Returns the PNG path, or None if the format isn't visual.
    """
    fmt = artifact.get("format", "raw")
    code = artifact.get("code", "")
    order_id = artifact.get("work_order_id", "artifact")

    if not code:
        return None

    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"{order_id}.png")

    if fmt == "svg":
        return capture_svg(code, output_path, width, height)
    elif fmt == "html":
        return capture_html(code, output_path, width, height)
    elif fmt == "latex":
        # Write tex to temp file, compile, capture
        tex_path = os.path.join(output_dir, f"{order_id}.tex")
        with open(tex_path, "w", encoding="utf-8") as f:
            f.write(code)
        return capture_latex(tex_path, output_path, width, height)
    else:
        return None


def screenshot_to_base64(png_path: str) -> str:
    """Read a PNG file and return base64-encoded string for LLM vision input."""
    with open(png_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")
