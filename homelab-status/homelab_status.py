#!/usr/bin/env python3
"""
Homelab Status Page Server
--------------------------
A lightweight, standard-library-only status page aggregator.
Polls configured services and serves the aggregated status via HTTP.

Usage:
    python3 homelab_status.py [--port 8080] [--config config.json]
"""

import json
import time
import urllib.request
import urllib.error
import http.server
import socketserver
import sys
import os
import threading
import argparse
from datetime import datetime, timezone

# --- Configuration Defaults ---
DEFAULT_PORT = 8080
DEFAULT_CONFIG = "config.json"
POLL_INTERVAL_SECONDS = 30
REQUEST_TIMEOUT_SECONDS = 10

# --- Global State ---
# Thread-safe storage for the latest status of each service
status_cache = {}
cache_lock = threading.Lock()

# --- Service Polling Logic ---

def check_service(service):
    """
    Polls a single service and returns a result dictionary.
    Handles timeouts and exceptions per-service so one failure doesn't crash others.
    """
    name = service.get("name", "Unknown")
    url = service.get("url", "")
    check_type = service.get("type", "http_ok")
    expected_field = service.get("expected_field")
    expected_value = service.get("expected_value")
    timeout = service.get("timeout", REQUEST_TIMEOUT_SECONDS)

    result = {
        "name": name,
        "url": url,
        "status": "unknown",  # up, down, unknown
        "http_code": None,
        "latency_ms": 0,
        "last_checked": None,
        "error": None
    }

    start_time = time.time()
    
    try:
        req = urllib.request.Request(url, method="GET")
        # Add a user agent so servers can distinguish status checks from real traffic
        req.add_header('User-Agent', 'Homelab-Status-Checker/1.0')
        
        with urllib.request.urlopen(req, timeout=timeout) as response:
            end_time = time.time()
            result["latency_ms"] = int((end_time - start_time) * 1000)
            result["http_code"] = response.status
            result["last_checked"] = datetime.now(timezone.utc).isoformat()

            # Logic for http_ok: Any 2xx/3xx is "up"
            if check_type == "http_ok":
                if 200 <= response.status < 400:
                    result["status"] = "up"
                else:
                    result["status"] = "down"
            
            # Logic for http_json: Parse body and check fields
            elif check_type == "http_json":
                try:
                    body = response.read().decode('utf-8')
                    data = json.loads(body)
                    
                    # Check expected field/value if configured
                    if expected_field:
                        actual_val = data.get(expected_field)
                        if actual_val == expected_value:
                            result["status"] = "up"
                        else:
                            result["status"] = "down"
                            result["error"] = f"Expected {expected_field}={expected_value}, got {actual_val}"
                    else:
                        # If no specific field check, just presence of valid JSON + 2xx is up
                        result["status"] = "up"
                except json.JSONDecodeError:
                    result["status"] = "down"
                    result["error"] = "Invalid JSON response"

    except urllib.error.HTTPError as e:
        end_time = time.time()
        result["latency_ms"] = int((end_time - start_time) * 1000)
        result["http_code"] = e.code
        result["last_checked"] = datetime.now(timezone.utc).isoformat()
        result["status"] = "down"
        result["error"] = f"HTTP {e.code}"

    except urllib.error.URLError as e:
        end_time = time.time()
        result["latency_ms"] = int((end_time - start_time) * 1000)
        result["last_checked"] = datetime.now(timezone.utc).isoformat()
        result["status"] = "down"
        result["error"] = str(e.reason)

    except Exception as e:
        end_time = time.time()
        result["latency_ms"] = int((end_time - start_time) * 1000)
        result["last_checked"] = datetime.now(timezone.utc).isoformat()
        result["status"] = "down"
        result["error"] = str(e)

    return result


def poll_loop(config_path):
    """
    Background thread that loads config and polls services at an interval.
    """
    while True:
        try:
            with open(config_path, 'r') as f:
                services = json.load(f)
            
            # Clear cache to ensure we only show known services
            with cache_lock:
                status_cache.clear()
            
            # Poll each service
            for service in services:
                result = check_service(service)
                with cache_lock:
                    status_cache[service["name"]] = result
                    
        except FileNotFoundError:
            print(f"[WARN] Config file {config_path} not found. Retrying in {POLL_INTERVAL_SECONDS}s...", file=sys.stderr)
        except Exception as e:
            print(f"[ERROR] Polling loop error: {e}", file=sys.stderr)
        
        time.sleep(POLL_INTERVAL_SECONDS)


# --- HTML Generation ---

