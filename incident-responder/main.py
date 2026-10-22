import asyncio
import json
import logging
import os
import re
import sys
import time

import aiohttp
from aiohttp import web

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("incident-responder")

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
if not DISCORD_WEBHOOK_URL:
    sys.exit("FATAL: DISCORD_WEBHOOK_URL environment variable is required")
PROMETHEUS_URL = os.environ.get(
    "PROMETHEUS_URL",
    "http://kube-prometheus-stack-prometheus.monitoring:9090/prometheus",
)
LOKI_URL = os.environ.get(
    "LOKI_URL",
    "http://loki-gateway.monitoring:80",
)
OLLAMA_URL = os.environ.get("OLLAMA_URL", "")  # empty = skip AI
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "minicpm5-nothink")
OLLAMA_THINK = os.environ.get("OLLAMA_THINK", "").strip().lower()
OLLAMA_STRUCTURED = os.environ.get("OLLAMA_STRUCTURED", "true").strip().lower() not in (
    "",
    "0",
    "false",
    "no",
)
OLLAMA_OPTIONS = {
    "num_ctx": 2048,
    "num_predict": int(os.environ.get("OLLAMA_NUM_PREDICT", "256")),
    "temperature": float(os.environ.get("OLLAMA_TEMPERATURE", "0.3")),
    "top_p": float(os.environ.get("OLLAMA_TOP_P", "0.95")),
    "min_p": float(os.environ.get("OLLAMA_MIN_P", "0.0")),
    "seed": int(os.environ.get("OLLAMA_SEED", "42")),
}

AI_SYSTEM_PROMPT_LEGACY = (
    "You are a Minecraft server admin assistant. Use ONLY the metrics and log lines provided.\n"
    "Rules:\n"
    "- TPS is ticks per second: 20 is healthy, below 15 is degraded, below 10 is severe.\n"
    "- Heap is percent of max heap; Load is system load average; Players is current player count.\n"
    "- If a metric is None or the log sample is empty, say the data is insufficient. "
    "Do not treat missing metrics as zero.\n"
    "- TargetDown means Prometheus cannot scrape the Minecraft metrics target. "
    "It does not mean zero players.\n"
    "- Do not invent numbers, causes, players, or events. Every number you mention must appear in the data.\n"
    "- Do not recommend impossible or unsafe actions such as spawning server threads, "
    "increasing GC pause time, killing processes or threads, or deleting world data.\n"
    "- Answer in 3-5 sentences. Start with the most likely cause supported by the data, "
    "then the single best next action.\n"
    "- If the alert and the data disagree, say the alert may be stale or misconfigured.\n"
    "- Respond in English."
)

AI_SYSTEM_PROMPT = (
    "You are a Minecraft server incident summarizer. Use ONLY the facts provided.\n"
    "Return exactly one JSON object, with no markdown and no prose outside it.\n"
    'Schema: {"cause": "...", "evidence": ["M1"], "action": "...", '
    '"confidence": "low|medium|high", "missing": []}\n'
    "Rules:\n"
    "- Cite at least one valid evidence ID in every answer.\n"
    "- Do not mention any number that is not in the facts.\n"
    "- The cause must match A2: if A2 starts with consistent, do not call the alert "
    "stale or misconfigured; if A2 starts with contradicted, say the alert may be "
    "stale or misconfigured; if A2 starts with not, say the alert is unconfirmed.\n"
    "- For a consistent alert, recommend a real remediation, not checking the alert "
    "rule or threshold.\n"
    "- Never say no action is required and never tell the operator to ignore an alert.\n"
    "- If A3 says a field is missing, list it in missing; never call a present field missing.\n"
    "- Do not recommend killing processes or threads, deleting world data, spawning "
    "server threads, increasing GC pause time, or increasing tick rate.\n"
    "- Keep cause and action to one sentence each.\n"
    "Example 1:\n"
    "Facts: A2 Alert/metric consistency: contradicted (players=0); M4 Players: 0\n"
    'Output: {"cause": "The PlayerCountHigh alert is stale because the current player count is 0.", '
    '"evidence": ["A2", "M4"], "action": "Check the player-count query and the '
    'PlayerCountHigh alert rule.", "confidence": "high", "missing": []}\n'
    "Example 2:\n"
    "Facts: A1 Alert: TargetDown (critical); A3 Data completeness: TPS, heap, load, "
    "players, and logs missing\n"
    'Output: {"cause": "Prometheus cannot scrape the Minecraft metrics target, so the '
    'metrics are missing rather than zero.", "evidence": ["A1", "A3"], '
    '"action": "Check the Prometheus target and scrape configuration.", '
    '"confidence": "high", "missing": ["tps", "heap", "load", "players", "logs"]}'
)

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_UNSAFE_ADVICE_RE = re.compile(
    r"\b(?:kill|killed|killing|terminate|terminating|spawn(?:ing)? .*threads?|"
    r"increase .*tick rate|increase .*gc pause|close unused java processes)\b",
    re.IGNORECASE,
)
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")
_FALSE_DATA_MISSING_RE = re.compile(
    r"\b(?:data|metrics?|values?)\b[^.]{0,60}"
    r"\b(?:missing|insufficient|unavailable|not available|not provided)\b",
    re.IGNORECASE,
)


