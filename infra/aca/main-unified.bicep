// Azure Container Apps deployment for Code Interpreter API (Unified Single-Container)
// This template deploys:
// - Azure Container Apps Environment
// - Unified Container App (API + Executor in one container)
// - Azure Cache for Redis
// - Azure Blob Storage
// - Azure Container Registry
//
// Key differences from 2-container architecture:
// - Single container with inline execution (no internal HTTP calls)
// - Serialized execution (1 request per replica at a time)
// - ACA-managed scaling based on concurrent requests

@description('Name prefix for all resources')
param namePrefix string = 'codeinterp'

@description('Azure region for all resources')
param location string = resourceGroup().location

@description('Environment type (dev, staging, prod)')
@allowed(['dev', 'staging', 'prod'])
param environment string = 'dev'

@description('Unified container image tag')
param imageTag string = 'latest'

@description('Use placeholder images for initial deployment (before ACR images are built)')
param usePlaceholderImages bool = true

@description('Existing ACR name (if ACR was created separately before this deployment)')
param existingAcrName string = ''

@description('User-Assigned Managed Identity resource ID (pre-created with AcrPull role)')
param managedIdentityId string = ''

// Use the provided managed identity if available
var useManagedIdentity = !empty(managedIdentityId)

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

@description('Minimum replicas (baseline capacity)')
param minReplicas int = 3

@description('Maximum replicas')
param maxReplicas int = 30

@description('Enable state archival to blob storage')
param enableStateArchival bool = true

// Generate unique suffix for resources (use shorter suffix for name-length-limited resources)
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

// Unified Container App (API + Executor in single container)
resource unifiedContainerApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: '${resourceName}-unified'
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
          identity: useManagedIdentity ? managedIdentityId : 'system'
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'unified'
          image: usePlaceholderImages ? placeholderImage : '${acrLoginServer}/code-interpreter-unified:${imageTag}'
          resources: {
            // More resources since this container handles both API and execution
            cpu: json('1.0')
            memory: '2Gi'
          }
          env: [
            {
              name: 'DEPLOYMENT_MODE'
              value: 'azure'
            }
            {
              name: 'UNIFIED_MODE'
              value: 'true'
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
              name: 'STATE_ARCHIVE_ENABLED'
              value: string(enableStateArchival)
            }
            {
              name: 'MAX_CONCURRENT_EXECUTIONS'
              value: '1'  // Serialized execution for isolation
            }
            {
              name: 'WORKING_DIR_BASE'
              value: '/mnt/data'
            }
          ]
        }
      ]
      scale: {
        // With serialized execution (1 request per replica), scale based on demand
        // 3 replicas minimum for baseline capacity
        minReplicas: minReplicas
        maxReplicas: maxReplicas
        rules: [
          {
            name: 'http-serialized'
            http: {
              metadata: {
                // 5 concurrent requests per replica before scaling
                // Balances isolation with reasonable scaling behavior
                concurrentRequests: '5'
              }
            }
          }
        ]
      }
    }
  }
  identity: useManagedIdentity ? {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentityId}': {}
    }
  } : {
    type: 'SystemAssigned'
  }
}

// Outputs
output apiUrl string = 'https://${unifiedContainerApp.properties.configuration.ingress.fqdn}'
output redisHostname string = redis.properties.hostName
output storageAccountName string = storageAccount.name
output containerAppsEnvironmentId string = containerAppsEnv.id
output acrLoginServer string = acrLoginServer
output acrName string = acrName
