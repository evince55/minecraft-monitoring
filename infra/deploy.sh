#!/usr/bin/env bash
set -euo pipefail

echo "==> Logging in to Azure..."
az login --output none

echo "==> Deploying AKS infrastructure with Bicep..."
az deployment sub create \
  --location westus3 \
  --parameters infra/parameters/main.bicepparam \
  --output table

echo "==> Getting AKS credentials..."
az aks get-credentials \
  --resource-group rg-minecraft-cloud \
  --name aks-minecraft \
  --overwrite-existing

echo "==> Deploying Helm stack to AKS..."
# TODO: Apply same Helm charts + ArgoCD setup to AKS
echo "Done."
