# Homelab Status Page

A lightweight, self-contained homelab service health dashboard. Python 3 standard library only — no dependencies to install.

## How It Works

A background thread polls each configured endpoint on an interval, caches results, and serves them via two routes:

- **`GET /`** — HTML dashboard with dark-theme card grid and auto-refresh (10s JS fetch loop)
- **`GET /api/status`** — Aggregated status as JSON

CORS is never an issue because the browser fetches from the same origin.

## Quick Start

```bash
python3 homelab_status.py
```

Open http://localhost:8080 in a browser.

## Configuration

Edit `config.json` (in the same directory as the script). Top-level keys:

| Key | Default | Description |
|---|---|---|
| `poll_interval_seconds` | `30` | Seconds between background polls of all services |

### Service Entry

Each entry in the `services` array:

```json
{
    "name": "My Service",
    "url": "http://example.com/health",
    "type": "http_ok",
    "timeout": 10,
    "field": null,
    "value": null
}
```

| Field | Required | Description |
|---|---|---|
| `name` | yes | Display name on the dashboard |
| `url` | yes | Endpoint to check |
| `type` | yes | `"http_ok"` (2xx/3xx = up) or `"http_json"` (parse JSON response) |
| `timeout` | no | Request timeout in seconds (default 10) |
| `field` | no | JSON field to inspect (required for `http_json`) |
| `value` | no | Expected value of the JSON field (required for `http_json`) |

**Examples:**

- `http_ok`: any 2xx/3xx response means the service is up.
- `http_json` with `field: "status"` and `value: "ok"`: parses the JSON body and checks if `response["status"] == "ok"`.

## Port

The server listens on port `8080` by default. Override with the `STATUS_PORT` environment variable:

```bash
STATUS_PORT=9090 python3 homelab_status.py
```

## Adding a Service

1. Add a new object to the `services` array in `config.json`.
2. Restart the server.

That's it — no code changes needed.
