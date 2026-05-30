param location string
param clusterName string
param adminUsername string
param sshPublicKey string
param systemSubnetId string
param userSubnetId string
param acrName string

resource aks 'Microsoft.ContainerService/managedClusters@2024-02-01' = {
  name: clusterName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    kubernetesVersion: ''
    dnsPrefix: uniqueString(resourceGroup().id)
    linuxProfile: sshPublicKey != '' ? {
      adminUsername: adminUsername
      ssh: {
        publicKeys: [
          {
            keyData: sshPublicKey
          }
        ]
      }
    } : null
    agentPoolProfiles: [
      {
        name: 'systempool'
        mode: 'System'
        type: 'VirtualMachineScaleSets'
        vmSize: 'Standard_DS2_v2'
        osDiskSizeGB: 128
        count: 1
        minCount: 1
        maxCount: 3
        enableAutoScaling: true
        vnetSubnetID: systemSubnetId
      }
      {
        name: 'userpool'
        mode: 'User'
        type: 'VirtualMachineScaleSets'
        vmSize: 'Standard_DS2_v2'
        osDiskSizeGB: 128
        count: 0
        minCount: 0
        maxCount: 3
        enableAutoScaling: true
        vnetSubnetID: userSubnetId
      }
    ]
    networkProfile: {
      networkPlugin: 'azure'
      networkPolicy: 'calico'
      serviceCidr: '10.96.0.0/12'
      dnsServiceIP: '10.96.0.10'
    }
    apiServerAccessProfile: {
      enablePrivateCluster: false
    }
    aadProfile: {
      managed: true
      enableAzureRBAC: true
    }
  }
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: acrName
}

resource acrPullAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: acr
  name: guid(aks.id, acr.name, 'acrpull')
  properties: {
    principalId: aks.identity.principalId
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '7f951dda-4ed3-4680-a7ca-43fe172d538d'
    )
    principalType: 'ServicePrincipal'
  }
}

output clusterName string = aks.name
output clusterId string = aks.id