def _fmt(value) -> str:
    if value is None:
        return "missing"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _tps_status(tps: float | None) -> str:
    if tps is None:
        return "missing"
    if tps < 10:
        return "severe"
    if tps < 15:
        return "degraded"
    if tps < 20:
        return "below target"
    return "healthy"


def _heap_status(heap: float | None) -> str:
    if heap is None:
        return "missing"
    if heap >= 95:
        return "critical"
    if heap >= 85:
        return "high"
    return "normal"


def _load_status(load: float | None) -> str:
    if load is None:
        return "missing"
    if load > 6:
        return "high"
    return "normal"


def _has_log(logs: str, *needles: str) -> bool:
    lowered = logs.lower()
    return any(needle.lower() in lowered for needle in needles)


def _is_present(diag: dict, key: str) -> bool:
    data_present = diag.get("data_present")
    if isinstance(data_present, dict) and key in data_present:
        return bool(data_present[key])
    if key == "players":
        return diag.get("players") is not None
    if key == "logs":
        return bool(diag.get("logs_sample"))
    return diag.get(key) is not None


def _infer_consistency(
    alert_name: str, severity: str, diag: dict, present: dict, logs: str
) -> str:
    name = alert_name.lower()
    tps = diag.get("tps") if present["tps"] else None
    heap = diag.get("heap_pct") if present["heap"] else None
    load = diag.get("load") if present["load"] else None
    players = diag.get("players") if present["players"] else None

    if "target" in name or "down" in name:
        if not any(present[key] for key in ("tps", "heap", "load")):
            return "consistent: the scrape target is down and metrics are missing"
        return "contradicted: metrics are present, so the alert may be stale"

    if "tps" in name:
        if tps is None:
            return "not verifiable: TPS is missing"
        if tps < 10:
            detail = f"consistent: TPS {_fmt(tps)} is severe (<10)"
            if severity != "critical":
                detail += "; alert severity is lower than metric severity"
            return detail
        if tps < 15:
            return f"consistent: TPS {_fmt(tps)} is below 15"
        return f"contradicted: TPS {_fmt(tps)} is at or above 15"

    if "heap" in name:
        if heap is None:
            return "not verifiable: heap is missing"
        if heap >= 95:
            detail = f"consistent: heap {_fmt(heap)}% is critical (>95%)"
            if severity != "critical":
                detail += "; alert severity is lower than metric severity"
            return detail
        if heap >= 85:
            return f"consistent: heap {_fmt(heap)}% is high (>85%)"
        return f"contradicted: heap {_fmt(heap)}% is below the 85% warning threshold"

    if "load" in name:
        if load is None:
            return "not verifiable: system load is missing"
        if load > 6:
            return f"consistent: system load {_fmt(load)} is above 6"
        return f"contradicted: system load {_fmt(load)} is at or below 6"

    if "disconnect" in name:
        if _has_log(logs, "left the game", "disconnect"):
            return "consistent: logs contain player disconnect events"
        return "not automatically verifiable: no disconnect log lines in the sample"

    if "gc" in name:
        if _has_log(logs, "gc"):
            return "consistent: logs contain GC activity"
        return "not automatically verifiable: no GC log lines in the sample"

    if "chunk" in name:
        if _has_log(logs, "chunk", "can't keep up"):
            return "consistent: logs contain chunk or tick pressure"
        return "not automatically verifiable: no chunk log lines in the sample"

    if "disk" in name:
        if _has_log(logs, "no space", "disk"):
            return "consistent: logs contain a disk-space error"
        return "not automatically verifiable: no disk log lines in the sample"

    if "memory" in name or "leak" in name:
        if heap is not None and heap >= 85:
            return f"consistent: heap {_fmt(heap)}% is high"
        if _has_log(logs, "outofmemory", "memory"):
            return "consistent: logs contain memory pressure"
        return "not automatically verifiable: current heap and logs do not confirm growth"

    if "player" in name:
        if players is None:
            return "not verifiable: players is missing"
        if players == 0:
            return "contradicted: current player count is 0"
        return f"not automatically verifiable: current player count is {players}"

    return "not automatically verifiable: no deterministic check for this alert"


