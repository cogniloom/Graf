"""Read-only real-API browser acceptance; requires a ready fictional demo workspace.
Usage: python tests/live-browser.py /absolute/private/app.json /absolute/output-directory
No API mocking or generative inference. Never use private evidence for public screenshots.
"""

import json
import os
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

cfg = json.loads(Path(sys.argv[1]).read_text())
out = Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
base = f"http://127.0.0.1:{cfg['port']}"
token = Path(cfg["token_file"]).read_text().strip()
with sync_playwright() as p:
    browser = p.chromium.launch(
        executable_path=os.environ.get("CHROMIUM", "/usr/bin/chromium"),
        headless=True,
        args=["--enable-unsafe-swiftshader"],
    )
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    errors, external, failures = [], [], []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("crash", lambda _: errors.append("Renderer crashed"))
    context.on(
        "request",
        lambda r: (
            external.append(r.url.split("#")[0]) if not r.url.startswith((base, "blob:", "data:")) else None
        ),
    )
    page.on(
        "response",
        lambda r: (
            failures.append({"url": r.url.split("#")[0], "status": r.status})
            if r.status >= 400 and "/api/status" not in r.url
            else None
        ),
    )
    page.goto(base + "/#token=" + token)
    expect(page.get_by_role("button", name="Fit graph")).to_be_enabled(timeout=45000)
    assert "#token=" not in page.url
    expect(page.get_by_role("textbox", name="Search document names")).to_be_visible()
    page.get_by_role("button", name="Pause motion").click()
    page.get_by_role("button", name="Fit graph").click()
    page.screenshot(path=str(out / "explore.png"), full_page=True)
    for width, height in [(390, 844), (1440, 1000), (768, 1024), (1536, 1024), (390, 844)]:
        page.set_viewport_size({"width": width, "height": height})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.get_by_role("button", name="Fit graph").click()
    page.screenshot(path=str(out / "mobile.png"), full_page=True)
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.get_by_role("button", name="Documents (8)", exact=True).click()
    page.get_by_role("button", name="approval.eml", exact=False).first.click()
    expect(page.get_by_role("heading", name="approval.eml", exact=True)).to_be_visible()
    expect(page.get_by_text("Approved, subject to receipt", exact=False)).to_be_visible()
    page.screenshot(path=str(out / "inspector.png"), full_page=True)
    page.get_by_role("button", name="Close source details").click()
    for _ in range(3):
        page.get_by_role("button", name="Graph", exact=True).click()
        expect(page.get_by_role("button", name="Fit graph")).to_be_enabled(timeout=45000)
        page.get_by_role("button", name="Documents (8)", exact=True).click()
    page.get_by_role("button", name="Sources", exact=True).click()
    expect(page.get_by_role("heading", name="Your sources", exact=True)).to_be_visible()
    page.screenshot(path=str(out / "sources.png"), full_page=True)
    page.get_by_role("button", name="Open in Codex", exact=True).click()
    expect(page.get_by_text("plugin-install", exact=False)).to_be_visible()
    assert not errors, errors
    assert not external, external
    assert not failures, failures
    print(
        json.dumps(
            {
                "real_api": True,
                "viewport_changes": 5,
                "graph_remounts": 3,
                "errors": errors,
                "external_requests": external,
                "failed_responses": failures,
                "token_removed": True,
            }
        )
    )
    browser.close()
