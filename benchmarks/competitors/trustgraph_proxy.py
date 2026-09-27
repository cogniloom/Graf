#!/usr/bin/env python3
"""Restricted Unix-socket forwarding for the TrustGraph benchmark containers."""
import argparse
import http.client
import json
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn, UnixStreamServer
from urllib.parse import urlsplit


class Handler(BaseHTTPRequestHandler):
    def answer(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def allowed_client(self):
        # Access is restricted to the task-private socket mounted in only the
        # two benchmark model containers; no TCP listener exists.
        return isinstance(self.server, UnixStreamServer)

    def address_string(self):
        return 'trustgraph-unix-socket'

    def do_GET(self):
        if not self.allowed_client() or self.path != "/health":
            return self.answer(404, b'{}')
        self.answer(200, b'{"ok":true,"scope":"trustgraph-unix-proxy"}')

    def do_POST(self):
        allowed = {self.server.base_path + suffix for suffix in ('/chat/completions', '/embeddings')}
        if not self.allowed_client() or self.path not in allowed:
            return self.answer(404, b'{}')
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 8 * 1024 * 1024 or self.headers.get("Transfer-Encoding"):
                return self.answer(413, b'{}')
            body = self.rfile.read(length)
            payload = json.loads(body)
            if payload.get("stream"):
                return self.answer(400, b'{"error":"streaming unsupported"}')
            connection = http.client.HTTPConnection("127.0.0.1", self.server.upstream_port, timeout=1800)
            try:
                connection.request("POST", self.path, body=body, headers={
                    "Content-Type": "application/json", "Authorization": "Bearer benchmark-local"})
                response = connection.getresponse()
                result = response.read(16 * 1024 * 1024 + 1)
                if len(result) > 16 * 1024 * 1024:
                    raise ValueError("gateway response too large")
                self.answer(response.status, result)
            finally:
                connection.close()
        except Exception as exc:
            self.answer(502, json.dumps({"error": str(exc), "retry": False}).encode())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', required=True)
    parser.add_argument('--endpoint', default='http://127.0.0.1:18791/trustgraph/v1')
    args = parser.parse_args()
    endpoint = urlsplit(args.endpoint)
    if (endpoint.scheme != 'http' or endpoint.hostname != '127.0.0.1' or not endpoint.port
            or endpoint.path.rstrip('/') not in ('/v1', '/trustgraph/v1')
            or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment):
        parser.error('Only loopback HTTP /v1 upstream endpoints are accepted')
    socket = Path(args.socket)
    socket.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    class Server(ThreadingMixIn, UnixStreamServer):
        daemon_threads = True
    with Server(str(socket), Handler) as server:
        server.upstream_port = endpoint.port
        server.base_path = endpoint.path.rstrip('/')
        socket.chmod(0o600)
        try:
            server.serve_forever()
        finally:
            socket.unlink(missing_ok=True)
