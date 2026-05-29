import os

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]

PROMETHEUS_URL = os.environ.get(
    "PROMETHEUS_URL",
    "http://kube-prometheus-stack-prometheus.monitoring:9090/prometheus",
)
LOKI_URL = os.environ.get("LOKI_URL", "http://loki-gateway.monitoring:80")

RCON_HOST = os.environ.get("RCON_HOST", "minecraft-rcon.default")
RCON_PORT = int(os.environ.get("RCON_PORT", "25575"))
RCON_PASSWORD = os.environ["RCON_PASSWORD"]

ADMIN_ROLE_ID = int(os.environ.get("ADMIN_ROLE_ID", "0"))
