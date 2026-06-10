import asyncio
import logging
import os
import sys
import time

import aiohttp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("health-checker")

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
if not DISCORD_WEBHOOK_URL:
    sys.exit("FATAL: DISCORD_WEBHOOK_URL environment variable is required")

# Services to monitor — internal ClusterIP URLs
SERVICES = [
    {"name": "Homepage", "url": "http://homepage-service.default:3000/", "expected": 200},
    {"name": "Grafana", "url": "http://kube-prometheus-stack-grafana.monitoring:80/api/health", "expected": 200},
    {"name": "Prometheus", "url": "http://kube-prometheus-stack-prometheus.monitoring:9090/prometheus/-/healthy", "expected": 200},
    {"name": "Loki", "url": "http://loki-gateway.monitoring:80/", "expected": 200},
    {"name": "ArgoCD", "url": "http://argocd-server.argocd:80/argocd/healthz", "expected": 200},
]


def severity_color(failing: int, total: int) -> int:
    ratio = failing / total if total > 0 else 1
    if ratio >= 0.8:
        return 0xE74C3C  # red
    elif ratio >= 0.4:
        return 0xF39C12  # orange
    return 0x3498DB  # blue


async def check_service(session: aiohttp.ClientSession, service: dict) -> dict:
    name = service["name"]
    url = service["url"]
    expected = service["expected"]

    try:
        start = time.monotonic()
        async with session.get(
            url, timeout=aiohttp.ClientTimeout(total=15), allow_redirects=True
        ) as resp:
            elapsed_ms = round((time.monotonic() - start) * 1000, 1)
            status = resp.status
            return {
                "name": name,
                "url": url,
                "status": status,
                "expected": expected,
                "healthy": status == expected,
                "response_time": elapsed_ms,
            }
    except asyncio.TimeoutError:
        return {
            "name": name,
            "url": url,
            "status": "timeout",
            "expected": expected,
            "healthy": False,
            "response_time": None,
        }
    except Exception as e:
        return {
            "name": name,
            "url": url,
            "status": str(e),
            "expected": expected,
            "healthy": False,
            "response_time": None,
        }


async def post_discord(embed: dict):
    for attempt in range(3):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    DISCORD_WEBHOOK_URL, json={"embeds": [embed]}, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 429:
                        retry_after = float(resp.headers.get("Retry-After", 2 ** attempt))
                        log.warning("Discord rate limited, retrying after %.1fs", retry_after)
                        await asyncio.sleep(retry_after)
                        continue
                    if resp.status >= 400:
                        body = await resp.text()
                        log.error("Discord webhook failed: %s %s", resp.status, body)
                    return
        except Exception as e:
            log.error("Discord webhook error (attempt %d): %s", attempt + 1, e)
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)
    log.error("Discord webhook failed after 3 attempts")


def build_health_embed(results: list, timestamp: str) -> dict:
    total = len(results)
    healthy = sum(1 for r in results if r["healthy"])
    failing = total - healthy

    fields = []
    for r in results:
        icon = "✅" if r["healthy"] else "❌"
        rt = f" ({r['response_time']}ms)" if r["response_time"] is not None else ""
        fields.append(
            {
                "name": f"{icon} {r['name']}",
                "value": f"Status: {r['status']}{rt}",
                "inline": True,
            }
        )

    return {
        "title": f"🏥 Homelab Health Check — {healthy}/{total} services up",
        "description": f"Checked {total} services at {timestamp}" if healthy else f"⚠️ {failing} service(s) DOWN!",
        "color": severity_color(failing, total),
        "fields": fields,
        "footer": {"text": "chai-homelab health-checker"},
        "timestamp": timestamp,
    }


def build_alert_embed(results: list, timestamp: str) -> dict:
    failing = [r for r in results if not r["healthy"]]

    fields = []
    for r in failing:
        rt = f" ({r['response_time']}ms)" if r["response_time"] is not None else ""
        fields.append(
            {
                "name": f"❌ {r['name']}",
                "value": f"URL: `{r['url']}`\nStatus: `{r['status']}`{rt}",
                "inline": False,
            }
        )

    return {
        "title": f"🚨 Homelab Service Alert — {len(failing)} service(s) DOWN!",
        "description": "One or more homelab services are unreachable.",
        "color": 0xE74C3C,
        "fields": fields,
        "footer": {"text": "chai-homelab health-checker"},
        "timestamp": timestamp,
    }


async def run_check():
    log.info("Running health check on %d services...", len(SERVICES))

    async with aiohttp.ClientSession() as session:
        tasks = [check_service(session, svc) for svc in SERVICES]
        results = await asyncio.gather(*tasks)

    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    healthy = sum(1 for r in results if r["healthy"])
    total = len(results)
    failing = [r for r in results if not r["healthy"]]

    # Single message per run: red alert if failures, green summary if all healthy
    if failing:
        embed = build_alert_embed(failing, timestamp)
        log.warning("Alert: %d failing service(s)", len(failing))
    else:
        embed = build_health_embed(results, timestamp)
        log.info("All %d services healthy", total)

    await post_discord(embed)


if __name__ == "__main__":
    asyncio.run(run_check())
