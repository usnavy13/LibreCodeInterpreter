#!/bin/bash
# Build and push Docker images to Azure Container Registry
# Usage: ./build-push.sh <acr-name> [api-tag] [executor-tag]
#
# Examples:
#   ./build-push.sh codeinterpdevxyz123
#   ./build-push.sh codeinterpdevxyz123 v1.0.0 v1.0.0

set -e

ACR_NAME="${1:?Error: ACR name is required as first argument}"
API_TAG="${2:-latest}"
EXECUTOR_TAG="${3:-latest}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${SCRIPT_DIR}/../.."
ACR_SERVER="${ACR_NAME}.azurecr.io"

echo "=== Building and Pushing Docker Images ==="
echo "ACR: $ACR_SERVER"
echo "API Tag: $API_TAG"
echo "Executor Tag: $EXECUTOR_TAG"
echo "Project Root: $PROJECT_ROOT"
echo ""

# Login to ACR
echo "Logging into Azure Container Registry..."
az acr login --name "$ACR_NAME"

# Build and push API image
echo ""
echo "=== Building API Image ==="
docker build \
    -t "${ACR_SERVER}/code-interpreter-api:${API_TAG}" \
    -f "${PROJECT_ROOT}/Dockerfile" \
    "${PROJECT_ROOT}"

echo ""
echo "=== Pushing API Image ==="
docker push "${ACR_SERVER}/code-interpreter-api:${API_TAG}"

# Build and push Executor image
echo ""
echo "=== Building Executor Image ==="
echo "Note: This image includes all 12 language runtimes and may take several minutes..."
docker build \
    -t "${ACR_SERVER}/code-interpreter-executor:${EXECUTOR_TAG}" \
    -f "${PROJECT_ROOT}/docker/multi-lang.Dockerfile" \
    "${PROJECT_ROOT}"

echo ""
echo "=== Pushing Executor Image ==="
docker push "${ACR_SERVER}/code-interpreter-executor:${EXECUTOR_TAG}"

echo ""
echo "=== Build Complete ==="
echo ""
echo "Images pushed:"
echo "  - ${ACR_SERVER}/code-interpreter-api:${API_TAG}"
echo "  - ${ACR_SERVER}/code-interpreter-executor:${EXECUTOR_TAG}"
echo ""
echo "To verify images in ACR:"
echo "  az acr repository list --name $ACR_NAME --output table"
echo "  az acr repository show-tags --name $ACR_NAME --repository code-interpreter-api --output table"
echo "  az acr repository show-tags --name $ACR_NAME --repository code-interpreter-executor --output table"