def _salient_logs(logs: str) -> list[str]:
    lines = [line.strip() for line in logs.splitlines() if line.strip()]
    if not lines:
        return []
    interesting = [
        line
        for line in lines
        if re.search(
            r"error|warn|exception|outofmemory|no space|can't keep up|gc|chunk|"
            r"left the game|disconnect|watchdog|tick",
            line,
            re.IGNORECASE,
        )
    ]
    selected = interesting[-8:] if interesting else lines[-5:]
    return [line[:240] for line in selected]


def build_fact_sheet(alert_name: str, severity: str, diag: dict) -> dict:
    present = {
        key: _is_present(diag, key)
        for key in ("tps", "heap", "load", "players", "logs")
    }
    missing = [key for key, value in present.items() if not value]
    logs = diag.get("logs_sample") or ""
    completeness = (
        "all metrics and logs present"
        if not missing
        else "missing: " + ", ".join(missing)
    )
    consistency = _infer_consistency(alert_name, severity, diag, present, logs)

    facts = [
        {"id": "A1", "text": f"Alert: {alert_name} ({severity})"},
        {"id": "A2", "text": f"Alert/metric consistency: {consistency}"},
        {"id": "A3", "text": f"Data completeness: {completeness}"},
    ]

    tps = diag.get("tps") if present["tps"] else None
    heap = diag.get("heap_pct") if present["heap"] else None
    load = diag.get("load") if present["load"] else None
    players = diag.get("players") if present["players"] else None

    facts.append(
        {
            "id": "M1",
            "text": (
                f"TPS: {_fmt(tps)} ({_tps_status(tps)}; "
                "thresholds: healthy 20, warning <15, severe <10)"
                if present["tps"]
                else "TPS: missing"
            ),
        }
    )
    facts.append(
        {
            "id": "M2",
            "text": (
                f"Heap: {_fmt(heap)}% ({_heap_status(heap)}; "
                "thresholds: warning >85%, critical >95%)"
                if present["heap"]
                else "Heap: missing"
            ),
        }
    )
    facts.append(
        {
            "id": "M3",
            "text": (
                f"System load: {_fmt(load)} ({_load_status(load)}; "
                "HighSystemLoad threshold >6)"
                if present["load"]
                else "System load: missing"
            ),
        }
    )
    facts.append(
        {
            "id": "M4",
            "text": f"Players: {_fmt(players)}" if present["players"] else "Players: missing",
        }
    )

    if present["logs"]:
        for index, line in enumerate(_salient_logs(logs), start=1):
            facts.append({"id": f"L{index}", "text": f"Log: {line}"})

    numbers = set()
    for fact in facts:
        for number in _NUM_RE.findall(fact["text"]):
            try:
                numbers.add(float(number))
            except ValueError:
                pass

    return {
        "facts": facts,
        "ids": {fact["id"] for fact in facts},
        "numbers": numbers,
        "present": present,
        "missing_fields": missing,
        "consistency": consistency,
        "alert_name": alert_name,
        "severity": severity,
    }


