#!/usr/bin/env python3
"""Homelab Status Page — server-side polling aggregator.

Usage:
    python3 homelab_status.py [port]

Reads config.json from the same directory. Polls each configured
endpoint in a background thread, caches the latest result, and
serves two routes:
    GET /              — HTML dashboard
    GET /api/status    — aggregated JSON
"""

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config.json"
POLL_INTERVAL = 15  # seconds between polls

# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_config():
    """Load and validate config.json. Returns list of service dicts."""
    with open(CONFIG_PATH, "r") as f:
        data = json.load(f)
    services = data.get("services", [])
    for s in services:
        for key in ("name", "url", "type"):
            if key not in s:
                raise ValueError(f"Service missing required key '{key}': {s}")
        if s["type"] not in ("http_ok", "http_json"):
            raise ValueError(f"Unknown type '{s['type']}' for {s['name']}")
    return services


# ---------------------------------------------------------------------------
# Polling engine
# ---------------------------------------------------------------------------

class ServiceResult:
    __slots__ = ("name", "up", "http_status", "latency_ms",
                 "last_checked", "error", "extra")

    def __init__(self, name, up=None, http_status=None, latency_ms=None,
                 last_checked=None, error=None, extra=None):
        self.name = name
        self.up = up          # True, False, or None (unknown)
        self.http_status = http_status
        self.latency_ms = latency_ms
        self.last_checked = last_checked
        self.error = error
        self.extra = extra or {}

    def to_dict(self):
        return {
            "name": self.name,
            "status": "up" if self.up is True else "down" if self.up is False else "unknown",
            "http_status": self.http_status,
            "latency_ms": self.latency_ms,
            "last_checked": self.last_checked,
            "error": self.error,
            "extra": self.extra,
        }


