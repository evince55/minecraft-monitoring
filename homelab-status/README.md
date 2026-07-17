# Homelab Status Page

A lightweight, self-contained status dashboard that aggregates the health of homelab services into a single view.

## What it is

`homelab_status.py` is a single-file Python 3 HTTP server (standard library only — no dependencies to install) that:

- **Polls** each configured service on a background thread at a fixed interval.
- **Caches** the latest result for every endpoint.
- **Serves** an HTML dashboard at `GET /` and a JSON API at `GET /api/status`.

The dashboard auto-refreshes every 15 seconds via an inline `fetch()` loop that hits the same-origin API — no CORS, no external dependencies.

## How to run

```bash
cd homelab-status
python3 homelab_status.py        # defaults to port 8080
python3 homelab_status.py 9090   # custom port
```

Open `http://localhost:8080` in a browser.

## Configuration

Services are defined in `config.json` in the same directory. Each entry:

| Field            | Required | Description                                                                 |
|------------------|----------|-----------------------------------------------------------------------------|
| `name`           | Yes      | Display name for the service card.                                          |
| `url`            | Yes      | HTTP(S) endpoint to poll.                                                   |
| `type`           | Yes      | `http_ok` — up if 2xx/3xx. `http_json` — parse a JSON field.                |
| `field`          | No       | For `http_json`: the JSON key to inspect (default: `status`).               |
| `expected_value` | No       | For `http_json`: if set, the field must equal this value to be "up".         |
| `timeout`        | No       | Per-request timeout in seconds (default: 10).                               |

### Example: `http_ok` (simple connectivity check)

```json
{
  "name": "Loki",
  "url": "http://loki-gateway.monitoring/ready",
  "type": "http_ok"
}
```

### Example: `http_json` (parse a specific field)

```json
{
  "name": "aria-backend",
  "url": "http://100.76.103.1:8000/api/health",
  "type": "http_json",
  "field": "status",
  "expected_value": "ok"
}
```

### Example: `http_json` with truthy check (no specific value)

```json
{
  "name": "Some API",
  "url": "http://api.example.com/health",
  "type": "http_json",
  "field": "healthy"
}
```

## Adding a service

1. Open `config.json`.
2. Add a new object to the `services` array following the schema above.
3. Restart the server (or edit and save — the config is read at startup).

## Architecture notes

- **Server-side polling**: The Python server polls endpoints, not the browser. This avoids CORS issues entirely.
- **Isolation**: Each endpoint is polled independently with its own try/except + timeout. A single failure cannot crash the server or block other polls.
- **Config reload**: Config is read once at startup. To add/remove services, restart the process.
- **No external dependencies**: Pure Python 3 standard library (`http.server`, `urllib`, `json`, `threading`).
