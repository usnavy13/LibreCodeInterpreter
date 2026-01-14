// Azure Container Apps deployment for Code Interpreter API
// This template deploys:
// - Azure Container Apps Environment
// - API Container App (external ingress)
// - Executor Container App (internal ingress, warm pool)
// - Azure Cache for Redis
// - Azure Blob Storage
// - Azure Container Registry

@description('Name prefix for all resources')
param namePrefix string = 'codeinterp'

@description('Azure region for all resources')
param location string = resourceGroup().location

@description('Environment type (dev, staging, prod)')
@allowed(['dev', 'staging', 'prod'])
param environment string = 'dev'

@description('API container image tag')
param apiImageTag string = 'latest'

@description('Executor container image tag')
param executorImageTag string = 'latest'

@description('Use placeholder images for initial deployment (before ACR images are built)')
param usePlaceholderImages bool = true

@description('Existing ACR name (if ACR was created separately before this deployment)')
param existingAcrName string = ''

// Placeholder image that works on Container Apps (used for initial deployment)
var placeholderImage = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

// Use existing ACR if provided, otherwise create new one
var useExistingAcr = !empty(existingAcrName)

@description('API keys (comma-separated)')
@secure()
param apiKeys string

@description('Master API key for admin panel access')
@secure()
param masterApiKey string

@description('Minimum API replicas')
param apiMinReplicas int = 1

@description('Maximum API replicas')
param apiMaxReplicas int = 10

@description('Minimum executor replicas (warm pool size)')
param executorMinReplicas int = 3

@description('Maximum executor replicas')
param executorMaxReplicas int = 20

@description('Enable state archival to blob storage')
param enableStateArchival bool = true

// Generate unique suffix for resources (use shorter suffix for name-length-limited resources)
// Container App names max 32 chars: namePrefix(10) + env(4) + suffix(6) + -executor(9) = 31
var uniqueSuffix = uniqueString(resourceGroup().id, namePrefix)
var shortSuffix = substring(uniqueSuffix, 0, 6)
var resourceName = '${namePrefix}-${environment}-${shortSuffix}'

// Log Analytics workspace for Container Apps
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: '${resourceName}-logs'
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

// Storage account for files and state archival (max 24 chars)
resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: '${namePrefix}${shortSuffix}'
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
  }
}

// Blob service and container
resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-01-01' = {
  parent: storageAccount
  name: 'default'
}

resource blobContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = {
  parent: blobService
  name: 'code-interpreter-files'
  properties: {
    publicAccess: 'None'
  }
}

// Azure Container Registry - use existing or create new
resource existingAcr 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = if (useExistingAcr) {
  name: existingAcrName
}

resource newAcr 'Microsoft.ContainerRegistry/registries@2023-07-01' = if (!useExistingAcr) {
  name: '${namePrefix}${shortSuffix}acr'
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled'
  }
}

// Reference to the ACR (either existing or new)
var acrName = useExistingAcr ? existingAcrName : newAcr.name
var acrLoginServer = useExistingAcr ? existingAcr.properties.loginServer : newAcr.properties.loginServer

// Azure Cache for Redis
resource redis 'Microsoft.Cache/redis@2023-08-01' = {
  name: '${resourceName}-redis'
  location: location
  properties: {
    sku: {
      name: environment == 'prod' ? 'Standard' : 'Basic'
      family: 'C'
      capacity: environment == 'prod' ? 1 : 0
    }
    enableNonSslPort: false
    minimumTlsVersion: '1.2'
    redisConfiguration: {
      'maxmemory-policy': 'volatile-lru'
    }
  }
}

