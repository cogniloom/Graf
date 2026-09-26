"""Synthetic API contract/browser smoke test. Requires Python Playwright and Chromium.
After npm run build, run python tests/browser-smoke.py. An isolated static server is started automatically.
Optionally set GRAF_UI_URL to verify another server static build and CSP; API calls are still intercepted.
All /api responses are synthetic; this does not validate the real backend or corpus.
"""

import json
import os
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from playwright.sync_api import expect, sync_playwright

BASE = os.environ.get("GRAF_UI_URL")
server = None
if not BASE:

    class StaticHandler(SimpleHTTPRequestHandler):
        def end_headers(self):
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; worker-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
            )
            super().end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        partial(StaticHandler, directory=str(Path(__file__).resolve().parent.parent / "dist")),
    )
    Thread(target=server.serve_forever, daemon=True).start()
    BASE = f"http://127.0.0.1:{server.server_port}"
OUT = Path(__file__).resolve().parent.parent / "qa"
NAMES = [
    "Purchase request",
    "Vendor quote",
    "Approval email",
    "Product spec",
    "Revised order",
    "Delivery schedule",
    "Contract terms",
    "Risk assessment",
]
NODES = [dict(id=f"d{i}", label=n, kind="document", document_id=f"d{i}") for i, n in enumerate(NAMES)]
EDGES = [dict(id=f"e{i}", source=f"d{i}", target=f"d{(i + 1) % 8}", type="references") for i in range(8)]
for i in range(8):
    for j in range(7):
        node = f"p{i}-{j}"
        NODES.append(dict(id=node, label=f"Passage {j + 1}", kind="passage", document_id=f"d{i}"))
        EDGES.append(dict(id=node, source=f"d{i}", target=node, type="contains"))