def generate_card_html(name, data):
    """
    Generates HTML for a single service card.
    """
    status_class = data["status"]  # up, down, unknown
    
    error_display = ""
    if data.get("error"):
        error_display = f'<div class="error">Error: {data["error"]}</div>'

    card_html = f"""
    <div class="card {status_class}">
        <div class="card-header">
            <span class="status-icon {status_class}">●</span>
            <h3>{name}</h3>
        </div>
        <div class="card-body">
            <div class="metric">
                <span class="label">Status</span>
                <span class="value status-text-{status_class}">{status_class.upper()}</span>
            </div>
            <div class="metric">
                <span class="label">Latency</span>
                <span class="value">{data["latency_ms"]} ms</span>
            </div>
            <div class="metric">
                <span class="label">HTTP Code</span>
                <span class="value">{data["http_code"] or 'N/A'}</span>
            </div>
            <div class="metric">
                <span class="label">Last Checked</span>
                <span class="value">{data["last_checked"] or 'Never'}</span>
            </div>
            {error_display}
        </div>
    </div>
    """
    return card_html


def generate_dashboard_html(status_data):
    """
    Generates the full HTML dashboard using a template with placeholders.
    """
    # Calculate overall status
    services = list(status_data.values())
    if not services:
        overall_status = "unknown"
        overall_msg = "No services configured or polling not started."
    else:
        up_count = sum(1 for s in services if s["status"] == "up")
        down_count = sum(1 for s in services if s["status"] == "down")
        unknown_count = sum(1 for s in services if s["status"] == "unknown")
        
        if down_count == 0 and unknown_count == 0:
            overall_status = "up"
            overall_msg = "All systems operational"
        elif down_count > 0:
            overall_status = "down"
            overall_msg = f"{down_count} service(s) down"
        else:
            overall_status = "degraded"
            overall_msg = f"{unknown_count} service(s) unknown"

    # Build cards HTML
    cards_html = ""
    for name, data in status_data.items():
        cards_html += generate_card_html(name, data)

    # Use a plain string template with placeholders
    html_template = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Homelab Status</title>
    <style>
        :root {
            --bg-color: #121212;
            --card-bg: #1e1e1e;
            --text-primary: #e0e0e0;
            --text-secondary: #a0a0a0;
            --border-color: #333;
            --up-color: #4caf50;
            --down-color: #f44336;
            --unknown-color: #9e9e9e;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-primary);
            margin: 0;
            padding: 20px;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
        }
        .banner {
            padding: 15px 20px;
            border-radius: 8px;
            margin-bottom: 20px;
            text-align: center;
            font-size: 1.2em;
            font-weight: bold;
        }
        .banner.up { background-color: rgba(76, 175, 80, 0.2); color: var(--up-color); border: 1px solid var(--up-color); }
        .banner.down { background-color: rgba(244, 67, 54, 0.2); color: var(--down-color); border: 1px solid var(--down-color); }
        .banner.degraded { background-color: rgba(255, 152, 0, 0.2); color: #ff9800; border: 1px solid #ff9800; }
        .banner.unknown { background-color: rgba(158, 158, 158, 0.2); color: var(--unknown-color); border: 1px solid var(--unknown-color); }

        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 20px;
        }
        .card {
            background-color: var(--card-bg);
            border-radius: 8px;
            padding: 20px;
            border: 1px solid var(--border-color);
            transition: transform 0.2s;
        }
        .card:hover {
            transform: translateY(-2px);
        }
        .card.up { border-top: 4px solid var(--up-color); }
        .card.down { border-top: 4px solid var(--down-color); }
        .card.unknown { border-top: 4px solid var(--unknown-color); }

        .card-header {
            display: flex;
            align-items: center;
            margin-bottom: 15px;
            padding-bottom: 10px;
            border-bottom: 1px solid var(--border-color);
        }
        .status-icon {
            font-size: 1.5em;
            margin-right: 10px;
        }
        .status-icon.up { color: var(--up-color); }
        .status-icon.down { color: var(--down-color); }
        .status-icon.unknown { color: var(--unknown-color); }

        .card-header h3 {
            margin: 0;
            font-size: 1.1em;
            font-weight: 600;
        }
        .card-body {
            display: flex;
            flex-direction: column;
            gap: 10px;
        }
        .metric {
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .label {
            color: var(--text-secondary);
            font-size: 0.9em;
        }
        .value {
            font-weight: 500;
        }
        .status-text-up { color: var(--up-color); }
        .status-text-down { color: var(--down-color); }
        .status-text-unknown { color: var(--unknown-color); }

        .error {
            margin-top: 10px;
            padding: 8px;
            background-color: rgba(244, 67, 54, 0.1);
            border: 1px solid var(--down-color);
            border-radius: 4px;
            color: var(--down-color);
            font-size: 0.85em;
        }
        .footer {
            text-align: center;
            margin-top: 30px;
            color: var(--text-secondary);
            font-size: 0.8em;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="banner %%BANNER_CLASS%%">
            %%BANNER_MSG%%
        </div>
        <div class="grid">
            %%CARDS%%
        </div>
        <div class="footer">
            Homelab Status Page &bull; Auto-refreshes every 10s
        </div>
    </div>

    <script>
        async function refreshStatus() {
            try {
                const response = await fetch('/api/status');
                if (!response.ok) throw new Error('Network response was not ok');
                const data = await response.json();
                updateDashboard(data);
            } catch (error) {
                console.error('Failed to fetch status:', error);
            }
        }

        function updateDashboard(data) {
            // Update banner
            const services = Object.values(data);
            const banner = document.querySelector('.banner');
            
            if (services.length === 0) {
                banner.className = 'banner unknown';
                banner.textContent = 'No services configured or polling not started.';
                return;
            }

            const upCount = services.filter(s => s.status === 'up').length;
            const downCount = services.filter(s => s.status === 'down').length;
            const unknownCount = services.filter(s => s.status === 'unknown').length;

            if (downCount === 0 && unknownCount === 0) {
                banner.className = 'banner up';
                banner.textContent = 'All systems operational';
            } else if (downCount > 0) {
                banner.className = 'banner down';
                banner.textContent = `${downCount} service(s) down`;
            } else {
                banner.className = 'banner degraded';
                banner.textContent = `${unknownCount} service(s) unknown`;
            }

            // Update cards
            const grid = document.querySelector('.grid');
            grid.innerHTML = '';

            for (const [name, svc] of Object.entries(data)) {
                const card = document.createElement('div');
                card.className = `card ${svc.status}`;
                
                let errorHtml = '';
                if (svc.error) {
                    errorHtml = `<div class="error">Error: ${svc.error}</div>`;
                }

                card.innerHTML = `
                    <div class="card-header">
                        <span class="status-icon ${svc.status}">●</span>
                        <h3>${name}</h3>
                    </div>
                    <div class="card-body">
                        <div class="metric">
                            <span class="label">Status</span>
                            <span class="value status-text-${svc.status}">${svc.status.toUpperCase()}</span>
                        </div>
                        <div class="metric">
                            <span class="label">Latency</span>
                            <span class="value">${svc.latency_ms} ms</span>
                        </div>
                        <div class="metric">
                            <span class="label">HTTP Code</span>
                            <span class="value">${svc.http_code || 'N/A'}</span>
                        </div>
                        <div class="metric">
                            <span class="label">Last Checked</span>
                            <span class="value">${svc.last_checked || 'Never'}</span>
                        </div>
                        ${errorHtml}
                    </div>
                `;
                grid.appendChild(card);
            }
        }

        // Initial load
        refreshStatus();
        // Refresh every 10 seconds
        setInterval(refreshStatus, 10000);
    </script>
</body>
</html>"""

    # Replace placeholders
    html = html_template.replace("%%BANNER_CLASS%%", overall_status)
    html = html.replace("%%BANNER_MSG%%", overall_msg)
    html = html.replace("%%CARDS%%", cards_html)

    return html


# --- HTTP Server ---

class StatusHandler(http.server.SimpleHTTPRequestHandler):
    """
    Handles GET / and GET /api/status.
    """
    
    def do_GET(self):
        if self.path == '/api/status':
            self._serve_json()
        elif self.path == '/' or self.path == '/index.html':
            self._serve_dashboard()
        else:
            self.send_error(404, "Not Found")

    def _serve_json(self):
        with cache_lock:
            # Return a copy of the cache
            data = dict(status_cache)
        
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2).encode('utf-8'))

    def _serve_dashboard(self):
        with cache_lock:
            data = dict(status_cache)
        
        html = generate_dashboard_html(data)
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))

    def log_message(self, format, *args):
        # Suppress default logging for cleaner output
        pass


def main():
    parser = argparse.ArgumentParser(description="Homelab Status Page Server")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port to listen on")
    parser.add_argument("--config", type=str, default=DEFAULT_CONFIG, help="Path to config.json")
    args = parser.parse_args()

    config_path = args.config
    port = args.port

    if not os.path.exists(config_path):
        print(f"[ERROR] Config file '{config_path}' not found.", file=sys.stderr)
        sys.exit(1)

    print(f"[INFO] Starting Homelab Status Page on port {port}")
    print(f"[INFO] Loading config from {config_path}")
    print(f"[INFO] Polling interval: {POLL_INTERVAL_SECONDS}s")

    # Start background poller
    poller_thread = threading.Thread(target=poll_loop, args=(config_path,), daemon=True)
    poller_thread.start()

    # Start HTTP server
    with socketserver.TCPServer(("", port), StatusHandler) as httpd:
        print(f"[INFO] Dashboard available at http://localhost:{port}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[INFO] Shutting down server...")
            httpd.shutdown()

if __name__ == "__main__":
    main()