def format_fact_sheet(facts: dict) -> str:
    return "\n".join(f"{fact['id']}: {fact['text']}" for fact in facts["facts"])


def build_structured_prompt(facts: dict) -> str:
    return (
        f"Facts:\n{format_fact_sheet(facts)}\n\n"
        "Return the JSON object now."
    )


def _extract_json_object(text: str) -> tuple[dict | None, str]:
    text = _THINK_BLOCK_RE.sub("", text)
    text = text.replace("<|im_end|>", "").replace("</s>", "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None, "no JSON object in response"
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc}"
    if not isinstance(parsed, dict):
        return None, "JSON response is not an object"
    return parsed, ""


def _field_aliases() -> dict[str, tuple[str, ...]]:
    return {
        "tps": ("tps",),
        "heap": ("heap",),
        "load": ("load",),
        "players": ("players", "player count"),
        "logs": ("logs", "log"),
    }


def validate_structured_analysis(data: dict, facts: dict) -> tuple[bool, str]:
    required = {"cause", "evidence", "action", "confidence", "missing"}
    if not required <= data.keys():
        return False, f"missing keys: {sorted(required - data.keys())}"

    cause = data.get("cause")
    action = data.get("action")
    confidence = data.get("confidence")
    evidence = data.get("evidence")
    missing = data.get("missing")

    if not isinstance(cause, str) or not cause.strip():
        return False, "cause is empty"
    if not isinstance(action, str) or not action.strip():
        return False, "action is empty"
    if not isinstance(confidence, str) or confidence.strip().lower() not in (
        "low",
        "medium",
        "high",
    ):
        return False, "confidence is invalid"
    if not isinstance(evidence, list) or not evidence:
        return False, "evidence is empty"
    if not isinstance(missing, list):
        return False, "missing is not a list"

    invalid_evidence = [
        item
        for item in evidence
        if not isinstance(item, str) or item not in facts["ids"]
    ]
    if invalid_evidence:
        return False, f"invalid evidence IDs: {invalid_evidence}"

    consistency_kind = facts["consistency"].split(":", 1)[0].strip().lower()
    cause_lower = cause.lower()
    if consistency_kind == "consistent":
        if re.search(
            r"\b(?:stale|misconfigur\w*|contradict\w*|false|invalid|not valid)\b",
            cause_lower,
        ):
            return False, "cause contradicts A2 consistency"
        if re.search(r"alert (?:rule|configuration|threshold)", action, re.IGNORECASE):
            return False, "action reviews the alert rule for a consistent alert"
    elif consistency_kind == "contradicted":
        if not re.search(
            r"\b(?:stale|misconfigur\w*|contradict\w*|false|invalid|not valid|"
            r"not supported|unsupported|doesn't match|does not match)\b",
            cause_lower,
        ):
            return False, "cause does not reflect A2 contradiction"
    elif consistency_kind.startswith("not"):
        if re.search(r"\b(?:stale|misconfigur\w*|contradict\w*)\b", cause_lower):
            return False, "cause overstates A2"

    if re.search(
        r"\bignore\b|"
        r"\b(?:no|not)\s+(?:remediation|action|fix|further action)\b|"
        r"\bnothing (?:is )?required\b|\bdo nothing\b",
        action,
        re.IGNORECASE,
    ):
        return False, "action is dismissive or unsafe"

    for item in missing:
        if not isinstance(item, str):
            return False, "missing contains a non-string"
        key = item.strip().lower()
        if key not in facts["missing_fields"]:
            return False, f"claims {key} is missing but it is present"

    text = f"{cause} {action}".strip()
    for number in _NUM_RE.findall(text):
        try:
            value = float(number)
        except ValueError:
            continue
        if value not in facts["numbers"]:
            return False, f"number {number} is not in the facts"

    if not facts["missing_fields"] and _FALSE_DATA_MISSING_RE.search(text):
        return False, "claims data is missing while all fields are present"

    for key, aliases in _field_aliases().items():
        if not facts["present"].get(key):
            continue
        for alias in aliases:
            field_pattern = re.compile(
                rf"\b{re.escape(alias)}\b[^.]{{0,50}}"
                r"\b(?:data|metric|value)?\b[^.]{0,20}"
                r"\b(?:is|are)\s+(?:missing|unavailable|not available)\b",
                re.IGNORECASE,
            )
            if field_pattern.search(text):
                return False, f"claims {key} is missing but it is present"

    if _UNSAFE_ADVICE_RE.search(action):
        return False, "action contains a blocked recommendation"

    return True, ""


