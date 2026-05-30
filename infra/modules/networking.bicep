param location string
param vnetName string
param vnetAddressPrefix string
param systemSubnetName string
param systemSubnetPrefix string
param userSubnetName string
param userSubnetPrefix string

resource vnet 'Microsoft.Network/virtualNetworks@2024-01-01' = {
  name: vnetName
  location: location
  properties: {
    addressSpace: {
      addressPrefixes: [vnetAddressPrefix]
    }
    subnets: [
      {
        name: systemSubnetName
        properties: {
          addressPrefix: systemSubnetPrefix
        }
      }
      {
        name: userSubnetName
        properties: {
          addressPrefix: userSubnetPrefix
        }
      }
    ]
  }
}

output systemSubnetId string = vnet.properties.subnets[0].id
output userSubnetId string = vnet.properties.subnets[1].id
output vnetId string = vnet.id
