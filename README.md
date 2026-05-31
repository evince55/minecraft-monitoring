# Minecraft Monitoring Stack

This project is a self-hosted monitoring and management platform for a PaperMC Minecraft server, running entirely on a home k3s Kubernetes cluster. It provides full observability through Prometheus metrics, Grafana dashboards, and Loki log aggregation, with AlertManager routing critical alerts to Discord. A custom Discord bot enables remote server management — checking status, listing players, whitelisting users, and triggering backups — all without leaving the chat. The bot also integrates a local AI model (phi3 via Ollama) to analyze server lag and summarize recent logs on demand. Automated incident response enriches every alert with live diagnostics from Prometheus and Loki, plus AI-powered root-cause analysis, before posting to Discord. The entire stack is deployed via GitOps using ArgoCD, with secrets managed through External Secrets Operator backed by Azure Key Vault.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         k3s Cluster (192.168.1.192)                     │
│                                                                         │
│  ┌──────────────┐    JMX + RCON Exporters    ┌────────────────────┐     │
│  │  PaperMC      │ ─────────────────────────> │   Prometheus       │     │
│  │  1.21.4       │    metrics (TPS, heap,     │   + AlertManager   │     │
│  │  3GB heap     │    players, GC, JVM)       └────────┬───────────┘     │
│  └──────────────┘                                      │                 │
│       │                                          ┌─────┴──────┐         │
│       │ RCON                                     │            │         │
│       ▼                                          ▼            ▼         │
│  ┌──────────────┐                     ┌──────────────┐  ┌─────────┐    │
│  │ Discord Bot   │                     │   Grafana    │  │  Loki   │    │
│  │ (slash cmds)  │                     │  20-panel    │  │+Promtail│    │
│  │              │                     │  dashboard   │  └─────────┘    │
│  └──────┬───────┘                     └──────────────┘                  │
│         │                                                               │
│         │ queries                          AlertManager ─── Discord     │
│         ▼                                      │            Webhook     │
│  ┌──────────────┐                             ▼                         │
│  │  Prometheus  │<──── Incident Responder (diagnostics + AI) ──────>    │
│  └──────────────┘                      │                               │
│                                        ▼                               │
│                                 ┌──────────────┐                       │
│                                 │  Ollama       │                       │
│                                 │  phi3 (2.2GB) │                       │
│                                 └──────────────┘                       │
│                                                                         │
│  ┌────────────────────────────────────────────────────────────────┐     │
│  │  ArgoCD (App of Apps)     │  External Secrets Operator         │     │
│  │  - monitoring-root        │  - Azure Key Vault backend         │     │
│  │  - loki, promtail         │  - RCON password, webhook URL      │     │
│  │  - cert-manager           │                                    │     │
│  └────────────────────────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────────────────┘
```

## Features

### Discord Bot (10 slash commands)

| Command | Description |
|---------|-------------|
| `/status` | Live TPS, player count, heap usage, uptime |
| `/players` | List online players with session duration |
| `/tps` | Current server TPS |
| `/uptime` | Server uptime |
| `/whitelist` | Add/remove players from the whitelist (admin) |
| `/backup` | Trigger a world save and backup (admin) |
| `/ai-status` | Check if the Ollama AI model is online |
| `/analyze-lag` | AI-powered lag analysis from live Prometheus metrics |
| `/summarize` | AI summary of recent server logs from Loki |
| `/help` | List all available commands |

### Monitoring & Dashboards

- **20-panel Grafana dashboard** covering TPS, JVM heap, GC rates, CPU/load, player stats, movement, crafting, and health
- **7 alert rules**: MinecraftServerDown, LowTPS, CriticalTPS, HighHeapUsage, HighHeapUsageCritical, HighSystemLoad, NoPlayersOnline
- **PVC backup system**: Daily compressed backups (zstd, 3-day retention) at 4 AM CST with restore and verification scripts

### Automated Incident Response

When Prometheus fires an alert, the incident-responder automatically:

1. Forwards the alert to Discord as a rich embed
2. Gathers live diagnostics from Prometheus (TPS, heap, load, players, uptime)
3. Queries Loki for recent server logs
4. Runs AI analysis via Ollama phi3 for root-cause suggestions
5. Posts all three embeds (alert + diagnostics + AI) to Discord

The AI analysis is **best-effort** — if Ollama is unavailable or slow (CPU-bound, ~38s per query), alert delivery is never blocked.

## Tech Stack

| Component | Tool | Version | Role |
|-----------|------|---------|------|
| Runtime | k3s | v1.34.5 | Lightweight Kubernetes |
| Server | PaperMC | 1.21.4 | Minecraft server (Java 21) |
| Metrics | Prometheus + kube-prometheus-stack | — | Collection and alerting |
| Dashboard | Grafana | — | 20-panel Minecraft dashboard |
| Logs | Loki + Promtail | — | Log aggregation (7-day retention) |
| Alerting | AlertManager | — | Discord webhook routing |
| GitOps | ArgoCD | — | App of Apps deployment |
| Secrets | External Secrets Operator | — | Azure Key Vault backend |
| TLS | cert-manager | v1.20.2 | Let's Encrypt certificates |
| AI | Ollama + phi3 | — | Local LLM (2.2GB model) |
| CI/CD | GitHub Actions | — | YAML lint, Helm lint, kubeconform, Docker build |
| Registry | Docker Hub | evince55/ | Custom images |

## Key Engineering Decisions

### TPS Measurement: Direct Division, Not Cumulative Rate

PaperMC's JMX exporter exposes TPS as a histogram (`minecraft_tps_bucket_sum / minecraft_tps_bucket_count`). The exporter resets counters each scrape, making them gauge-like. Using `rate()` would produce meaningless results. Direct division (`sum / count`) gives accurate instantaneous TPS.

```
minecraft_tps_bucket_sum / minecraft_tps_bucket_count
```

### Player Detection: Workarounds for Missing Metrics

PaperMC never emits the standard `minecraft_player_online` metric. Instead, we detect online players by checking if `minecraft_play_time_ticks_total` has increased in the last 5 minutes:

```
increase(minecraft_play_time_ticks_total[5m]) > 0
```

Falls back to `vector(0)` when no players are online (avoids empty query results).

### AI Integration: `/api/chat` Over `/api/generate`

Ollama's `/api/generate` endpoint doesn't follow chat templates — phi3 echoes the raw prompt and hallucinates numbers. Switching to `/api/chat` with structured `messages` array leverages phi3's native chat template (`<|system|>...<|end|>`) and system messages with explicit guardrails:

- "Use ONLY the data provided"
- "Do not repeat or restate the prompt"
- "Do not fabricate or assume any numbers"

System messages include server context (PaperMC 1.21.4, 3GB heap) to reduce hallucination.

### Ollama Memory: `num_ctx` in Request Body

Ollama ignores the `OLLAMA_NUM_CTX` environment variable — the `num_ctx` parameter must be passed in every API request body (`"options": {"num_ctx": 2048}`). With the default `n_ctx=4096`, KV cache consumed ~1536 MiB, pushing total memory (2075 MiB model + 1536 MiB cache = 3611 MiB) over the 3Gi limit, causing OOM crashes with exit code -1. Reducing to 2048 resolved the issue.

### Dual-Path Alerting: Direct + Webhook

AlertManager sends to both a direct Discord webhook (immediate notification) and the incident-responder via webhook (enriched diagnostics). The incident-responder processes alerts asynchronously — even if Ollama takes 38 seconds on CPU, alert delivery is never blocked.

### Monitoring Sub-Paths

All services are accessible at sub-paths via Traefik (`/grafana`, `/prometheus`, `/loki`) with matching `routePrefix`, `serve_from_sub_path`, and `externalUrl` configuration across Grafana and Prometheus.

## Repository Structure

```
monitoring/
├── argocd/                    # ArgoCD Application manifests (App of Apps)
│   ├── root-app.yaml          # Root application
│   ├── monitoring-app.yaml    # kube-prometheus-stack
│   ├── loki-app.yaml          # Loki
│   ├── promtail-app.yaml      # Promtail
│   └── manifests-app.yaml     # Raw K8s manifests
├── discord-bot/               # Discord bot (Python, discord.py)
│   ├── bot.py                 # Bot setup, extension loading
│   ├── main.py                # Entry point, config
│   ├── config.py              # Environment config
│   ├── prometheus_client.py   # Async Prometheus query client
│   ├── rcon_client.py         # RCON protocol client
│   ├── cogs/
│   │   ├── status.py          # /status, /players, /tps, /uptime
│   │   ├── admin.py           # /whitelist, /backup (role-gated)
│   │   ├── ai.py              # /ai-status, /analyze-lag, /summarize
│   │   └── help.py            # /help
│   ├── Dockerfile
│   └── requirements.txt
├── incident-responder/        # Webhook server for Prometheus alerts
│   ├── main.py                # Alert → diagnostics → Discord embeds
│   └── Dockerfile
├── helm/
│   └── kube-prometheus-stack/ # Helm chart wrapper + values
├── infra/                     # Bicep IaC for Azure AKS (portfolio reference)
├── loki/                      # Loki + Promtail Helm values
├── manifests/
│   ├── alerting/              # PrometheusRules + AlertmanagerConfig
│   ├── cert-manager/          # ClusterIssuers
│   ├── dashboards/            # Grafana dashboard ConfigMaps
│   ├── datasources/           # Grafana datasource ConfigMaps
│   ├── discord-bot/           # K8s manifests for bot + incident-responder
│   ├── eso/                   # ExternalSecrets + ClusterSecretStore
│   ├── exporters/             # JMX + RCON exporter ServiceMonitors
│   ├── ingress/               # Traefik IngressRoutes
│   ├── middleware/             # Traefik middlewares (redirect, strip)
│   ├── ollama/                # Ollama StatefulSet + Service
│   └── traefik/               # Traefik config
└── scripts/
    ├── backup-pvcs.sh         # Daily PVC backup (zstd, 3-day retention)
    ├── restore-pvc.sh         # PVC restore
    └── verify-backup.sh       # Backup integrity verification
