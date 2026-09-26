"""Thin authenticated local API bridge; no model inference or document ingestion here."""

import json
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

cfg = json.loads(Path(sys.argv[1]).read_text())
if cfg["host"] != "127.0.0.1" or not 1024 <= cfg["port"] <= 65535:
    raise ValueError("Graf must use a local loopback address")
server = MCPServer("Graf")
READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)


def request(path, method="GET", payload=None):
    token = Path(cfg["token_file"]).read_text().strip()
    req = Request(
        f"http://127.0.0.1:{cfg['port']}" + path,
        method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    try:
        with urlopen(req, timeout=300) as response:
            return json.load(response)
    except HTTPError as exc:
        body = exc.read(8192).decode(errors="replace")
        raise ValueError(f"Graf returned HTTP {exc.code}: {body}") from None


@server.tool(annotations=READ)
def workspace_status() -> dict[str, Any]:
    """Read readiness, processing gaps and the current/published source revisions first."""
    return request("/api/status")


@server.tool(annotations=READ)
def list_sources() -> dict[str, Any]:
    """List registered local sources and the directories the owner has allowed."""
    return request("/api/sources")


@server.tool(annotations=WRITE)
def add_source(path: str) -> dict[str, Any]:
    """Register an allowed local file or directory when the user asks to add it."""
    return request("/api/sources", "POST", {"path": path})


@server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False))
def remove_source(source_id: str) -> dict[str, Any]:
    """Remove a source from future evidence results. Original files are preserved. Requires user intent."""
    return request("/api/sources/" + quote(source_id, safe=""), "DELETE")


@server.tool(annotations=WRITE)
def rescan_source(source_id: str) -> dict[str, Any]:
    """Queue a fresh scan of an existing source."""
    return request("/api/sources/" + quote(source_id, safe="") + "/rescan", "POST", {})


@server.tool(annotations=READ)
def discover(question: str, limit: int = 12) -> dict[str, Any]:
    """Discover ranked connected evidence. Ranking is priority, not proof of completeness."""
    return request("/api/search", "POST", {"question": question, "limit": limit})


@server.tool(annotations=READ)
def evidence_workset(workset_id: str, cursor: str | None = None, limit: int = 100) -> dict[str, Any]:
    """Continue a retained candidate workset; unavailable after its source revision changes."""
    params = {"limit": limit}
    if cursor is not None:
        params["cursor"] = cursor
    return request("/api/worksets/" + quote(workset_id, safe="") + "?" + urlencode(params))


@server.tool(annotations=READ)
def list_documents(query: str = "", offset: int = 0, limit: int = 50) -> dict[str, Any]:
    """Enumerate documents in the published revision, including extraction warnings."""
    return request("/api/documents?" + urlencode({"query": query, "offset": offset, "limit": limit}))


@server.tool(annotations=READ)
def read_document(document_id: str, offset: int = 0, limit: int = 50) -> dict[str, Any]:
    """Read source passages, locators and relationships; follow next_offset until finished."""
    return request(
        "/api/documents/" + quote(document_id, safe="") + "?" + urlencode({"offset": offset, "limit": limit})
    )


@server.tool(annotations=READ)
def graph_neighborhood(limit: int = 2000) -> dict[str, Any]:
    """Inspect the bounded graph. Check truncated and totals; layout is not evidence strength."""
    return request("/api/graph?" + urlencode({"limit": limit}))


if __name__ == "__main__":
    server.run()
