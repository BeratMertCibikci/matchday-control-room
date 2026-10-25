targetScope = 'resourceGroup'

@description('Environment name (azd sets this)')
param environmentName string

@description('Location for all resources')
param location string = resourceGroup().location

@description('Provision a Microsoft Foundry resource, project and model deployment for the agent crew')
param deployFoundry bool = true

@description('LLM backend when deployFoundry = false: offline | foundry | azure_openai')
param llmBackend string = 'offline'

@description('Existing Foundry project endpoint (only used when deployFoundry = false and llmBackend = foundry)')
param foundryProjectEndpoint string = ''

@description('Model to deploy, also used as the deployment name')
param foundryModel string = 'gpt-4.1-mini'

@description('Model version')
param foundryModelVersion string = '2025-04-14'

@description('Deployment capacity in thousands of tokens per minute')
param foundryModelCapacity int = 10

var token = toLower(uniqueString(subscription().id, resourceGroup().id, environmentName))
var tags = { 'azd-env-name': environmentName }

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-${token}'
  location: location
  tags: tags
  properties: { sku: { name: 'PerGB2018' }, retentionInDays: 30 }
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: 'acr${token}'
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${token}'
  location: location
  tags: tags
}

// Let the app's identity pull images from the registry.
resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: registry
  name: guid(registry.id, identity.id, 'acrpull')
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
  }
}

resource insights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-${token}'
  location: location
  tags: tags
  kind: 'web'
  properties: { Application_Type: 'web', WorkspaceResourceId: logs.id }
}

// Microsoft Foundry: an AI Services resource with a project and a model deployment.
resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' = if (deployFoundry) {
  name: 'aif-${token}'
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    customSubDomainName: 'aif-${token}'
    allowProjectManagement: true
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource foundryProject 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = if (deployFoundry) {
  parent: foundry
  name: 'mcr'
  location: location
  identity: { type: 'SystemAssigned' }
  properties: { displayName: 'Matchday Control Room' }
}

resource modelDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = if (deployFoundry) {
  parent: foundry
  name: foundryModel
  sku: { name: 'GlobalStandard', capacity: foundryModelCapacity }
  properties: {
    model: { format: 'OpenAI', name: foundryModel, version: foundryModelVersion }
  }
}

// Let the app's identity call the project (Azure AI User), keyless.
resource aiUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployFoundry) {
  scope: foundry
  name: guid(foundry.id, identity.id, 'azure-ai-user')
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '53ca6127-db72-4b80-b1b0-d745d6d5456d')
  }
}

var projectEndpoint = deployFoundry
  ? 'https://aif-${token}.services.ai.azure.com/api/projects/mcr'
  : foundryProjectEndpoint

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-${token}'
  location: location
  tags: tags
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
  name: 'ca-web-${token}'
  location: location
  tags: union(tags, { 'azd-service-name': 'web' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      ingress: { external: true, targetPort: 8000, transport: 'auto' }
      registries: [ { server: registry.properties.loginServer, identity: identity.id } ]
    }
    template: {
      containers: [
        {
          name: 'web'
          // azd replaces this placeholder image on deploy
          image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'MCR_LLM', value: deployFoundry ? 'foundry' : llmBackend }
            { name: 'FOUNDRY_PROJECT_ENDPOINT', value: projectEndpoint }
            { name: 'FOUNDRY_MODEL', value: foundryModel }
            { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: insights.properties.ConnectionString }
          ]
        }
      ]
      scale: { minReplicas: 0, maxReplicas: 3, rules: [ { name: 'http', http: { metadata: { concurrentRequests: '30' } } } ] }
    }
  }
  dependsOn: [ acrPull, aiUser, modelDeployment, foundryProject ]
}

output AZURE_CONTAINER_REGISTRY_ENDPOINT string = registry.properties.loginServer
output SERVICE_WEB_URI string = 'https://${app.properties.configuration.ingress.fqdn}'
output APP_IDENTITY_PRINCIPAL_ID string = identity.properties.principalId
output FOUNDRY_PROJECT_ENDPOINT string = projectEndpoint
output APPLICATIONINSIGHTS_NAME string = insights.name
