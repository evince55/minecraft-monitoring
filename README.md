# Minecraft Monitoring Stack

Homelab Kubernetes monitoring stack: Prometheus, Grafana, Loki, AlertManager, and Minecraft server metrics running on k3s.

## Architecture

| Component | Purpose |
|-----------|---------|
| Prometheus | Metrics collection (TPS, players, JVM heap) |
| Grafana | Dashboards and visualization |
| Loki + Promtail | Log aggregation |
| AlertManager | Alert routing to Discord |
| cert-manager | TLS certificate management (WIP) |
| ArgoCD | GitOps deployment |

## Repository Structure

```
helm/              # Helm chart wrappers with values
  kube-prometheus-stack/
loki/              # Loki + Promtail Helm configs
manifests/         # Raw Kubernetes manifests
  ingress/         # Traefik ingress routes
  middleware/      # Traefik middlewares
  alerting/        # PrometheusRule + AlertmanagerConfig
  dashboards/      # Grafana dashboard ConfigMaps
  datasources/     # Grafana datasource ConfigMaps
  exporters/       # JMX + RCON exporter configs
argocd/            # ArgoCD Application manifests
scripts/           # PVC backup/restore shell scripts
```

## Access

- Grafana: http://192.168.1.192/grafana
- Prometheus: http://192.168.1.192/prometheus
- Loki: http://192.168.1.192/loki
- ArgoCD: http://192.168.1.192/argocd

## Backup

PVC backups run daily at 4:00 AM CST via host cron.