def poll_service(service, timeout):
    """Poll a single service. Returns a ServiceResult."""
    name = service["name"]
    url = service["url"]
    stype = service["type"]
    start = time.monotonic()
    try:
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", "HomelabStatusPage/1.0")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            elapsed = (time.monotonic() - start) * 1000
            http_status = resp.status
            body = resp.read()

            if stype == "http_ok":
                if 200 <= http_status < 400:
                    return ServiceResult(name, up=True, http_status=http_status,
                                         latency_ms=round(elapsed, 1),
                                         last_checked=time.strftime(
                                             "%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
                else:
                    return ServiceResult(name, up=False, http_status=http_status,
                                         latency_ms=round(elapsed, 1),
                                         last_checked=time.strftime(
                                             "%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                         error=f"HTTP {http_status}")

            elif stype == "http_json":
                try:
                    data = json.loads(body)
                except json.JSONDecodeError:
                    return ServiceResult(name, up=False, http_status=http_status,
                                         latency_ms=round(elapsed, 1),
                                         last_checked=time.strftime(
                                             "%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                         error="Response is not valid JSON")

                check_field = service.get("field", "status")
                expected = service.get("expected_value")

                if expected is not None:
                    actual = data.get(check_field)
                    if actual == expected:
                        return ServiceResult(name, up=True, http_status=http_status,
                                             latency_ms=round(elapsed, 1),
                                             last_checked=time.strftime(
                                                 "%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                             extra={"field_value": actual, "field": check_field})
                    else:
                        return ServiceResult(name, up=False, http_status=http_status,
                                             latency_ms=round(elapsed, 1),
                                             last_checked=time.strftime(
                                                 "%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                             error=f"Field '{check_field}' = {actual!r}, expected {expected!r}")
                else:
                    # Just check that the field exists and is truthy
                    if check_field in data and data[check_field]:
                        return ServiceResult(name, up=True, http_status=http_status,
                                             latency_ms=round(elapsed, 1),
                                             last_checked=time.strftime(
                                                 "%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                             extra={"field_value": data[check_field], "field": check_field})
                    else:
                        return ServiceResult(name, up=False, http_status=http_status,
                                             latency_ms=round(elapsed, 1),
                                             last_checked=time.strftime(
                                                 "%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                             error=f"Field '{check_field}' not found or empty in response")

    except urllib.error.HTTPError as e:
        elapsed = (time.monotonic() - start) * 1000
        return ServiceResult(name, up=False, http_status=e.code,
                             latency_ms=round(elapsed, 1),
                             last_checked=time.strftime(
                                 "%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                             error=f"HTTP {e.code}")
    except urllib.error.URLError as e:
        elapsed = (time.monotonic() - start) * 1000
        reason = str(e.reason)
        if "timed out" in reason.lower():
            error_msg = "Connection timed out"
        elif "Name or service not known" in reason:
            error_msg = f"DNS resolution failed: {reason}"
        else:
            error_msg = reason
        return ServiceResult(name, up=False, http_status=None,
                             latency_ms=round(elapsed, 1),
                             last_checked=time.strftime(
                                 "%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                             error=error_msg)
    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        return ServiceResult(name, up=False, http_status=None,
                             latency_ms=round(elapsed, 1),
                             last_checked=time.strftime(
                                 "%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                             error=str(e))


def run_polling_loop(services, results, interval, timeout):
    """Background thread that polls all services on an interval."""
    while True:
        for svc in services:
            result = poll_service(svc, timeout)
            with results["lock"]:
                results["data"][svc["name"]] = result
        time.sleep(interval)


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Homelab Status</title>
<style>
  :root {
    --bg: #0f1117;
    --surface: #1a1d27;
    --border: #2a2d3a;
    --text: #e4e6ed;
    --text-muted: #8b8fa3;
    --up: #22c55e;
    --up-bg: rgba(34,197,94,0.12);
    --down: #ef4444;
    --down-bg: rgba(239,68,68,0.12);
    --unknown: #6b7280;
    --unknown-bg: rgba(107,114,128,0.12);
    --accent: #6366f1;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    padding: 2rem 1.5rem;
  }
  .container { max-width: 1100px; margin: 0 auto; }
  h1 {
    font-size: 1.6rem;
    font-weight: 700;
    margin-bottom: 0.3rem;
    letter-spacing: -0.02em;
  }
  .subtitle { color: var(--text-muted); font-size: 0.9rem; margin-bottom: 2rem; }
  .banner {
    padding: 0.75rem 1.2rem;
    border-radius: 8px;
    font-weight: 600;
    font-size: 0.95rem;
    margin-bottom: 1.8rem;
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }
  .banner.all-up { background: var(--up-bg); color: var(--up); border: 1px solid rgba(34,197,94,0.25); }
  .banner.degraded { background: rgba(245,158,11,0.12); color: #f59e0b; border: 1px solid rgba(245,158,11,0.25); }
  .banner.many-down { background: var(--down-bg); color: var(--down); border: 1px solid rgba(239,68,68,0.25); }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
    gap: 1rem;
  }
  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 1.2rem;
    transition: border-color 0.2s;
  }
  .card:hover { border-color: #3a3d4a; }
  .card.up    { border-left: 3px solid var(--up); }
  .card.down  { border-left: 3px solid var(--down); }
  .card.unknown { border-left: 3px solid var(--unknown); }
  .card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 0.8rem;
  }
  .card-name { font-weight: 600; font-size: 1.05rem; }
  .status-badge {
    font-size: 0.75rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    padding: 0.2rem 0.6rem;
    border-radius: 999px;
  }
  .status-badge.up    { background: var(--up-bg); color: var(--up); }
  .status-badge.down  { background: var(--down-bg); color: var(--down); }
  .status-badge.unknown { background: var(--unknown-bg); color: var(--unknown); }
  .card-meta {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0.4rem 1rem;
    font-size: 0.85rem;
    color: var(--text-muted);
  }
  .meta-label { }
  .meta-value { text-align: right; font-variant-numeric: tabular-nums; }
  .error-text {
    margin-top: 0.6rem;
    font-size: 0.8rem;
    color: var(--down);
    word-break: break-word;
  }
  .footer {
    margin-top: 2.5rem;
    text-align: center;
    font-size: 0.8rem;
    color: var(--text-muted);
  }
  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
  }
  .refreshing { animation: pulse 1.5s ease-in-out infinite; }
</style>
</head>
<body>
<div class="container">
  <h1>Homelab Status</h1>
  <p class="subtitle">Aggregated health of homelab services</p>
  <div id="banner" class="banner">Loading…</div>
  <div id="grid" class="grid"></div>
  <div class="footer">Auto-refreshes every 15s · Last full poll: <span id="last-poll">—</span></div>
</div>
<script>
  const REFRESH_MS = 15000;
  async function fetchStatus() {
    try {
      const r = await fetch('/api/status');
      const data = await r.json();
      render(data);
    } catch(e) {
      document.getElementById('banner').textContent = 'Failed to reach API';
      document.getElementById('banner').className = 'banner many-down';
    }
  }
  function render(data) {
    const services = data.services || [];
    const upCount = services.filter(s => s.status === 'up').length;
    const downCount = services.filter(s => s.status === 'down').length;
    const total = services.length;
    const banner = document.getElementById('banner');
    if (total === 0) {
      banner.textContent = 'No services configured';
      banner.className = 'banner many-down';
    } else if (downCount === 0) {
      banner.textContent = 'All ' + total + ' service' + (total>1?'s':'') + ' operational';
      banner.className = 'banner all-up';
    } else if (downCount <= total / 2) {
      banner.textContent = downCount + ' of ' + total + ' service' + (total>1?'s':'') + ' down';
      banner.className = 'banner degraded';
    } else {
      banner.textContent = downCount + ' of ' + total + ' service' + (total>1?'s':'') + ' down';
      banner.className = 'banner many-down';
    }
    const grid = document.getElementById('grid');
    grid.innerHTML = services.map(s => {
      const cls = s.status;
      const errorHtml = s.error ? '<div class="error-text">' + esc(s.error) + '</div>' : '';
      const extraHtml = s.extra && s.extra.field_value !== undefined
        ? '<div class="card-meta"><span class="meta-label">Field</span><span class="meta-value">' + esc(String(s.extra.field_value)) + '</span></div>'
        : '';
      return '<div class="card ' + cls + '">'
        + '<div class="card-header">'
        + '<span class="card-name">' + esc(s.name) + '</span>'
        + '<span class="status-badge ' + cls + '">' + s.status + '</span>'
        + '</div>'
        + '<div class="card-meta">'
        + '<span class="meta-label">HTTP</span><span class="meta-value">' + (s.http_status ?? '—') + '</span>'
        + '<span class="meta-label">Latency</span><span class="meta-value">' + (s.latency_ms != null ? s.latency_ms + ' ms' : '—') + '</span>'
        + '<span class="meta-label">Checked</span><span class="meta-value">' + esc(s.last_checked || '—') + '</span>'
        + '</div>'
        + extraHtml
        + errorHtml
        + '</div>';
    }).join('');
    document.getElementById('last-poll').textContent = data.last_checked || '—';
  }
  function esc(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }
  fetchStatus();
  setInterval(fetchStatus, REFRESH_MS);
</script>
</body>
</html>"""


class StatusHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the status dashboard and API."""

    def log_message(self, format, *args):
        """Suppress default stderr logging for cleaner output."""
        pass

    def do_GET(self):
        if self.path == "/api/status":
            self._serve_api()
        elif self.path == "/" or self.path == "":
            self._serve_dashboard()
        else:
            self.send_error(404, "Not Found")

    def _serve_api(self):
        with results["lock"]:
            snapshot = {
                "last_checked": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "services": [results["data"][n].to_dict() for n in results["data"]],
            }
        body = json.dumps(snapshot, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_dashboard(self):
        body = DASHBOARD_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

results = {"data": {}, "lock": threading.Lock()}


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    timeout = 10  # per-endpoint timeout in seconds

    print(f"Loading config from {CONFIG_PATH}")
    services = load_config()
    print(f"Configured {len(services)} service(s):")
    for s in services:
        print(f"  - {s['name']} ({s['type']}) → {s['url']}")

    # Initial poll so the dashboard isn't empty on first load
    print("Running initial poll…")
    for svc in services:
        result = poll_service(svc, timeout)
        with results["lock"]:
            results["data"][svc["name"]] = result

    # Start background polling thread
    t = threading.Thread(target=run_polling_loop, args=(services, results, POLL_INTERVAL, timeout),
                         daemon=True)
    t.start()

    server = HTTPServer(("0.0.0.0", port), StatusHandler)
    print(f"Starting server on http://0.0.0.0:{port}")
    print(f"Dashboard: http://localhost:{port}/")
    print(f"API:       http://localhost:{port}/api/status")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