def fallback_action(alert_name: str) -> str:
    name = alert_name.lower()
    if "target" in name or "down" in name:
        return "Check the Prometheus target and scrape configuration."
    if "tps" in name:
        return "Inspect server logs, chunk loading, and CPU pressure before changing the server."
    if "heap" in name or "memory" in name or "leak" in name:
        return "Inspect heap usage and GC logs; do not raise the heap beyond the pod limit."
    if "load" in name:
        return "Inspect CPU usage and server-thread activity."
    if "disk" in name:
        return "Free disk space and check the Minecraft volume."
    if "gc" in name:
        return "Inspect GC logs and heap pressure."
    if "chunk" in name:
        return "Inspect chunk-loading activity and server tick time."
    if "disconnect" in name:
        return "Inspect server logs for disconnect causes."
    if "player" in name:
        return "Verify the player-count query and alert rule."
    return "Inspect the current logs and metrics before taking action."


def fallback_analysis(facts: dict) -> str:
    consistency = facts["consistency"]
    if consistency.startswith("contradicted"):
        cause = f"The alert may be stale or misconfigured: {consistency}."
    elif consistency.startswith("consistent"):
        cause = f"The available data supports the alert: {consistency}."
    else:
        cause = f"The available data does not fully confirm the alert: {consistency}."

    metrics = "; ".join(
        fact["text"] for fact in facts["facts"] if fact["id"].startswith("M")
    )
    if consistency.startswith("contradicted"):
        action = (
            f"Verify the {facts['alert_name']} alert rule against the current "
            "metrics before acting."
        )
    else:
        action = fallback_action(facts["alert_name"])
    return (
        "Automated fallback (model output failed validation). "
        f"{cause} {metrics}. {action} Verify before acting."
    )


def render_structured_analysis(data: dict) -> str:
    return (
        f"{data['cause'].strip()} Next action: {data['action'].strip()} "
        f"(Confidence: {data['confidence'].strip().lower()}.)"
    )


def sanitize_analysis(text: str) -> str:
    """Remove thinking/stop-token leakage and drop unsafe recommendation sentences."""
    text = _THINK_BLOCK_RE.sub("", text)
    text = text.replace("<|im_end|>", "").replace("</s>", "").strip()
    if not text:
        return ""

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    safe = [s for s in sentences if not _UNSAFE_ADVICE_RE.search(s)]
    if len(safe) < 2:
        log.warning("AI recommendation contained an unsafe action; withholding it")
        return (
            "The model's recommendation included a potentially unsafe action and was withheld. "
            "Inspect the server logs and thread dump before taking action."
        )
    if len(safe) != len(sentences):
        log.warning("Removed unsafe AI recommendation sentence(s)")
        safe.append(
            "Verify the finding against current logs and metrics before taking action."
        )
    return " ".join(safe)


def build_ai_prompt(alert_name: str, severity: str, diag: dict) -> str:
    players = _fmt(diag.get("players")) if _is_present(diag, "players") else "missing"
    heap = _fmt(diag.get("heap_pct")) if _is_present(diag, "heap") else "missing"
    heap_display = f"{heap}%" if heap != "missing" else heap
    return (
        f"Alert: {alert_name} ({severity})\n"
        "Metrics:\n"
        f"- TPS: {_fmt(diag.get('tps'))}\n"
        f"- Heap: {heap_display}\n"
        f"- System load: {_fmt(diag.get('load'))}\n"
        f"- Players: {players}\n"
        f"Recent logs (last 5m):\n{diag.get('logs_sample', '')[:800]}\n\n"
        "Diagnose the likely cause supported by this data and recommend the next action. "
        "If key data is missing, say so explicitly. Be concise (3-5 sentences)."
    )