```

## Access

| Service | URL |
|---------|-----|
| Grafana | https://chai-homelab.com/grafana |
| Prometheus | https://chai-homelab.com/prometheus |
| Loki | https://chai-homelab.com/loki |
| ArgoCD | https://chai-homelab.com/argocd |

## Setup

### Prerequisites

- k3s cluster (v1.21+)
- Helm v3
- kubectl configured for the cluster
- Traefik ingress controller (included with k3s)

### Deployment

All components are managed by ArgoCD via the App of Apps pattern. Push to the `main` branch of the `chaitea321/minecraft-monitoring` repository, and ArgoCD auto-syncs.

```bash
# Clone the repository
git clone git@github.com:chaitea321/minecraft-monitoring.git
cd minecraft-monitoring

# Install ArgoCD root app (if not already installed)
kubectl apply -f argocd/root-app.yaml

# All other components deploy automatically via ArgoCD
```

### Local Development

```bash
# Lint YAML manifests
yamllint -c .yamllint.yaml manifests/

# Validate Kubernetes resources
kubeconform -strict -summary manifests/

# Build Docker images
docker build -t evince55/discord-bot:latest discord-bot/
docker build -t evince55/incident-responder:latest incident-responder/
```

## Environment Variables

### Discord Bot

| Variable | Description | Default |
|----------|-------------|---------|
| `DISCORD_TOKEN` | Bot token | Required |
| `PROMETHEUS_URL` | Prometheus endpoint | `http://kube-prometheus-stack-prometheus.monitoring:9090/prometheus` |
| `OLLAMA_URL` | Ollama API endpoint | `http://ollama.ollama:11434` |
| `OLLAMA_MODEL` | LLM model name | `phi3` |
| `ADMIN_ROLE_ID` | Discord role for admin commands | — |

### Incident Responder

| Variable | Description | Default |
|----------|-------------|---------|
| `DISCORD_WEBHOOK_URL` | Discord webhook URL | Required |
| `PROMETHEUS_URL` | Prometheus endpoint | `http://kube-prometheus-stack-prometheus.monitoring:9090/prometheus` |
| `LOKI_URL` | Loki gateway endpoint | `http://loki-gateway.monitoring:80` |
| `OLLAMA_URL` | Ollama endpoint (empty = skip AI) | `""` |
| `OLLAMA_MODEL` | LLM model name | `phi3` |

## License

MIT