// Container Apps Environment
resource containerAppsEnv 'Microsoft.App/managedEnvironments@2023-05-01' = {
  name: '${resourceName}-env'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

// API Container App
resource apiContainerApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: '${resourceName}-api'
  location: location
  properties: {
    managedEnvironmentId: containerAppsEnv.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
      secrets: [
        {
          name: 'api-keys'
          value: apiKeys
        }
        {
          name: 'master-api-key'
          value: masterApiKey
        }
        {
          name: 'redis-connection'
          value: '${redis.properties.hostName}:${redis.properties.sslPort},password=${redis.listKeys().primaryKey},ssl=true,abortConnect=false'
        }
        {
          name: 'storage-connection'
          value: 'DefaultEndpointsProtocol=https;AccountName=${storageAccount.name};AccountKey=${storageAccount.listKeys().keys[0].value};EndpointSuffix=core.windows.net'
        }
      ]
      registries: usePlaceholderImages ? [] : [
        {
          server: acrLoginServer
          identity: 'system'
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'api'
          image: usePlaceholderImages ? placeholderImage : '${acrLoginServer}/code-interpreter-api:${apiImageTag}'
          resources: {
            cpu: json('1.0')
            memory: '2Gi'
          }
          env: [
            {
              name: 'DEPLOYMENT_MODE'
              value: 'azure'
            }
            {
              name: 'API_KEY'
              secretRef: 'api-keys'
            }
            {
              name: 'API_KEYS'
              secretRef: 'api-keys'
            }
            {
              name: 'MASTER_API_KEY'
              secretRef: 'master-api-key'
            }
            {
              name: 'AZURE_REDIS_CONNECTION_STRING'
              secretRef: 'redis-connection'
            }
            {
              name: 'AZURE_STORAGE_CONNECTION_STRING'
              secretRef: 'storage-connection'
            }
            {
              name: 'AZURE_STORAGE_CONTAINER'
              value: 'code-interpreter-files'
            }
            {
              name: 'EXECUTOR_URL'
              value: 'http://${resourceName}-executor'
            }
            {
              name: 'STATE_ARCHIVE_ENABLED'
              value: string(enableStateArchival)
            }
          ]
        }
      ]
      scale: {
        minReplicas: apiMinReplicas
        maxReplicas: apiMaxReplicas
        rules: [
          {
            name: 'http-rule'
            http: {
              metadata: {
                concurrentRequests: '100'
              }
            }
          }
        ]
      }
    }
  }
  identity: {
    type: 'SystemAssigned'
  }
}

// Executor Container App (internal ingress only)
resource executorContainerApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: '${resourceName}-executor'
  location: location
  properties: {
    managedEnvironmentId: containerAppsEnv.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: false  // Internal only
        targetPort: 8001
        transport: 'auto'
      }
      registries: usePlaceholderImages ? [] : [
        {
          server: acrLoginServer
          identity: 'system'
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'executor'
          image: usePlaceholderImages ? placeholderImage : '${acrLoginServer}/code-interpreter-executor:${executorImageTag}'
          resources: {
            cpu: json('2.0')
            memory: '4Gi'
          }
          env: [
            {
              name: 'DEPLOYMENT_MODE'
              value: 'azure'
            }
            {
              name: 'EXECUTOR_PORT'
              value: '8001'
            }
            {
              name: 'MAX_CONCURRENT_EXECUTIONS'
              value: '4'
            }
            {
              name: 'WORKING_DIR_BASE'
              value: '/mnt/data'
            }
          ]
        }
      ]
      scale: {
        minReplicas: executorMinReplicas  // Warm pool
        maxReplicas: executorMaxReplicas
        rules: [
          {
            name: 'http-rule'
            http: {
              metadata: {
                concurrentRequests: '10'  // Scale up when busy
              }
            }
          }
        ]
      }
    }
  }
  identity: {
    type: 'SystemAssigned'
  }
}

// Role assignment for API container app to pull from ACR (only if using new ACR)
resource apiAcrPullRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!useExistingAcr) {
  name: guid(newAcr.id, apiContainerApp.id, 'acrpull')
  scope: newAcr
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
    principalId: apiContainerApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Role assignment for API container app to pull from existing ACR
resource apiAcrPullRoleExisting 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (useExistingAcr) {
  name: guid(existingAcr.id, apiContainerApp.id, 'acrpull')
  scope: existingAcr
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
    principalId: apiContainerApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Role assignment for Executor container app to pull from ACR (only if using new ACR)
resource executorAcrPullRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!useExistingAcr) {
  name: guid(newAcr.id, executorContainerApp.id, 'acrpull')
  scope: newAcr
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
    principalId: executorContainerApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Role assignment for Executor container app to pull from existing ACR
resource executorAcrPullRoleExisting 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (useExistingAcr) {
  name: guid(existingAcr.id, executorContainerApp.id, 'acrpull')
  scope: existingAcr
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
    principalId: executorContainerApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Outputs
output apiUrl string = 'https://${apiContainerApp.properties.configuration.ingress.fqdn}'
output executorInternalUrl string = 'http://${executorContainerApp.name}'
output redisHostname string = redis.properties.hostName
output storageAccountName string = storageAccount.name
output containerAppsEnvironmentId string = containerAppsEnv.id
output acrLoginServer string = acrLoginServer
output acrName string = acrName