with sync_playwright() as p:
    browser = p.chromium.launch(
        executable_path=os.environ.get("CHROMIUM", "/usr/bin/chromium"),
        headless=True,
        args=["--enable-unsafe-swiftshader"],
    )
    context = browser.new_context(viewport={"width": 1536, "height": 1024})
    page = context.new_page()
    errors = []
    external = []
    calls = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    context.on(
        "request",
        lambda r: external.append(r.url) if not r.url.startswith((BASE, "blob:", "data:")) else None,
    )
    state = {"state": "ready_with_gaps", "revision": 1, "unauthorized": False, "offline": False}
    sources = [
        dict(
            id="s1",
            path="/synthetic/scan.pdf",
            kind="file",
            enabled=True,
            status="error",
            file_count=1,
            error="Synthetic text extraction error",
        )
    ]

    def handle(route):
        req = route.request
        if state["offline"]:
            route.abort()
            return
        if state["unauthorized"]:
            route.fulfill(
                status=401,
                content_type="application/json",
                body=json.dumps({"detail": "Synthetic session expired"}),
            )
            return
        path = req.url.split("/api")[-1].split("?")[0]
        calls.append((req.method, path))
        body = {}
        if path == "/status":
            body = dict(
                workspace_name="Synthetic QA collection",
                state=state["state"],
                phase="published",
                revision=state["revision"],
                published_revision=1,
                snapshot_id="synthetic-qa",
                counts=dict(documents=8, passages=56, connections=64, gaps=1),
                message="Explicitly synthetic browser verification data.",
                models_ready=True,
                device="cpu",
            )
        elif path == "/graph":
            body = dict(
                nodes=NODES,
                edges=EDGES,
                total_nodes=len(NODES),
                total_edges=len(EDGES),
                truncated=False,
                snapshot_id="synthetic-qa",
            )
        elif path == "/documents":
            body = dict(
                items=[
                    dict(id=f"d{i}", path=f"/synthetic/{n}.txt", status="ready", warnings=[], passage_count=7)
                    for i, n in enumerate(NAMES)
                    if "query=absent" not in req.url
                ],
                total=0 if "query=absent" in req.url else 8,
            )
        elif path.startswith("/documents/"):
            i = int(path.split("/")[-1][1:])
            body = dict(
                id=f"d{i}",
                path=f"/synthetic/{NAMES[i]}.txt",
                status="ready",
                warnings=["Synthetic fixture; not real evidence."],
                passage_count=7,
                passages=[
                    dict(
                        id=f"p{i}-0",
                        text="Synthetic browser verification passage. No real document data.",
                        locators={"page": 1},
                    )
                ],
                relationships=[
                    dict(
                        type="references", target=f"d{(i + 1) % 8}", evidence="Synthetic relationship fixture"
                    )
                ],
                next_offset=None,
            )
        elif path == "/sources":
            if req.method == "POST":
                sources.append(
                    dict(
                        id="s2",
                        path=req.post_data_json["path"],
                        kind="directory",
                        enabled=True,
                        status="queued",
                        file_count=0,
                        error=None,
                    )
                )
                body = sources[-1]
            else:
                body = dict(items=sources, allowed_roots=["/synthetic"])
        elif path.startswith("/sources/"):
            source = next((s for s in sources if s["id"] == path.split("/")[2]), None)
            if req.method == "DELETE":
                sources.remove(source)
            if req.method == "PATCH":
                source["enabled"] = req.post_data_json["enabled"]
                body = source
        elif path == "/jobs":
            body = dict(
                items=[
                    dict(
                        id="qa-job",
                        state="failed",
                        phase="extracting",
                        started_at=None,
                        finished_at=None,
                        error="Synthetic text extraction error",
                        progress={"files": 1},
                    )
                ]
            )
        elif path == "/settings":
            body = dict(device="cpu", codex_instructions="Synthetic QA instructions. Do not execute.")
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    page.route("**/api/**", handle)
    page.goto(BASE + "/#token=synthetic-qa-token")
    expect(page.get_by_role("button", name="Fit graph")).to_be_enabled(timeout=30000)
    expect(page.get_by_text("Approval email", exact=True)).to_be_visible(timeout=30000)
    page.get_by_role("button", name="Pause motion").click()
    page.get_by_role("button", name="Fit graph").click()
    page.get_by_text("Approval email", exact=True).click()
    expect(page.get_by_text("Synthetic browser verification passage. No real document data.")).to_be_visible()
    expect(page.get_by_text("/synthetic/Approval email.txt", exact=True)).to_be_visible()
    page.screenshot(path=str(OUT / "explore-desktop.png"))
    page.get_by_role("button", name="Labels", exact=True).click()
    expect(page.get_by_role("button", name="Labels", exact=True)).to_have_attribute("aria-pressed", "false")
    page.get_by_role("button", name="Documents (8)", exact=True).click()
    page.get_by_role("button", name="Close source details").click()
    page.get_by_role("textbox", name="Search document names").fill("absent")
    expect(page.get_by_text("No documents match your search.")).to_be_visible()
    page.get_by_role("textbox", name="Search document names").fill("")
    page.get_by_role("button", name="Sources", exact=True).click()
    expect(page.get_by_text("Synthetic text extraction error")).to_be_visible()
    page.get_by_role("checkbox", name="Enabled").click()
    expect(page.get_by_role("checkbox", name="Enabled")).not_to_be_checked()
    page.get_by_label("Rescan /synthetic/scan.pdf", exact=True).click()
    page.get_by_label("File or directory path", exact=True).fill("/synthetic/new")
    page.get_by_role("button", name="Add source", exact=True).click()
    expect(page.get_by_text("/synthetic/new", exact=True)).to_be_visible()
    page.get_by_label("Remove /synthetic/new", exact=True).click()
    expect(page.get_by_role("dialog")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.get_by_role("dialog")).not_to_be_visible()
    assert len(sources) == 2
    page.get_by_label("Remove /synthetic/new", exact=True).click()
    page.get_by_role("button", name="Remove source", exact=True).click()
    expect(page.get_by_role("dialog")).not_to_be_visible()
    expect(page.get_by_role("row", name="/synthetic/new", exact=False)).not_to_be_visible()
    page.screenshot(path=str(OUT / "sources-desktop.png"))
    page.get_by_role("button", name="Open in Codex").click()
    expect(page.get_by_text("Synthetic QA instructions. Do not execute.")).to_be_visible()
    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_role("button", name="Explore", exact=True).click()
    page.get_by_role("button", name="Documents (8)", exact=True).click()
    page.get_by_role("button", name="Purchase request.txt", exact=False).click()
    expect(page.get_by_text("Synthetic browser verification passage. No real document data.")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(OUT / "explore-mobile.png"), full_page=True)
    state["state"] = "updating"
    state["revision"] = 2
    expect(page.get_by_text("Preparing your evidence")).to_be_visible(timeout=6000)
    expect(
        page.get_by_text("Synthetic browser verification passage. No real document data.")
    ).not_to_be_visible()
    state["state"] = "blocked"
    expect(page.get_by_text("Workspace needs attention")).to_be_visible(timeout=6000)
    state["state"] = "empty"
    expect(page.get_by_text("Your evidence starts here")).to_be_visible(timeout=6000)
    state["unauthorized"] = True
    expect(page.get_by_text("Open your workspace securely")).to_be_visible(timeout=6000)
    state["unauthorized"] = False
    page.evaluate("location.hash='token=synthetic-mounted-browser-token'")
    expect(page.get_by_text("Your evidence starts here")).to_be_visible(timeout=6000)
    assert calls.count(("POST", "/session")) == 2
    state["offline"] = True
    expect(page.get_by_role("heading", name="Local server unavailable")).to_be_visible(timeout=6000)
    assert ("PATCH", "/sources/s1") in calls
    assert ("POST", "/sources/s1/rescan") in calls
    assert not external, external
    assert not errors, errors
    assert ("POST", "/session") in calls
    assert page.evaluate("location.hash") == ""
    print(
        "PASS: real Cosmograph rendered 64 synthetic nodes/64 links; graph label opens correct source; controls/list/search/source add/removal/Escape/mobile/no overflow/readiness invalidation/token removal; no external requests or page errors."
    )
    browser.close()

if server:
    server.shutdown()
