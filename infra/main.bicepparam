// Example parameter file. Copy, fill in, and never commit real secrets (pass them with --parameters on the CLI
// or from Key Vault references instead).
using './main.bicep'

param namePrefix = 'dashb'
param location = 'uaenorth'
param entraClientId = '<application (client) id of the Entra app registration>'
param entraClientSecret = '<client secret>'           // better: pass on the CLI: -p entraClientSecret=$SECRET
param llmProvider = 'foundry'
param llmBaseUrl = 'https://<your-foundry-resource>.services.ai.azure.com/anthropic/'
param llmModel = 'claude-opus-5-5'
param llmApiKey = '<foundry api key>'                 // better: pass on the CLI: -p llmApiKey=$LLM_KEY
param retentionHours = 72
