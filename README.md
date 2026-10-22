<div align="center">

<img src="docs/assets/banner.svg" alt="Minecraft Monitoring Stack" width="100%">

<br/>

**Production-grade observability and ChatOps for a self-hosted PaperMC server — running on a home k3s cluster, deployed entirely by GitOps.**

<br/>

[![PR Validate](https://github.com/evince55/minecraft-monitoring/actions/workflows/pr-validate.yaml/badge.svg)](https://github.com/evince55/minecraft-monitoring/actions/workflows/pr-validate.yaml)
&nbsp;![License: MIT](https://img.shields.io/badge/License-MIT-3DA639?style=flat-square)
&nbsp;![PaperMC](https://img.shields.io/badge/PaperMC-26.1.2-5FA83C?style=flat-square)
&nbsp;![k3s](https://img.shields.io/badge/k3s-v1.34-FFC61C?style=flat-square&logo=k3s&logoColor=black)

![Kubernetes](https://img.shields.io/badge/Kubernetes-326CE5?style=flat-square&logo=kubernetes&logoColor=white)
&nbsp;![Argo CD](https://img.shields.io/badge/GitOps-Argo%20CD-EF7B4D?style=flat-square&logo=argo&logoColor=white)
&nbsp;![Helm](https://img.shields.io/badge/Helm-0F1689?style=flat-square&logo=helm&logoColor=white)
&nbsp;![Prometheus](https://img.shields.io/badge/Prometheus-E6522C?style=flat-square&logo=prometheus&logoColor=white)
&nbsp;![Grafana](https://img.shields.io/badge/Grafana-F46800?style=flat-square&logo=grafana&logoColor=white)
&nbsp;![Loki](https://img.shields.io/badge/Loki-F5A800?style=flat-square&logo=grafana&logoColor=white)
&nbsp;![Python](https://img.shields.io/badge/Python-3776AB?style=flat-square&logo=python&logoColor=white)
&nbsp;![Docker](https://img.shields.io/badge/Docker-2496ED?style=flat-square&logo=docker&logoColor=white)

</div>

---

A self-hosted platform that gives a Minecraft server the same observability you'd expect from a production service: **Prometheus** metrics, **Grafana** dashboards, **Loki** log aggregation, and **AlertManager** routing to Discord. A custom **Discord bot** turns the chat window into a control plane — status, players, whitelist, backups — and an **incident-responder** enriches every alert with live diagnostics and a local-LLM root-cause analysis before it reaches your phone. Everything ships via **Argo CD** GitOps, with secrets synced from **Azure Key Vault** through the External Secrets Operator.

## Highlights

- **Full observability pipeline** — metrics, logs, dashboards, and alerting wired end-to-end for a single game server, on a single-node k3s home cluster.
- **ChatOps control plane** — 12 Discord slash commands manage the server over RCON without SSH or a console.
- **AI-assisted incident response** — alerts arrive pre-enriched with live PromQL/LogQL diagnostics and a MiniCPM5-2B (Ollama) root-cause summary, non-blocking by design.
- **GitOps all the way down** — Argo CD App-of-Apps reconciles the entire stack from this repo; zero manual `kubectl apply`.
- **Operable** — daily zstd PVC backups with restore verification, predictive alerts (memory-leak / disk / TPS decline), and CI that lints and validates every manifest.

## Architecture

```mermaid
%%{init: {'theme':'base','themeVariables':{'fontFamily':'ui-monospace, SFMono-Regular, monospace','primaryColor':'#20262B','primaryTextColor':'#EDE6D6','primaryBorderColor':'#3A4249','lineColor':'#5E6A72','clusterBkg':'#181B1E','clusterBorder':'#2E353B','tertiaryColor':'#181B1E'}}}%%
flowchart TB
    MC["PaperMC 26.1.2<br/>Java 25 · 3 GB heap"]

    subgraph OBS["Observability"]
        PROM["Prometheus<br/>+ AlertManager"]
        GRAF["Grafana<br/>dashboards"]
        LOKI["Loki + Promtail<br/>log aggregation"]
    end

    subgraph OPS["ChatOps + Automation"]
        BOT["Discord Bot<br/>12 slash commands"]
        IR["Incident Responder<br/>diagnostics + AI"]
        OLLA["Ollama · MiniCPM5-2B<br/>local LLM"]
    end

    HOME["Homepage<br/>unified dashboard"]
    DISCORD(["Discord"])

    MC -- "JMX metrics" --> PROM
    MC -- "pod logs" --> LOKI
    BOT -- "RCON" --> MC
    PROM --> GRAF
    LOKI --> GRAF
    PROM -- "fires alert" --> IR
    PROM -- "fires alert" --> DISCORD
    IR -- "PromQL + LogQL" --> PROM
    IR --> LOKI
    IR -- "root-cause" --> OLLA
    IR --> DISCORD
    BOT --> PROM
    BOT --> LOKI
    BOT -- "/analyze-lag" --> OLLA
    BOT --> DISCORD
    HOME --> PROM
    HOME --> GRAF

    classDef game fill:#2E4A22,stroke:#6DBB3C,color:#EDE6D6;
    classDef obs fill:#1E3A38,stroke:#4EC1B8,color:#EDE6D6;
    classDef ops fill:#3A2E14,stroke:#E0A526,color:#EDE6D6;
    classDef ext fill:#2C2340,stroke:#8B7BC7,color:#EDE6D6;
    class MC game;
    class PROM,GRAF,LOKI obs;
    class BOT,IR,OLLA ops;
    class HOME,DISCORD ext;
```

<div align="center"><sub>Deployed via Argo CD App-of-Apps · secrets synced from Azure Key Vault by the External Secrets Operator · ingress through Traefik sub-paths.</sub></div>

## Features

### Discord bot — 12 slash commands

| Command | Description |
|---------|-------------|
| `/status` | Live TPS, player count, heap usage, and uptime in one embed |
| `/players` | Online players with session play-time |
| `/tps` | Current server tick rate |
| `/uptime` | Server uptime |
| `/session` | Total play-time for a specific player |
| `/top-players` | Leaderboard by play-time |
| `/whitelist` | Add or remove a player from the whitelist *(admin)* |
| `/backup` | Save the world and trigger a backup *(admin)* |
| `/ai-status` | Check whether the Ollama model is online |
| `/analyze-lag` | AI lag analysis from live Prometheus metrics |
| `/summarize` | AI summary of recent server logs from Loki |
| `/help` | List every command |

### Monitoring & dashboards

- **Three Grafana dashboards, 20 panels total** — Server (4: TPS, JVM heap, GC rate, CPU/load), Player (8: session stats, movement, crafting, health), and Logs (8: live PaperMC log stream with level filtering and error tracking).
- **Homepage** — one-glance status board across every homelab service, with a public Uptime Kuma status page.
- **Alert rules** — server-down, low/critical TPS, high heap (warning + critical), high system load, and no-players.
- **Predictive alerts** — memory-leak (`predict_linear` on heap), disk exhaustion, and TPS decline.
- **Log-based alerts** — high error rate, GC stress, chunk-load pressure, and player-disconnect spikes.
- **PVC backups** — daily zstd-compressed snapshots (3-day retention) at 04:00 CST with Discord notifications and a restore-verification script.

### Automated incident response

When Prometheus fires an alert, the incident-responder:

1. Forwards it to Discord as a rich embed.
2. Gathers live diagnostics from Prometheus (TPS, heap, load, players, uptime).
3. Pulls recent server logs from Loki.
4. Runs a MiniCPM5-2B (Ollama) root-cause analysis.
5. Posts alert + diagnostics + AI embeds together.

AI is **best-effort** — if Ollama is slow (CPU-bound, ~14 s median / ~18 s p95 for MiniCPM5-2B) or down, alert delivery is never blocked.

## Tech stack

| Layer | Tool | Role |
|-------|------|------|
| Runtime | **k3s** (v1.34) | Lightweight Kubernetes |
| Server | **PaperMC** 26.1.2 | Minecraft server (Java 25) |
| Metrics | **Prometheus** / kube-prometheus-stack | Collection + alerting |
| Dashboards | **Grafana** | 3 dashboards, 20 panels (server · player · logs) |
| Logs | **Loki + Promtail** | Aggregation (7-day retention) |
| Alerting | **AlertManager** | Discord webhook routing |
| GitOps | **Argo CD** | App-of-Apps deployment |
| Secrets | **External Secrets Operator** | Azure Key Vault backend |
| Ingress | **Traefik** | Sub-path routing + middlewares |
| AI | **Ollama + MiniCPM5-2B** | Local LLM (1.6 GB, Q4_K_M) |
| CI | **GitHub Actions** | YAML lint · Helm lint · kubeconform · Docker build |

## Engineering decisions

Selected problems worth writing down — the kind that don't show up until something is running.

<details open>
<summary><b>One TPS metric, everywhere</b></summary>

<br/>

Tick rate is the headline health signal, and it had been queried three different ways: a histogram (`minecraft_tps_bucket_*`) the exporter never emits, a legacy `forge_tps`, and the dashboard's `paper_tps_1m`. Dashboards, alerts, and the bot silently disagreed. The stack now reads TPS from **one source of truth — the Paper exporter's `paper_tps_1m` gauge —** used identically across Grafana, the PrometheusRules, the Discord bot, and the incident-responder. It's a gauge, not a counter, so no `rate()` gymnastics.
</details>

<details>
<summary><b>Player detection without a player metric</b></summary>

<br/>

PaperMC never emits a `minecraft_player_online` gauge. Online players are inferred from play-time counters that are still advancing:

```promql
increase(minecraft_play_time_ticks_total[5m]) > 0
```
</details>

<details>
<summary><b>AI integration: <code>/api/chat</code> over <code>/api/generate</code></b></summary>

<br/>

Ollama's `/api/generate` ignores chat templates — the previous phi3 responder model echoed the prompt and hallucinated numbers. Switching to `/api/chat` with a structured `messages` array engages the model's chat template and lets a system message set guardrails ("use only the data provided", "don't restate the prompt", "don't fabricate numbers") plus server context (PaperMC 26.1.2, 3 GB heap). MiniCPM5-2B also gets an explicit no-think template and `think:false` so its output budget is spent on the answer, not reasoning tokens.
</details>

<details>
<summary><b>Ollama memory: <code>num_ctx</code> must ride in the request body</b></summary>

<br/>

Ollama ignores the `OLLAMA_NUM_CTX` environment variable; the context size must be set per request (`"options": {"num_ctx": 2048}`). At the default 4096, phi3's KV cache plus model could exceed the original 3 GiB limit and get OOM-killed. The responder now pins 2048 and uses MiniCPM5-2B, which measured ~1.8 GB anonymous RSS on the 4 GiB pod; the 4 GiB limit is retained so phi3 remains available for the Discord bot and rollback.
</details>

<details>
<summary><b>Dual-path alerting: fast + enriched</b></summary>

<br/>

AlertManager fans each alert out to a direct Discord webhook (instant) **and** to the incident-responder (enriched). The responder processes asynchronously, so a slow Ollama call (observed ~14 s median for MiniCPM5-2B, ~25 s for phi3) never delays the first notification.
</details>

<details>
<summary><b>Incident responder model: MiniCPM5-2B Q4_K_M</b></summary>

<br/>

The responder uses `minicpm5-nothink`:

- MiniCPM5-2B Q4_K_M, 1.6 GB on disk, ~1.8 GB anonymous RSS during inference.
- `think:false`, `num_ctx=2048`, `num_predict=256`, `temperature=0.3`, `top_p=0.95`, `min_p=0.0`.
- `OLLAMA_STRUCTURED=true` sends a precomputed fact sheet with evidence IDs (`A1`–`A3`, `M1`–`M4`, `L1`–`Ln`) and requests JSON-only output. A deterministic validator rejects unsupported numbers, invalid evidence IDs, false missing-data claims, alert/consistency contradictions, and unsafe or dismissive actions; rejected output falls back to a grounded rule-based summary.
- `OLLAMA_MAX_LOADED_MODELS=1` prevents phi3 and MiniCPM5 from loading at the same time.
- Measured on the 4 GiB pod at temperature 0.3: 12/12 structured answers valid, ~14 s median and ~18 s p95 warm latency, ~1.9 GB anonymous RSS, no OOM kills; the phi3 baseline was ~25 s and ~4.0 GB anonymous RSS and still OOM-killed under sustained 12-request load at both 2048 and 1024 contexts.
- A `llama3.2:3b` Q4_K_M fallback was tested and rejected: it fit the pod without OOMs, but produced numeric contradictions (for example, calling TPS 8.2 “high” and reading 2040→2035 MB as an increase) and was slower than MiniCPM5.
- Q8_0 was tested and rejected: it used 2.9 GB anonymous RSS (versus 1.9 GB), produced 11/12 valid answers, and ran at ~20.5 s median / ~23.7 s p95. Q4_K_M is the final choice.
- Thinking mode is intentionally disabled. The structured no-think path meets the quality gate; enabling thinking would add latency and memory pressure inside the 4 GiB limit and would need a separate benchmark.
- AI analysis is advisory. Every AI embed carries an “AI-generated; verify before acting” footer, and unsafe recommendation sentences are removed before posting.

Provision after a fresh model volume:

```bash
kubectl -n ollama exec ollama-0 -- ollama pull hf.co/openbmb/MiniCPM5-2B-GGUF:Q4_K_M
kubectl -n ollama exec ollama-0 -- ollama cp hf.co/openbmb/MiniCPM5-2B-GGUF:Q4_K_M minicpm5-base
kubectl -n ollama exec -i ollama-0 -- sh -c 'cat > /tmp/Modelfile.minicpm5-nothink' <<'EOF'
FROM minicpm5-base

TEMPLATE """{{- if .Messages -}}
{{- range .Messages -}}
<|im_start|>{{ .Role }}
{{ .Content }}<|im_end|>
{{ end -}}
<|im_start|>assistant
<think>

</think>

{{ end -}}"""

PARAMETER stop "<|im_end|>"
PARAMETER stop "</s>"
PARAMETER temperature 0.3
PARAMETER top_p 0.95
PARAMETER min_p 0.0
PARAMETER num_ctx 2048
PARAMETER num_predict 256
EOF
kubectl -n ollama exec ollama-0 -- ollama create minicpm5-nothink -f /tmp/Modelfile.minicpm5-nothink
```

`phi3` intentionally remains installed for the Discord bot's general AI commands and as a rollback path. To roll back the responder, set `OLLAMA_MODEL=phi3` and `OLLAMA_STRUCTURED=false` (the legacy free-text prompt and sanitizer remain available), and remove `OLLAMA_THINK`.
</details>

<details>
<summary><b>Everything on a sub-path</b></summary>

<br/>

Grafana, Prometheus, and Loki are served under `/grafana`, `/prometheus`, `/loki` via Traefik, with matching `routePrefix` / `serve_from_sub_path` / `externalUrl` so deep links and asset loading work behind one host.
</details>

## Log aggregation

```
PaperMC logs → Promtail DaemonSet (/var/log/pods/default_*.log) → Loki (SingleBinary) → Grafana
```

Labelled `{app="minecraft"}`, with level parsing (INFO/WARN/ERROR/FATAL) and derived fields that link log lines back to Prometheus.

```logql
{app="minecraft"}                                  # all logs
{app="minecraft"} |= "ERROR"                       # errors only
count_over_time({app="minecraft"} |= "ERROR" [5m]) # error rate
```

## Repository structure

```
monitoring/
├── argocd/                 # Argo CD Applications (App-of-Apps)
├── discord-bot/            # Discord bot — Python / discord.py
│   ├── cogs/               #   status · admin · ai · help commands
│   ├── prometheus_client.py#   async Prometheus query client
│   └── rcon_client.py      #   RCON protocol client
├── incident-responder/     # Alert → diagnostics → AI → Discord webhook server
├── homelab-status/         # Stdlib-only unified status page (no deps)
├── helm/                   # kube-prometheus-stack Helm values
├── loki/                   # Loki + Promtail Helm values (incl. ruler for log alerts)
├── manifests/
│   ├── alerting/           #   PrometheusRules + AlertmanagerConfig + Loki ruler rules
│   ├── automation/         #   memory-restart CronJob + RBAC
│   ├── dashboards/         #   Grafana dashboard ConfigMaps
│   ├── datasources/        #   Grafana datasource ConfigMaps
│   ├── discord-bot/        #   Discord bot Deployment + quota
│   ├── eso/                #   ExternalSecrets + ClusterSecretStore
│   ├── exporters/          #   JMX exporter + ServiceMonitors
│   ├── health-checker/     #   Periodic service health CronJob
│   ├── homepage/           #   Homepage dashboard
│   ├── ingress/            #   Traefik ingress
│   ├── middleware/         #   Traefik middlewares (strip-prefix, rate-limit)
│   ├── minecraft/          #   PaperMC Deployment + Service (codified)
│   └── ollama/             #   Ollama StatefulSet
├── docs/                   # Assets for this README
├── infra/                  # Bicep IaC for Azure AKS (portfolio reference)
└── scripts/                # PVC backup / restore / verify (zstd)
```

## Quick start

<details>
<summary>Deploy · local dev · access · environment</summary>

<br/>

### Deploy

Everything is reconciled by Argo CD via App-of-Apps. Install the root app once; the rest follows from `main`.

```bash
git clone https://github.com/evince55/minecraft-monitoring.git
cd minecraft-monitoring
kubectl apply -f argocd/root-app.yaml   # bootstraps every other Application
```

Argo CD pulls this repo over **public HTTPS**, so no SSH deploy key or repository
credential is required. (Renaming the GitHub account previously invalidated the
SSH deploy key, which silently wedged every Application in `ComparisonError` /
`Unknown` sync state while the pods carried on running — HTTPS removes that
whole failure mode.)

**Prerequisites:** a k3s cluster (v1.21+), Helm v3, `kubectl`, and Traefik (bundled with k3s).

### Local development

```bash
kubeconform -strict -summary manifests/                       # validate manifests
docker build -t evince55/discord-bot:latest discord-bot/      # build images
docker build -t evince55/incident-responder:latest incident-responder/
```

### Access

| Service | Path |
|---------|------|
| Homepage | `/` |
| Grafana | `/grafana/` |
| Prometheus | `/prometheus/` |
| Loki | `/loki/` |
| Argo CD | `/argocd/` |
| Uptime Kuma | `/status/` |

Behind Traefik on the cluster host (`http://<node-ip>/…`, NodePort `30102` for direct access).

### Environment — Discord bot

| Variable | Description | Default |
|----------|-------------|---------|
| `DISCORD_TOKEN` | Bot token | *required* |
| `PROMETHEUS_URL` | Prometheus endpoint | `http://kube-prometheus-stack-prometheus.monitoring:9090/prometheus` |
| `OLLAMA_URL` | Ollama API endpoint | `http://ollama.ollama:11434` |
| `OLLAMA_MODEL` | LLM model name | `phi3` |
| `ADMIN_ROLE_ID` | Discord role for admin commands | — |

### Environment — incident responder

| Variable | Description | Default |
|----------|-------------|---------|
| `DISCORD_WEBHOOK_URL` | Discord webhook URL | *required* |
| `PROMETHEUS_URL` | Prometheus endpoint | `http://kube-prometheus-stack-prometheus.monitoring:9090/prometheus` |
| `LOKI_URL` | Loki gateway endpoint | `http://loki-gateway.monitoring:80` |
| `OLLAMA_URL` | Ollama endpoint (empty = skip AI) | `""` |
| `OLLAMA_MODEL` | LLM model name | `minicpm5-nothink` |
| `OLLAMA_THINK` | Send `think:false` to disable reasoning | `""` |
| `OLLAMA_STRUCTURED` | Use fact-sheet JSON output and validation | `true` |
| `OLLAMA_NUM_PREDICT` | Maximum generated tokens | `256` |
| `OLLAMA_TEMPERATURE` | Sampling temperature | `0.3` |
| `OLLAMA_TOP_P` | Nucleus sampling | `0.95` |
| `OLLAMA_MIN_P` | Minimum-probability sampling | `0.0` |
| `OLLAMA_SEED` | Sampling seed for reproducible output | `42` |

</details>

## License

[MIT](LICENSE)
