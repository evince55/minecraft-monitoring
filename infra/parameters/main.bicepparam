using '../main.bicep'

param location = 'westus3'
param resourceGroupName = 'rg-minecraft-cloud'
param clusterName = 'aks-minecraft'
param acrName = 'acrminecraft'
param keyVaultName = 'kv-minecraft-cloud'
param adminUsername = 'azureuser'

// Set your SSH public key here, or leave empty to auto-generate
param sshPublicKey = ''