# Track alert fingerprints to avoid duplicate diagnostics
RECENT_ALERTS = {}
DEDUP_WINDOW = 300  # seconds
_last_prune = 0.0  # last time RECENT_ALERTS was pruned
_alert_lock = asyncio.Lock()


async def is_duplicate(fingerprint: str) -> bool:
    global _last_prune
    now = time.time()

    async with _alert_lock:
        # Periodic pruning: clean up entries older than DEDUP_WINDOW
        if now - _last_prune >= DEDUP_WINDOW:
            stale_keys = [
                k for k, ts in RECENT_ALERTS.items()
                if now - ts >= DEDUP_WINDOW
            ]
            for k in stale_keys:
                del RECENT_ALERTS[k]
            _last_prune = now

        if fingerprint in RECENT_ALERTS:
            if now - RECENT_ALERTS[fingerprint] < DEDUP_WINDOW:
                return True
        RECENT_ALERTS[fingerprint] = now
        return False


def severity_color(severity: str) -> int:
    return {"critical": 0xE74C3C, "warning": 0xF39C12, "info": 0x3498DB}.get(
        severity, 0x95A5A6
    )


async def post_discord(embed: dict):
    async with aiohttp.ClientSession() as session:
        async with session.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]}) as resp:
            if resp.status >= 400:
                body = await resp.text()
                log.error("Discord webhook failed: %s %s", resp.status, body)


async def query_prometheus(query: str) -> list | None:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{PROMETHEUS_URL}/api/v1/query",
                params={"query": query},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
                if data["status"] == "success":
                    return data["data"]["result"]
    except Exception as e:
        log.warning("Prometheus query failed: %s — %s", query, e)
    return None


async def query_loki(query: str, minutes: int = 5) -> str:
    try:
        params = {
            "query": query,
            "start": str(int((time.time() - minutes * 60) * 1e9)),
            "end": str(int(time.time() * 1e9)),
            "limit": "50",
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{LOKI_URL}/loki/api/v1/query_range",
                params=params,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
                if data["status"] == "success":
                    lines = []
                    for result in data["data"]["result"]:
                        for ts, line in result.get("values", []):
                            lines.append(line)
                    return "\n".join(lines[-30:])
    except Exception as e:
        log.warning("Loki query failed: %s", e)
    return ""


async def query_ollama(
    prompt: str,
    timeout: int = 120,
    system: str = None,
    facts: dict | None = None,
) -> str | None:
    if not OLLAMA_URL:
        return None

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": OLLAMA_OPTIONS,
    }
    if facts is not None and OLLAMA_STRUCTURED:
        payload["format"] = "json"
    if OLLAMA_THINK in ("true", "false"):
        payload["think"] = OLLAMA_THINK == "true"

    started = time.monotonic()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{OLLAMA_URL}/api/chat",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    log.warning(
                        "Ollama returned %s after %.1fs: %s",
                        resp.status,
                        time.monotonic() - started,
                        body[:300],
                    )
                    return None
                data = await resp.json()
    except Exception as e:
        log.warning(
            "Ollama query failed after %.1fs: %s",
            time.monotonic() - started,
            e,
        )
        return None

    elapsed = time.monotonic() - started
    message = data.get("message") or {}
    content = (message.get("content") or "").strip()
    thinking = message.get("thinking") or ""
    total_s = data.get("total_duration", 0) / 1e9
    load_s = data.get("load_duration", 0) / 1e9
    eval_count = data.get("eval_count", 0)
    eval_s = data.get("eval_duration", 0) / 1e9
    tok_per_s = eval_count / eval_s if eval_s else 0

    if not content:
        log.warning(
            "Ollama returned empty content after %.1fs "
            "(model=%s, thinking=%d chars, done=%s)",
            elapsed,
            OLLAMA_MODEL,
            len(thinking),
            data.get("done_reason"),
        )
        return None

    if facts is not None and OLLAMA_STRUCTURED:
        parsed, reason = _extract_json_object(content)
        if parsed is None:
            log.warning("Structured AI analysis rejected: %s", reason)
            clean = fallback_analysis(facts)
        else:
            valid, reason = validate_structured_analysis(parsed, facts)
            if not valid:
                log.warning("Structured AI analysis rejected: %s", reason)
                clean = fallback_analysis(facts)
            else:
                clean = render_structured_analysis(parsed)
    else:
        clean = sanitize_analysis(content)

    if not clean:
        log.warning("Ollama response was empty after sanitization")
        return None

    log.info(
        "Ollama %s: wall=%.1fs total=%.1fs load=%.1fs eval=%d tok (%.1f tok/s) done=%s structured=%s",
        OLLAMA_MODEL,
        elapsed,
        total_s,
        load_s,
        eval_count,
        tok_per_s,
        data.get("done_reason"),
        "yes" if facts is not None and OLLAMA_STRUCTURED else "no",
    )
    return clean


