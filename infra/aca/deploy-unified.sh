#!/bin/bash
# Deploy Code Interpreter to Azure Container Apps (Unified Single-Container Architecture)
# Usage: ./deploy-unified.sh <environment> [resource-group] [location]
#
# Examples:
#   ./deploy-unified.sh dev
#   ./deploy-unified.sh dev code-interpreter-unified-dev eastus
#
# Environment variables:
#   API_KEY       - API key for authentication (generated if not set)
#   MASTER_API_KEY - Master key for admin panel (generated if not set)

set -e

ENVIRONMENT="${1:-dev}"
RESOURCE_GROUP="${2:-code-interpreter-unified-${ENVIRONMENT}}"
LOCATION="${3:-eastus}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Code Interpreter ACA Deployment (Unified Architecture) ==="
echo "Environment: $ENVIRONMENT"
echo "Resource Group: $RESOURCE_GROUP"
echo "Location: $LOCATION"
echo ""
echo "Architecture: Single Container (API + Executor combined)"
echo "Execution: Serialized (1 request per replica)"
echo ""

# Check if Azure CLI is logged in
if ! az account show &>/dev/null; then
    echo "Error: Not logged into Azure CLI. Run 'az login' first."
    exit 1
fi

# Get current subscription
SUBSCRIPTION=$(az account show --query name -o tsv)
SUBSCRIPTION_ID=$(az account show --query id -o tsv)
echo "Subscription: $SUBSCRIPTION"
echo "Subscription ID: $SUBSCRIPTION_ID"
echo ""

# Generate API keys if not provided
if [[ -z "$API_KEY" ]]; then
    API_KEY="$(openssl rand -base64 32 | tr -d '/+=' | cut -c1-32)"
    echo "Generated API_KEY: ${API_KEY:0:8}..."
fi

if [[ -z "$MASTER_API_KEY" ]]; then
    MASTER_API_KEY="$(openssl rand -base64 32 | tr -d '/+=' | cut -c1-32)"
    echo "Generated MASTER_API_KEY: ${MASTER_API_KEY:0:8}..."
fi
echo ""

# Confirm deployment
read -p "Continue with deployment? [y/N] " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Deployment cancelled."
    exit 0
fi

# Create resource group if it doesn't exist
echo ""
echo "Creating resource group..."
az group create \
    --name "$RESOURCE_GROUP" \
    --location "$LOCATION" \
    --output none

# Check for parameter file
PARAMS_FILE="${SCRIPT_DIR}/parameters/${ENVIRONMENT}-unified.json"
if [[ ! -f "$PARAMS_FILE" ]]; then
    echo "Warning: Parameter file not found: $PARAMS_FILE"
    echo "Using dev-unified.json as fallback..."
    PARAMS_FILE="${SCRIPT_DIR}/parameters/dev-unified.json"
fi

# Deploy Bicep template
echo "Deploying Bicep template (unified architecture)..."
DEPLOYMENT_NAME="code-interpreter-unified-$(date +%Y%m%d-%H%M%S)"

az deployment group create \
    --name "$DEPLOYMENT_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --template-file "${SCRIPT_DIR}/main-unified.bicep" \
    --parameters "@${PARAMS_FILE}" \
    --parameters apiKeys="$API_KEY" \
    --parameters masterApiKey="$MASTER_API_KEY" \
    --output table

# Get deployment outputs
echo ""
echo "=== Deployment Outputs ==="
az deployment group show \
    --name "$DEPLOYMENT_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --query properties.outputs \
    --output table

# Get API URL and ACR name
API_URL=$(az deployment group show \
    --name "$DEPLOYMENT_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --query properties.outputs.apiUrl.value \
    --output tsv)

ACR_NAME=$(az deployment group show \
    --name "$DEPLOYMENT_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --query properties.outputs.acrName.value \
    --output tsv)

# Save credentials to local file
CREDS_FILE="${SCRIPT_DIR}/.credentials.${ENVIRONMENT}-unified"
cat > "$CREDS_FILE" << EOF
# Code Interpreter ACA Credentials - ${ENVIRONMENT} (Unified)
# Generated: $(date -Iseconds)
# Resource Group: ${RESOURCE_GROUP}
# Architecture: Single Container (Unified)

API_KEY=${API_KEY}
MASTER_API_KEY=${MASTER_API_KEY}
API_URL=${API_URL}
ACR_NAME=${ACR_NAME}
DEPLOYMENT_NAME=${DEPLOYMENT_NAME}
EOF
chmod 600 "$CREDS_FILE"

echo ""
echo "=== Deployment Complete ==="
echo "API URL: $API_URL"
echo "ACR Name: $ACR_NAME"
echo ""
echo "Credentials saved to: $CREDS_FILE"
echo ""
echo "=== Next Steps ==="
echo ""
echo "1. Build and push the unified Docker image:"
echo "   ./build-push-unified.sh $ACR_NAME"
echo ""
echo "2. After the image is pushed, update the container app to use the new image:"
echo "   az containerapp update -n \$(az containerapp list -g $RESOURCE_GROUP --query '[0].name' -o tsv) -g $RESOURCE_GROUP --image ${ACR_NAME}.azurecr.io/code-interpreter-unified:latest"
echo ""
echo "3. Test the deployment:"
echo "   source $CREDS_FILE"
echo "   curl \$API_URL/health"
echo "   curl -X POST \$API_URL/exec -H \"x-api-key: \$API_KEY\" -H \"Content-Type: application/json\" -d '{\"code\":\"print(42)\",\"language\":\"py\"}'"
echo ""
echo "4. View logs:"
echo "   az containerapp logs show --name \$(az containerapp list -g $RESOURCE_GROUP --query '[0].name' -o tsv) -g $RESOURCE_GROUP --follow"
echo ""
echo "5. Check scaling:"
echo "   az containerapp replica list --name \$(az containerapp list -g $RESOURCE_GROUP --query '[0].name' -o tsv) -g $RESOURCE_GROUP -o table"
