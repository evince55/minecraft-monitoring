targetScope = 'subscription'

@description('Azure region for all resources')
param location string = 'westus3'

@description('Name of the resource group')
param resourceGroupName string = 'rg-minecraft-cloud'

@description('AKS cluster name')
param clusterName string = 'aks-minecraft'

@description('ACR name (globally unique, lowercase alphanumeric)')
param acrName string = 'acrminecraft'

@description('Key Vault name (globally unique)')
param keyVaultName string = 'kv-minecraft-cloud'

@description('Admin username for AKS nodes')
param adminUsername string = 'azureuser'

@description('SSH public key for AKS nodes')
param sshPublicKey string = ''

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
}

module networking 'modules/networking.bicep' = {
  name: 'networking'
  scope: rg
  params: {
    location: location
    vnetName: 'vnet-minecraft'
    vnetAddressPrefix: '10.0.0.0/16'
    systemSubnetName: 'snet-system'
    systemSubnetPrefix: '10.0.1.0/24'
    userSubnetName: 'snet-user'
    userSubnetPrefix: '10.0.2.0/24'
  }
}

module acr 'modules/acr.bicep' = {
  name: 'acr'
  scope: rg
  params: {
    location: location
    acrName: acrName
  }
}

module keyvault 'modules/keyvault.bicep' = {
  name: 'keyvault'
  scope: rg
  params: {
    location: location
    keyVaultName: keyVaultName
  }
}

module aks 'modules/aks.bicep' = {
  name: 'aks'
  scope: rg
  params: {
    location: location
    clusterName: clusterName
    adminUsername: adminUsername
    sshPublicKey: sshPublicKey
    systemSubnetId: networking.outputs.systemSubnetId
    userSubnetId: networking.outputs.userSubnetId
    acrName: acr.outputs.acrName
  }
}

output resourceGroupName string = resourceGroupName
output aksClusterName string = clusterName
output acrLoginServer string = acr.outputs.loginServer
output keyVaultUri string = keyvault.outputs.vaultUri