async def gather_diagnostics() -> dict:
    tps_data, heap_data, load_data, players_data, uptime_data, logs = await asyncio.gather(
        query_prometheus("paper_tps_1m"),
        query_prometheus("java_lang_Memory_HeapMemoryUsage_used / java_lang_Memory_HeapMemoryUsage_max"),
        query_prometheus("java_lang_OperatingSystem_SystemLoadAverage"),
        query_prometheus("increase(minecraft_play_time_ticks_total[5m]) > 0"),
        query_prometheus('time() - process_start_time_seconds{job="minecraft-metrics"}'),
        query_loki('{app="minecraft"}', 5),
    )

    tps = float(tps_data[0]["value"][1]) if tps_data else None
    heap = float(heap_data[0]["value"][1]) if heap_data else None
    load = float(load_data[0]["value"][1]) if load_data else None
    players = len(players_data) if players_data else 0
    uptime_sec = float(uptime_data[0]["value"][1]) if uptime_data else None

    data_present = {
        "tps": bool(tps_data),
        "heap": bool(heap_data),
        "load": bool(load_data),
        "players": players_data is not None,
        "logs": bool(logs),
    }

    return {
        "tps": tps,
        "heap_pct": heap * 100 if heap is not None else None,
        "load": load,
        "players": players,
        "uptime_days": round(uptime_sec / 86400, 1) if uptime_sec else None,
        "logs_sample": logs[:1000] if logs else "",
        "data_present": data_present,
    }


def build_alert_embed(alert: dict) -> dict:
    labels = alert.get("labels", {})
    annotations = alert.get("annotations", {})
    severity = labels.get("severity", "info")
    name = labels.get("alertname", "Unknown")
    status = alert.get("status", "firing")

    desc = annotations.get("description", annotations.get("summary", ""))
    color = severity_color(severity)

    embed = {
        "title": f"{'🔥' if status == 'firing' else '✅'} {name}",
        "description": desc,
        "color": color,
        "fields": [],
        "timestamp": alert.get("startsAt", ""),
    }

    for k, v in sorted(labels.items()):
        if k not in ("alertname", "severity", "namespace"):
            embed["fields"].append({"name": k, "value": v, "inline": True})

    values = alert.get("values", {})
    if values:
        embed["fields"].append(
            {"name": "Value", "value": str(values.get("A", "?")), "inline": True}
        )

    return embed


def build_diagnostic_embed(alert: dict, diag: dict) -> dict:
    labels = alert.get("labels", {})
    name = labels.get("alertname", "Unknown")
    severity = labels.get("severity", "info")

    fields = []
    if diag["tps"] is not None:
        fields.append({"name": "TPS", "value": f"{diag['tps']:.1f}", "inline": True})
    if diag["heap_pct"] is not None:
        fields.append(
            {"name": "Heap", "value": f"{diag['heap_pct']:.1f}%", "inline": True}
        )
    if diag["load"] is not None:
        fields.append(
            {"name": "System Load", "value": f"{diag['load']:.2f}", "inline": True}
        )
    if _is_present(diag, "players"):
        fields.append({"name": "Players", "value": str(diag["players"]), "inline": True})
    else:
        fields.append({"name": "Players", "value": "missing", "inline": True})
    if diag["uptime_days"] is not None:
        fields.append(
            {"name": "Uptime", "value": f"{diag['uptime_days']}d", "inline": True}
        )

    description = f"Diagnostics for **{name}**"
    if diag["logs_sample"]:
        # Truncate and escape for Discord
        log_sample = diag["logs_sample"][:500].replace("`", "'")
        fields.append(
            {
                "name": "Recent Logs (last 5m)",
                "value": f"```{log_sample}```",
                "inline": False,
            }
        )

    return {
        "title": f"📊 {name} — Diagnostics",
        "description": description,
        "color": severity_color(severity),
        "fields": fields,
        "timestamp": alert.get("startsAt", ""),
    }


