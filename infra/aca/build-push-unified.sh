#!/bin/bash
# Build and push unified Docker image to Azure Container Registry
# Usage: ./build-push-unified.sh <acr-name> [image-tag]
#
# Examples:
#   ./build-push-unified.sh codeinterpdevxyz123
#   ./build-push-unified.sh codeinterpdevxyz123 v1.0.0

set -e

ACR_NAME="${1:?Error: ACR name is required as first argument}"
IMAGE_TAG="${2:-latest}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${SCRIPT_DIR}/../.."
ACR_SERVER="${ACR_NAME}.azurecr.io"

echo "=== Building and Pushing Unified Docker Image ==="
echo "ACR: $ACR_SERVER"
echo "Image Tag: $IMAGE_TAG"
echo "Project Root: $PROJECT_ROOT"
echo ""

# Login to ACR
echo "Logging into Azure Container Registry..."
az acr login --name "$ACR_NAME"

# Build unified image
echo ""
echo "=== Building Unified Image ==="
echo "Note: This image includes API + all 12 language runtimes and may take several minutes..."
docker build \
    -t "${ACR_SERVER}/code-interpreter-unified:${IMAGE_TAG}" \
    -f "${PROJECT_ROOT}/Dockerfile.unified" \
    "${PROJECT_ROOT}"

echo ""
echo "=== Pushing Unified Image ==="
docker push "${ACR_SERVER}/code-interpreter-unified:${IMAGE_TAG}"

echo ""
echo "=== Build Complete ==="
echo ""
echo "Image pushed:"
echo "  - ${ACR_SERVER}/code-interpreter-unified:${IMAGE_TAG}"
echo ""
echo "To verify image in ACR:"
echo "  az acr repository list --name $ACR_NAME --output table"
echo "  az acr repository show-tags --name $ACR_NAME --repository code-interpreter-unified --output table"
echo ""
echo "Image size (approximate):"
docker images "${ACR_SERVER}/code-interpreter-unified:${IMAGE_TAG}" --format "{{.Size}}"
