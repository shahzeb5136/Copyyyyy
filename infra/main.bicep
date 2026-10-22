// Dashboard Builder - Azure infrastructure (one environment).
//
// Creates: Log Analytics, Container Apps environment, Azure Container Registry, Storage account + blob
// container, a user-assigned managed identity (AcrPull + Storage Blob Data Contributor), the Container App
// itself (with the LLM key as a secret) and Entra ID built-in authentication (Easy Auth).
//
// Deploy (after creating the Entra app registration - see docs/DEPLOYMENT_GUIDE.md section 5):
//   az deployment group create -g <rg> -f infra/main.bicep -p infra/main.bicepparam
//
// The first deployment uses a public placeholder image so the app exists before the pipeline pushes the
// real image; the pipeline then updates the image on every build.

targetScope = 'resourceGroup'

@description('Short name prefix used for every resource, e.g. dashb')
@minLength(3)
@maxLength(12)
param namePrefix string

@description('Azure region, e.g. uaenorth')
param location string = resourceGroup().location

@description('Container image to run. Leave the default for the first deployment; the pipeline updates it.')
param containerImage string = 'mcr.microsoft.com/k8se/quickstart:latest'

@description('Entra ID tenant id used for sign-in')
param entraTenantId string = subscription().tenantId

@description('Application (client) id of the Entra app registration for Easy Auth')
param entraClientId string

@secure()
@description('Client secret of the Entra app registration')
param entraClientSecret string

@description('LLM provider: foundry | azure_openai | anthropic | openai | mock')
param llmProvider string = 'foundry'

@description('LLM endpoint, e.g. https://<resource>.services.ai.azure.com/anthropic/ (foundry) or https://<resource>.openai.azure.com/ (azure_openai)')
param llmBaseUrl string = ''

@description('Model id or deployment name')
param llmModel string = 'claude-opus-5-5'

@secure()
@description('LLM API key (stored as a Container App secret)')
param llmApiKey string = ''

@description('Hours after which uploaded files and jobs are deleted')
param retentionHours int = 72

@description('Minimum / maximum replicas')
param minReplicas int = 1
param maxReplicas int = 3

var suffix = uniqueString(resourceGroup().id)
var acrName = toLower('${namePrefix}acr${suffix}')
var storageName = toLower(take('${namePrefix}st${suffix}', 24))
var appName = '${namePrefix}-app'
var blobContainerName = 'dashboard-builder'

// ---------------------------------------------------------------------------------------------- logs
resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${namePrefix}-logs'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

// ---------------------------------------------------------------------------------------------- registry
resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}

// ---------------------------------------------------------------------------------------------- storage
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource blobContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: blobContainerName
  properties: { publicAccess: 'None' }
}

// Belt and braces: the app deletes old uploads itself, and the lifecycle rule removes anything it missed.
resource lifecycle 'Microsoft.Storage/storageAccounts/managementPolicies@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    policy: {
      rules: [
        {
          name: 'expire-uploads'
          enabled: true
          type: 'Lifecycle'
          definition: {
            filters: { blobTypes: [ 'blockBlob' ], prefixMatch: [ '${blobContainerName}/' ] }
            actions: { baseBlob: { delete: { daysAfterModificationGreaterThan: 7 } } }
          }
        }
      ]
    }
  }
}

// ---------------------------------------------------------------------------------------------- identity
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${namePrefix}-identity'
  location: location
}

var acrPullRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
var blobContributorRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, identity.id, acrPullRole)
  scope: acr
  properties: {
    roleDefinitionId: acrPullRole
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource blobAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, identity.id, blobContributorRole)
  scope: storage
  properties: {
    roleDefinitionId: blobContributorRole
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------------------------------------- container apps
resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${namePrefix}-env'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        { server: acr.properties.loginServer, identity: identity.id }
      ]
      secrets: [
        { name: 'llm-api-key', value: llmApiKey }
        { name: 'entra-client-secret', value: entraClientSecret }
      ]
    }
    template: {
      containers: [
        {
          name: 'app'
          image: containerImage
          resources: { cpu: json('1.0'), memory: '2Gi' }
          env: [
            { name: 'LLM_PROVIDER', value: llmProvider }
            { name: 'LLM_BASE_URL', value: llmBaseUrl }
            { name: 'LLM_MODEL', value: llmModel }
            { name: 'LLM_API_KEY', secretRef: 'llm-api-key' }
            { name: 'STORAGE_MODE', value: 'blob' }
            { name: 'BLOB_ACCOUNT_URL', value: storage.properties.primaryEndpoints.blob }
            { name: 'BLOB_CONTAINER', value: blobContainerName }
            { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId } // tells DefaultAzureCredential which identity to use
            { name: 'RETENTION_HOURS', value: string(retentionHours) }
            { name: 'REQUIRE_EASY_AUTH', value: 'true' }
            { name: 'MAX_UPLOAD_MB', value: '50' }
            { name: 'LOG_LEVEL', value: 'INFO' }
          ]
          probes: [
            { type: 'Liveness', httpGet: { path: '/api/health', port: 8000 }, initialDelaySeconds: 15, periodSeconds: 30 }
            { type: 'Readiness', httpGet: { path: '/api/health', port: 8000 }, initialDelaySeconds: 5, periodSeconds: 10 }
          ]
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
        rules: [
          { name: 'http', http: { metadata: { concurrentRequests: '40' } } }
        ]
      }
    }
  }
  dependsOn: [ acrPull ]
}

// Built-in authentication: every request must be signed in with Entra ID. No auth code in the app.
resource auth 'Microsoft.App/containerApps/authConfigs@2024-03-01' = {
  parent: app
  name: 'current'
  properties: {
    platform: { enabled: true }
    globalValidation: {
      unauthenticatedClientAction: 'RedirectToLoginPage'
      redirectToProvider: 'azureactivedirectory'
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          openIdIssuer: 'https://login.microsoftonline.com/${entraTenantId}/v2.0'
          clientId: entraClientId
          clientSecretSettingName: 'entra-client-secret'
        }
        validation: {
          allowedAudiences: [ 'api://${entraClientId}' ]
        }
      }
    }
    login: {
      tokenStore: { enabled: true }
    }
  }
}

output appUrl string = 'https://${app.properties.configuration.ingress.fqdn}'
output acrLoginServer string = acr.properties.loginServer
output storageAccount string = storage.name
output identityClientId string = identity.properties.clientId
output redirectUri string = 'https://${app.properties.configuration.ingress.fqdn}/.auth/login/aad/callback'