def build_ai_embed(alert: dict, analysis: str) -> dict:
    labels = alert.get("labels", {})
    name = labels.get("alertname", "Unknown")
    severity = labels.get("severity", "info")
    return {
        "title": f"🤖 {name} — AI Analysis",
        "description": analysis[:2000],
        "color": severity_color(severity),
        "footer": {
            "text": f"Model: {OLLAMA_MODEL} · AI-generated; verify before acting."
        },
    }


async def handle_webhook(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except Exception as e:
        log.error("Failed to parse webhook payload: %s", e)
        return web.Response(status=400, text="invalid json")

    alerts = payload.get("alerts", [])
    log.info("Received %d alert(s)", len(alerts))

    async def process_alerts():
        for alert in alerts:
            try:
                fingerprint = alert.get("fingerprint")
                if not fingerprint:
                    labels = alert.get("labels", {})
                    label_str = "_".join(f"{k}={v}" for k, v in sorted(labels.items()))
                    fingerprint = f"{alert.get('status', 'unknown')}_{label_str}"
                if await is_duplicate(fingerprint):
                    log.info("Skipping duplicate alert: %s", fingerprint)
                    continue

                status = alert.get("status", "firing")
                labels = alert.get("labels", {})
                name = labels.get("alertname", "Unknown")
                log.info("Processing alert: %s (%s)", name, status)

                # 1. Forward alert embed to Discord
                alert_embed = build_alert_embed(alert)
                await post_discord(alert_embed)

                # 2. Gather diagnostics
                diag = await gather_diagnostics()

                # 3. Post diagnostic embed
                diag_embed = build_diagnostic_embed(alert, diag)
                await post_discord(diag_embed)

                # 4. AI analysis via Ollama (best-effort, never blocks)
                try:
                    severity = labels.get("severity", "?")
                    if OLLAMA_STRUCTURED:
                        facts = build_fact_sheet(name, severity, diag)
                        user_msg = build_structured_prompt(facts)
                        analysis = await query_ollama(
                            user_msg,
                            system=AI_SYSTEM_PROMPT,
                            facts=facts,
                        )
                    else:
                        user_msg = build_ai_prompt(name, severity, diag)
                        analysis = await query_ollama(
                            user_msg,
                            system=AI_SYSTEM_PROMPT_LEGACY,
                        )
                    if analysis:
                        ai_embed = build_ai_embed(alert, analysis)
                        await post_discord(ai_embed)
                except Exception as e:
                    log.warning("AI analysis failed (non-critical): %s", e)

                log.info("Completed diagnostics for: %s", name)
            except Exception as e:
                log.error("Failed to process alert %s: %s",
                          alert.get("fingerprint", "unknown"), e)

    async def _run_alerts():
        try:
            await process_alerts()
        except Exception as e:
            log.error("Unhandled error in process_alerts: %s", e)

    asyncio.create_task(_run_alerts())
    return web.Response(status=200, text="ok")


async def health(request: web.Request) -> web.Response:
    return web.Response(status=200, text="ok")


def main():
    app = web.Application()
    app.router.add_post("/webhook", handle_webhook)
    app.router.add_get("/health", health)

    port = int(os.environ.get("PORT", "8080"))
    log.info("Starting incident-responder on port %d", port)
    web.run_app(app, host="0.0.0.0", port=port, access_log=None)


if __name__ == "__main__":
    main()
