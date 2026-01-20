#!/bin/bash
# setup.sh - Unified Azure Container Apps deployment for Code Interpreter API
#
# Usage: ./setup.sh [OPTIONS]
#
# This script automates the complete deployment of Code Interpreter API to Azure Container Apps,
# using the UNIFIED single-container architecture (API + Executor combined).
#
# Key features:
# - Single container with inline execution (no HTTP between containers)
# - Serialized execution (1 request per replica) for isolation
# - ACA-managed scaling based on concurrent requests
#
# See --help for all available options.

set -euo pipefail

# =============================================================================
# SECTION 1: Configuration & Constants
# =============================================================================

# Script metadata
readonly SCRIPT_VERSION="1.0.0"
readonly SCRIPT_NAME="$(basename "${BASH_SOURCE[0]}")"
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly PROJECT_ROOT="${SCRIPT_DIR}/../.."
readonly LOG_FILE="${SCRIPT_DIR}/setup-$(date +%Y%m%d-%H%M%S).log"

# Default configuration
ENVIRONMENT="dev"
RESOURCE_GROUP=""
LOCATION="eastus"
SKIP_BUILD=false
SKIP_DEPLOY=false
UNINSTALL=false
NON_INTERACTIVE=false
VERBOSE=false
DRY_RUN=false
API_KEY=""
MASTER_API_KEY=""

# Deployment state (populated during execution)
DEPLOYMENT_NAME=""
API_URL=""
ACR_NAME=""
ACR_SERVER=""
MANAGED_IDENTITY_NAME=""
MANAGED_IDENTITY_ID=""
MANAGED_IDENTITY_PRINCIPAL_ID=""

# Required tool versions
readonly MIN_AZ_CLI_VERSION="2.50.0"

# Azure Container Apps extension
readonly ACA_EXTENSION="containerapp"

# Build timeouts (seconds)
readonly UNIFIED_BUILD_TIMEOUT=3600  # 60 minutes for unified image (API + all 12 language runtimes)

# Health check settings
readonly HEALTH_CHECK_MAX_ATTEMPTS=30
readonly HEALTH_CHECK_INTERVAL=10

# Colors for output
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly CYAN='\033[0;36m'
readonly MAGENTA='\033[0;35m'
readonly BOLD='\033[1m'
readonly NC='\033[0m' # No Color

# =============================================================================
# SECTION 2: Utility Functions
# =============================================================================

# Initialize log file
init_log() {
    mkdir -p "$(dirname "${LOG_FILE}")"
    cat > "${LOG_FILE}" << EOF
=== Code Interpreter Azure Setup ===
Script Version: ${SCRIPT_VERSION}
Started: $(date -Iseconds)
Arguments: $*
Working Directory: $(pwd)
Project Root: ${PROJECT_ROOT}

EOF
}

# Logging functions
log_info() {
    local msg="[INFO] $1"
    echo -e "${BLUE}${msg}${NC}"
    echo "[$(date -Iseconds)] ${msg}" >> "${LOG_FILE}"
}

log_success() {
    local msg="[SUCCESS] $1"
    echo -e "${GREEN}${msg}${NC}"
    echo "[$(date -Iseconds)] ${msg}" >> "${LOG_FILE}"
}

log_warning() {
    local msg="[WARNING] $1"
    echo -e "${YELLOW}${msg}${NC}"
    echo "[$(date -Iseconds)] ${msg}" >> "${LOG_FILE}"
}

log_error() {
    local msg="[ERROR] $1"
    echo -e "${RED}${msg}${NC}" >&2
    echo "[$(date -Iseconds)] ${msg}" >> "${LOG_FILE}"
}

log_debug() {
    if [[ "${VERBOSE}" == "true" ]]; then
        local msg="[DEBUG] $1"
        echo -e "${CYAN}${msg}${NC}"
        echo "[$(date -Iseconds)] ${msg}" >> "${LOG_FILE}"
    fi
}

log_phase() {
    local phase_num=$1
    local phase_name=$2
    echo ""
    echo -e "${MAGENTA}${BOLD}======================================${NC}"
    echo -e "${MAGENTA}${BOLD}  Phase ${phase_num}: ${phase_name}${NC}"
    echo -e "${MAGENTA}${BOLD}======================================${NC}"
    echo ""
    echo "" >> "${LOG_FILE}"
    echo "=== Phase ${phase_num}: ${phase_name} ===" >> "${LOG_FILE}"
}

# Progress spinner for long-running operations
spinner() {
    local pid=$1
    local delay=0.1
    local spinstr='|/-\'
    while kill -0 "$pid" 2>/dev/null; do
        local temp=${spinstr#?}
        printf " [%c]  " "$spinstr"
        local spinstr=$temp${spinstr%"$temp"}
        sleep $delay
        printf "\b\b\b\b\b\b"
    done
    printf "      \b\b\b\b\b\b"
}

# Stream command output showing only last N lines (refreshing in place)
# Usage: some_command 2>&1 | stream_tail_output 5 "Building image"
stream_tail_output() {
    local num_lines=${1:-5}
    local title=${2:-""}
    local line_buffer=()
    local total_lines=0

    # Hide cursor
    tput civis 2>/dev/null || true

    # Save cursor position and print title
    if [[ -n "${title}" ]]; then
        echo -e "${BLUE}${title}${NC}"
    fi

    # Print placeholder lines
    for ((i=0; i<num_lines; i++)); do
        echo ""
    done

    while IFS= read -r line; do
        # Log to file
        echo "${line}" >> "${LOG_FILE}"

        # Update buffer (keep last N lines)
        line_buffer+=("${line}")
        if [[ ${#line_buffer[@]} -gt ${num_lines} ]]; then
            line_buffer=("${line_buffer[@]:1}")
        fi
        ((total_lines++))

        # Move cursor up and clear lines
        tput cuu ${num_lines} 2>/dev/null || printf "\033[${num_lines}A"

        # Print the buffer
        for ((i=0; i<num_lines; i++)); do
            tput el 2>/dev/null || printf "\033[K"  # Clear line
            if [[ $i -lt ${#line_buffer[@]} ]]; then
                # Truncate line to terminal width
                local term_width
                term_width=$(tput cols 2>/dev/null || echo 80)
                printf "  ${CYAN}%-$((term_width-4))s${NC}\n" "${line_buffer[$i]:0:$((term_width-4))}"
            else
                echo ""
            fi
        done
    done

    # Show cursor
    tput cnorm 2>/dev/null || true

    echo -e "  ${GREEN}Processed ${total_lines} lines${NC}"
}

# Show a progress bar with status
# Usage: show_progress current max "message"
show_progress() {
    local current=$1
    local max=$2
    local message=${3:-""}
    local width=40

    # Calculate percentage
    local percent=$((current * 100 / max))
    local filled=$((current * width / max))
    local empty=$((width - filled))

    # Build progress bar
    local bar=""
    for ((i=0; i<filled; i++)); do bar+="█"; done
    for ((i=0; i<empty; i++)); do bar+="░"; done

    # Calculate elapsed time
    local elapsed=""
    if [[ -n "${PROGRESS_START_TIME:-}" ]]; then
        local now=$(date +%s)
        local secs=$((now - PROGRESS_START_TIME))
        local mins=$((secs / 60))
        secs=$((secs % 60))
        elapsed=$(printf " (%dm %02ds)" $mins $secs)
    fi

    # Print progress (overwrite current line)
    printf "\r  ${CYAN}[${bar}]${NC} ${percent}%%${elapsed} ${message}"
}

# Clear progress line
clear_progress() {
    printf "\r\033[K"
}

# Confirm prompt
confirm() {
    local prompt=$1
    local default=${2:-N}

    if [[ "${NON_INTERACTIVE}" == "true" ]]; then
        return 0
    fi

    if [[ "${default}" == "Y" ]]; then
        read -p "${prompt} [Y/n] " -n 1 -r
    else
        read -p "${prompt} [y/N] " -n 1 -r
    fi
    echo

    if [[ "${default}" == "Y" ]]; then
        [[ ! $REPLY =~ ^[Nn]$ ]]
    else
        [[ $REPLY =~ ^[Yy]$ ]]
    fi
}

# Version comparison (returns 0 if version1 >= version2)
version_ge() {
    printf '%s\n%s\n' "$2" "$1" | sort -V -C
}

# Generate secure random key
generate_random_key() {
    local length=${1:-32}
    if command -v openssl &>/dev/null; then
        openssl rand -base64 48 | tr -d '/+=' | cut -c1-"${length}"
    else
        head -c 48 /dev/urandom | base64 | tr -d '/+=' | cut -c1-"${length}"
    fi
}

# Cleanup handler
cleanup_on_exit() {
    local exit_code=$?
    if [[ ${exit_code} -ne 0 ]] && [[ -f "${LOG_FILE}" ]]; then
        echo ""
        log_error "Script failed with exit code ${exit_code}"
        log_error "Log file: ${LOG_FILE}"
    fi
}
trap cleanup_on_exit EXIT

# =============================================================================
# SECTION 3: Prerequisite Check Functions
# =============================================================================

# Check if Azure CLI is installed and meets version requirements
check_azure_cli() {
    log_info "Checking Azure CLI..."

    if ! command -v az &>/dev/null; then
        log_error "Azure CLI is not installed"
        log_error "Install: https://docs.microsoft.com/cli/azure/install-azure-cli"
        return 1
    fi

    local az_version
    az_version=$(az version --query '"azure-cli"' -o tsv 2>/dev/null || echo "0.0.0")

    if ! version_ge "${az_version}" "${MIN_AZ_CLI_VERSION}"; then
        log_error "Azure CLI version ${az_version} is below minimum ${MIN_AZ_CLI_VERSION}"
        log_error "Run: az upgrade"
        return 1
    fi

    log_success "Azure CLI version ${az_version} OK"
    return 0
}

# Check required Azure CLI extensions
check_azure_extensions() {
    log_info "Checking Azure CLI extensions..."

    local installed
    installed=$(az extension list --query "[?name=='${ACA_EXTENSION}'].name" -o tsv 2>/dev/null || echo "")

    if [[ -z "${installed}" ]]; then
        log_info "Installing ${ACA_EXTENSION} extension..."
        if ! az extension add --name "${ACA_EXTENSION}" --yes 2>> "${LOG_FILE}"; then
            log_error "Failed to install ${ACA_EXTENSION} extension"
            return 1
        fi
    else
        # Update if needed (ignore errors)
        az extension update --name "${ACA_EXTENSION}" --yes 2>/dev/null || true
    fi

    log_success "Azure Container Apps extension OK"
    return 0
}

# Check curl for testing
check_curl() {
    log_info "Checking curl..."

    if ! command -v curl &>/dev/null; then
        log_error "curl is not installed"
        log_error "Install curl to enable health checks and verification"
        return 1
    fi

    log_success "curl OK"
    return 0
}

# Check Azure login status
check_azure_login() {
    log_info "Checking Azure login status..."

    if ! az account show &>/dev/null; then
        log_warning "Not logged into Azure CLI"

        if [[ "${NON_INTERACTIVE}" == "true" ]]; then
            log_error "Azure login required. Run 'az login' first."
            return 1
        fi

        if confirm "Would you like to login to Azure now?"; then
            if ! az login; then
                log_error "Azure login failed"
                return 1
            fi
        else
            log_error "Azure login required to continue"
            return 1
        fi
    fi

    local account_name
    account_name=$(az account show --query name -o tsv)
    log_success "Logged in to Azure: ${account_name}"
    return 0
}

# Run all prerequisite checks
run_prerequisite_checks() {
    log_phase 0 "Prerequisite Checks"

    local failed=0

    check_azure_cli || ((failed++))
    check_azure_extensions || ((failed++))
    check_curl || ((failed++))
    check_azure_login || ((failed++))

    if [[ ${failed} -gt 0 ]]; then
        log_error "${failed} prerequisite check(s) failed"
        return 1
    fi

    log_success "All prerequisite checks passed"
    return 0
}

# =============================================================================
# SECTION 4: Interactive Setup Functions
# =============================================================================

# Select Azure subscription
select_subscription() {
    if [[ "${NON_INTERACTIVE}" == "true" ]]; then
        return 0
    fi

    log_info "Available Azure subscriptions:"
    echo ""
    az account list --query "[].{Name:name, Id:id, IsDefault:isDefault}" -o table
    echo ""

    if confirm "Use the current subscription?" "Y"; then
        return 0
    fi

    read -p "Enter subscription ID or name: " subscription_input

    if [[ -n "${subscription_input}" ]]; then
        if ! az account set --subscription "${subscription_input}" 2>> "${LOG_FILE}"; then
            log_error "Failed to set subscription: ${subscription_input}"
            return 1
        fi
        log_success "Switched to subscription: ${subscription_input}"
    fi

    return 0
}

# Select environment
select_environment() {
    if [[ "${NON_INTERACTIVE}" == "true" ]]; then
        return 0
    fi

    echo ""
    echo "Available environments:"
    echo "  1) dev  - Development environment (smaller resources, lower cost)"
    echo "  2) prod - Production environment (HA, larger resources)"
    echo ""

    read -p "Select environment [1/2] (default: 1): " env_choice

    case "${env_choice}" in
        2|prod)
            ENVIRONMENT="prod"
            ;;
        *)
            ENVIRONMENT="dev"
            ;;
    esac

    log_info "Selected environment: ${ENVIRONMENT}"
    return 0
}

# Configure resource group
configure_resource_group() {
    if [[ -z "${RESOURCE_GROUP}" ]]; then
        RESOURCE_GROUP="code-interpreter-${ENVIRONMENT}"
    fi

    if [[ "${NON_INTERACTIVE}" != "true" ]]; then
        echo ""
        read -p "Resource group name [${RESOURCE_GROUP}]: " rg_input
        if [[ -n "${rg_input}" ]]; then
            RESOURCE_GROUP="${rg_input}"
        fi
    fi

    log_info "Resource group: ${RESOURCE_GROUP}"
    return 0
}

# Configure location
configure_location() {
    if [[ "${NON_INTERACTIVE}" != "true" ]]; then
        echo ""
        echo "Common Azure regions: eastus, westus2, westeurope, northeurope, southeastasia"
        read -p "Azure region [${LOCATION}]: " location_input
        if [[ -n "${location_input}" ]]; then
            LOCATION="${location_input}"
        fi
    fi

    log_info "Location: ${LOCATION}"
    return 0
}

# Configure API keys
configure_api_keys() {
    if [[ -z "${API_KEY}" ]]; then
        if [[ "${NON_INTERACTIVE}" != "true" ]]; then
            echo ""
            if confirm "Generate new API keys automatically?" "Y"; then
                API_KEY="$(generate_random_key 32)"
                MASTER_API_KEY="$(generate_random_key 32)"
            else
                read -p "Enter API key (min 16 chars): " API_KEY
                read -p "Enter Master API key (min 16 chars): " MASTER_API_KEY
            fi
        else
            API_KEY="$(generate_random_key 32)"
            MASTER_API_KEY="$(generate_random_key 32)"
        fi
    fi

    if [[ -z "${MASTER_API_KEY}" ]]; then
        MASTER_API_KEY="$(generate_random_key 32)"
    fi

    log_info "API Key: ${API_KEY:0:8}..."
    log_info "Master API Key: ${MASTER_API_KEY:0:8}..."
    return 0
}

# Run interactive setup
run_interactive_setup() {
    echo ""
    echo -e "${BOLD}=== Code Interpreter Azure Setup ===${NC}"
    echo "Version: ${SCRIPT_VERSION}"
    echo ""

    select_subscription || return 1
    select_environment || return 1
    configure_resource_group || return 1
    configure_location || return 1
    configure_api_keys || return 1

    # Display summary
    echo ""
    echo -e "${BOLD}=== Deployment Configuration ===${NC}"
    echo "  Subscription:    $(az account show --query name -o tsv)"
    echo "  Environment:     ${ENVIRONMENT}"
    echo "  Resource Group:  ${RESOURCE_GROUP}"
    echo "  Location:        ${LOCATION}"
    echo "  Skip Build:      ${SKIP_BUILD}"
    echo "  Skip Deploy:     ${SKIP_DEPLOY}"
    echo ""

    if [[ "${NON_INTERACTIVE}" != "true" ]]; then
        if ! confirm "Proceed with deployment?"; then
            log_info "Deployment cancelled"
            exit 0
        fi
    fi

    return 0
}

# =============================================================================
# SECTION 5: Deployment Phase Functions
# =============================================================================

# Phase 1: Create Resource Group and ACR (needed for image builds)
phase_create_acr() {
    log_phase 1 "Create Resource Group and Container Registry"

    if [[ "${DRY_RUN}" == "true" ]]; then
        log_info "[DRY RUN] Would create resource group and ACR"
        return 0
    fi

    # Create resource group
    log_info "Creating resource group ${RESOURCE_GROUP} in ${LOCATION}..."
    if ! az group create --name "${RESOURCE_GROUP}" --location "${LOCATION}" --output none 2>> "${LOG_FILE}"; then
        log_error "Failed to create resource group"
        return 1
    fi
    log_success "Resource group created"

    # Generate ACR name (must be globally unique, alphanumeric only, 5-50 chars)
    local unique_suffix
    unique_suffix=$(echo "${RESOURCE_GROUP}" | md5sum | cut -c1-6)
    ACR_NAME="codeinterp${unique_suffix}acr"
    ACR_SERVER="${ACR_NAME}.azurecr.io"

    # Check if ACR already exists
    if az acr show --name "${ACR_NAME}" --resource-group "${RESOURCE_GROUP}" &>/dev/null; then
        log_info "ACR ${ACR_NAME} already exists"
    else
        log_info "Creating Azure Container Registry: ${ACR_NAME}..."
        if ! az acr create \
            --name "${ACR_NAME}" \
            --resource-group "${RESOURCE_GROUP}" \
            --location "${LOCATION}" \
            --sku Basic \
            --admin-enabled false \
            --output none 2>> "${LOG_FILE}"; then
            log_error "Failed to create ACR"
            return 1
        fi
    fi

    log_success "ACR created: ${ACR_NAME}"
    log_info "ACR Server: ${ACR_SERVER}"

    # Create User-Assigned Managed Identity for Container Apps
    # This allows us to assign AcrPull role BEFORE Container Apps are created
    MANAGED_IDENTITY_NAME="codeinterp-${unique_suffix}-identity"

    log_info "Creating User-Assigned Managed Identity: ${MANAGED_IDENTITY_NAME}..."
    if az identity show --name "${MANAGED_IDENTITY_NAME}" --resource-group "${RESOURCE_GROUP}" &>/dev/null; then
        log_info "Managed Identity ${MANAGED_IDENTITY_NAME} already exists"
    else
        if ! az identity create \
            --name "${MANAGED_IDENTITY_NAME}" \
            --resource-group "${RESOURCE_GROUP}" \
            --location "${LOCATION}" \
            --output none 2>> "${LOG_FILE}"; then
            log_error "Failed to create Managed Identity"
            return 1
        fi
    fi
    log_success "Managed Identity created"

    # Wait for identity to propagate to Azure AD Graph (can take 10-30 seconds)
    log_info "Waiting for identity to propagate to Azure AD..."
    sleep 15

    # Get the identity's principal ID and resource ID
    MANAGED_IDENTITY_PRINCIPAL_ID=$(az identity show \
        --name "${MANAGED_IDENTITY_NAME}" \
        --resource-group "${RESOURCE_GROUP}" \
        --query principalId -o tsv)
    MANAGED_IDENTITY_ID=$(az identity show \
        --name "${MANAGED_IDENTITY_NAME}" \
        --resource-group "${RESOURCE_GROUP}" \
        --query id -o tsv)

    log_info "Identity Principal ID: ${MANAGED_IDENTITY_PRINCIPAL_ID}"

    # Assign AcrPull role to the managed identity (with retry for propagation delay)
    log_info "Assigning AcrPull role to Managed Identity..."
    local acr_id
    acr_id=$(az acr show --name "${ACR_NAME}" --resource-group "${RESOURCE_GROUP}" --query id -o tsv)
    local acr_pull_role="7f951dda-4ed3-4680-a7ca-43fe172d538d"

    # Check if role already assigned
    local existing_role
    existing_role=$(az role assignment list \
        --assignee "${MANAGED_IDENTITY_PRINCIPAL_ID}" \
        --role "${acr_pull_role}" \
        --scope "${acr_id}" \
        --query "[].id" -o tsv 2>/dev/null || echo "")

    if [[ -n "${existing_role}" ]]; then
        log_info "AcrPull role already assigned to Managed Identity"
    else
        # Retry role assignment (identity may still be propagating)
        local max_retries=5
        local retry_delay=10
        local role_assigned=false

        for ((i=1; i<=max_retries; i++)); do
            if az role assignment create \
                --assignee "${MANAGED_IDENTITY_PRINCIPAL_ID}" \
                --role "${acr_pull_role}" \
                --scope "${acr_id}" \
                --output none 2>> "${LOG_FILE}"; then
                role_assigned=true
                break
            fi
            if [[ $i -lt $max_retries ]]; then
                log_warning "Role assignment attempt $i failed, retrying in ${retry_delay}s..."
                sleep ${retry_delay}
                retry_delay=$((retry_delay * 2))  # Exponential backoff
            fi
        done

        if [[ "${role_assigned}" != "true" ]]; then
            log_error "Failed to assign AcrPull role after ${max_retries} attempts"
            return 1
        fi
        log_success "AcrPull role assigned to Managed Identity"
    fi

    # Wait for role propagation (Azure RBAC can take up to 5 minutes)
    log_info "Waiting 30 seconds for role assignment to propagate..."
    sleep 30
    log_success "Role assignment propagation wait complete"

    return 0
}

# Phase 2: Build Docker image using ACR Tasks (BEFORE deploying container apps)
phase_build_images_acr() {
    log_phase 2 "Build Unified Docker Image (ACR Tasks)"

    if [[ "${SKIP_BUILD}" == "true" ]]; then
        log_info "Skipping image build (--skip-build specified)"
        return 0
    fi

    if [[ "${DRY_RUN}" == "true" ]]; then
        log_info "[DRY RUN] Would build unified image using ACR Tasks"
        return 0
    fi

    if [[ -z "${ACR_NAME}" ]]; then
        log_error "ACR not found. Run phase 1 first."
        return 1
    fi

    log_info "Building unified image in Azure Container Registry: ${ACR_NAME}"
    log_info "This uses ACR Tasks - no local Docker required"
    echo ""

    # Build unified image (API + all 12 language runtimes)
    log_info "Building unified image with API + 12 language runtimes..."
    log_info "This is a large image and may take 20-40 minutes"
    az acr build \
        --registry "${ACR_NAME}" \
        --image "code-interpreter-unified:latest" \
        --file "${PROJECT_ROOT}/Dockerfile.unified" \
        --timeout "${UNIFIED_BUILD_TIMEOUT}" \
        "${PROJECT_ROOT}" 2>&1 | stream_tail_output 5 ""
    if [[ ${PIPESTATUS[0]} -ne 0 ]]; then
        log_error "Failed to build unified image"
        return 1
    fi
    log_success "Unified image built and pushed to ACR"

    return 0
}

# Phase 3: Deploy full Azure infrastructure via Bicep (with images already in ACR)
phase_deploy_infrastructure() {
    log_phase 3 "Deploy Azure Infrastructure (Unified Architecture)"

    if [[ "${DRY_RUN}" == "true" ]]; then
        log_info "[DRY RUN] Would deploy Bicep template to ${RESOURCE_GROUP}"
        return 0
    fi

    # Check parameter file exists (use unified parameter files)
    local params_file="${SCRIPT_DIR}/parameters/${ENVIRONMENT}-unified.json"
    if [[ ! -f "${params_file}" ]]; then
        log_warning "Parameter file not found: ${params_file}"
        log_info "Falling back to dev-unified.json"
        params_file="${SCRIPT_DIR}/parameters/dev-unified.json"
    fi

    # Deploy Bicep template
    DEPLOYMENT_NAME="code-interpreter-unified-$(date +%Y%m%d-%H%M%S)"

    log_info "Deploying unified Bicep template..."
    log_info "Deployment name: ${DEPLOYMENT_NAME}"
    log_info "Architecture: Single container (API + Executor combined)"
    log_info "Image will be pulled from ACR: ${ACR_SERVER}"

    # Start deployment with --no-wait to avoid timeout issues
    # The managed identity already has AcrPull role, so image pulls will work immediately
    if ! az deployment group create \
        --name "${DEPLOYMENT_NAME}" \
        --resource-group "${RESOURCE_GROUP}" \
        --template-file "${SCRIPT_DIR}/main-unified.bicep" \
        --parameters "@${params_file}" \
        --parameters apiKeys="${API_KEY}" \
        --parameters masterApiKey="${MASTER_API_KEY}" \
        --parameters usePlaceholderImages=false \
        --parameters existingAcrName="${ACR_NAME}" \
        --parameters managedIdentityId="${MANAGED_IDENTITY_ID}" \
        --no-wait \
        --output none 2>> "${LOG_FILE}"; then
        log_error "Failed to start Bicep deployment"
        return 1
    fi

    log_info "Deployment started..."
    log_info "Unified Container App will use pre-configured managed identity with AcrPull role"

    # Wait for deployment to complete
    wait_for_deployment_complete || return 1

    # Get deployment outputs
    log_info "Retrieving deployment outputs..."

    API_URL=$(az deployment group show \
        --name "${DEPLOYMENT_NAME}" \
        --resource-group "${RESOURCE_GROUP}" \
        --query properties.outputs.apiUrl.value \
        --output tsv 2>/dev/null || echo "")

    # Update ACR_NAME from deployment if different
    local deployed_acr
    deployed_acr=$(az deployment group show \
        --name "${DEPLOYMENT_NAME}" \
        --resource-group "${RESOURCE_GROUP}" \
        --query properties.outputs.acrName.value \
        --output tsv 2>/dev/null || echo "")

    if [[ -n "${deployed_acr}" ]]; then
        ACR_NAME="${deployed_acr}"
        ACR_SERVER="${ACR_NAME}.azurecr.io"
    fi

    log_success "Infrastructure deployed successfully"
    log_info "API URL: ${API_URL}"
    log_info "ACR Name: ${ACR_NAME}"

    return 0
}

# Wait for Container App to be created with managed identity (unified architecture = 1 app)
wait_for_container_apps() {
    local max_attempts=720  # 60 minutes max (Redis can take 10-20 min to provision)
    local attempt=1

    log_info "Waiting for unified Container App to be created..."
    PROGRESS_START_TIME=$(date +%s)

    while [[ ${attempt} -le ${max_attempts} ]]; do
        # Check if container app exists and has managed identity
        local apps_with_identity
        apps_with_identity=$(az containerapp list -g "${RESOURCE_GROUP}" \
            --query "[?identity.principalId!=null].name" -o tsv 2>/dev/null || echo "")

        local app_count
        if [[ -z "${apps_with_identity}" ]]; then
            app_count=0
        else
            app_count=$(echo "${apps_with_identity}" | wc -l | tr -d ' ')
        fi

        # Unified architecture only has 1 container app
        if [[ ${app_count} -ge 1 ]]; then
            clear_progress
            log_success "Found unified Container App with managed identity"
            unset PROGRESS_START_TIME
            return 0
        fi

        show_progress ${attempt} ${max_attempts} "Waiting for container app..."
        sleep 5
        ((attempt++))
    done

    clear_progress
    unset PROGRESS_START_TIME
    log_error "Timed out waiting for Container App to be created"
    return 1
}

# Wait for Bicep deployment to complete
wait_for_deployment_complete() {
    local max_attempts=720  # 60 minutes max
    local attempt=1

    log_info "Waiting for deployment to complete..."
    PROGRESS_START_TIME=$(date +%s)

    while [[ ${attempt} -le ${max_attempts} ]]; do
        local state
        state=$(az deployment group show \
            --name "${DEPLOYMENT_NAME}" \
            --resource-group "${RESOURCE_GROUP}" \
            --query properties.provisioningState \
            --output tsv 2>/dev/null || echo "Unknown")

        case "${state}" in
            Succeeded)
                clear_progress
                unset PROGRESS_START_TIME
                log_success "Deployment completed successfully"
                return 0
                ;;
            Failed)
                clear_progress
                unset PROGRESS_START_TIME
                log_error "Deployment failed"
                # Get error details
                az deployment group show \
                    --name "${DEPLOYMENT_NAME}" \
                    --resource-group "${RESOURCE_GROUP}" \
                    --query properties.error \
                    --output json 2>/dev/null | tee -a "${LOG_FILE}" || true
                return 1
                ;;
            Canceled)
                clear_progress
                unset PROGRESS_START_TIME
                log_error "Deployment was canceled"
                return 1
                ;;
            *)
                show_progress ${attempt} ${max_attempts} "${state}"
                ;;
        esac

        sleep 5
        ((attempt++))
    done

    clear_progress
    unset PROGRESS_START_TIME
    log_warning "Deployment still running after ${max_attempts} attempts, continuing anyway..."
    return 0
}

# Assign AcrPull role to Container Apps for pulling images from ACR
assign_acr_pull_roles() {
    log_info "Assigning AcrPull roles to Container Apps..."

    # Get ACR resource ID
    local acr_id
    acr_id=$(az acr show --name "${ACR_NAME}" --resource-group "${RESOURCE_GROUP}" --query id -o tsv 2>/dev/null || echo "")

    if [[ -z "${acr_id}" ]]; then
        log_error "Could not get ACR resource ID"
        return 1
    fi

    log_debug "ACR ID: ${acr_id}"

    # Get list of container apps in the resource group
    local container_apps
    container_apps=$(az containerapp list -g "${RESOURCE_GROUP}" --query "[].name" -o tsv 2>/dev/null || echo "")

    if [[ -z "${container_apps}" ]]; then
        log_warning "No container apps found in resource group"
        return 0
    fi

    # AcrPull role definition ID
    local acr_pull_role="7f951dda-4ed3-4680-a7ca-43fe172d538d"

    for app_name in ${container_apps}; do
        log_info "  Assigning AcrPull to ${app_name}..."

        # Get the managed identity principal ID
        local principal_id
        principal_id=$(az containerapp show -n "${app_name}" -g "${RESOURCE_GROUP}" \
            --query identity.principalId -o tsv 2>/dev/null || echo "")

        if [[ -z "${principal_id}" ]]; then
            log_warning "  Could not get principal ID for ${app_name}, skipping"
            continue
        fi

        log_debug "  Principal ID: ${principal_id}"

        # Check if role assignment already exists
        local existing
        existing=$(az role assignment list \
            --assignee "${principal_id}" \
            --role "${acr_pull_role}" \
            --scope "${acr_id}" \
            --query "[].id" -o tsv 2>/dev/null || echo "")

        if [[ -n "${existing}" ]]; then
            log_info "  AcrPull role already assigned to ${app_name}"
            continue
        fi

        # Create role assignment
        if az role assignment create \
            --assignee "${principal_id}" \
            --role "${acr_pull_role}" \
            --scope "${acr_id}" \
            --output none 2>> "${LOG_FILE}"; then
            log_success "  AcrPull role assigned to ${app_name}"
        else
            log_warning "  Failed to assign AcrPull role to ${app_name} (may already exist)"
        fi
    done

    log_success "AcrPull role assignments complete"

    # Restart container apps to pick up new role assignments
    restart_container_apps || return 1

    return 0
}

# Restart container apps to pick up new ACR permissions and pull images
restart_container_apps() {
    log_info "Restarting Container Apps to pull images with new permissions..."

    # Get list of container apps in the resource group
    local container_apps
    container_apps=$(az containerapp list -g "${RESOURCE_GROUP}" --query "[].name" -o tsv 2>/dev/null || echo "")

    if [[ -z "${container_apps}" ]]; then
        log_warning "No container apps found to restart"
        return 0
    fi

    for app_name in ${container_apps}; do
        log_info "  Updating ${app_name} to trigger new revision..."

        # Force a new revision by updating with the same image
        # This will re-pull the image with the new AcrPull permissions
        local image
        image=$(az containerapp show -n "${app_name}" -g "${RESOURCE_GROUP}" \
            --query "properties.template.containers[0].image" -o tsv 2>/dev/null || echo "")

        if [[ -n "${image}" ]]; then
            if az containerapp update \
                -n "${app_name}" \
                -g "${RESOURCE_GROUP}" \
                --image "${image}" \
                --output none 2>> "${LOG_FILE}"; then
                log_success "  ${app_name} updated successfully"
            else
                log_warning "  Failed to update ${app_name}"
            fi
        else
            log_warning "  Could not get image for ${app_name}, skipping"
        fi
    done

    log_success "Container Apps restart complete"
    return 0
}

# Phase 4: Wait for container to be ready
phase_wait_for_ready() {
    log_phase 4 "Wait for Unified Container to be Ready"

    if [[ "${DRY_RUN}" == "true" ]]; then
        log_info "[DRY RUN] Would wait for container to be ready"
        return 0
    fi

    # Get API URL if not set (unified architecture - look for 'unified' in name)
    if [[ -z "${API_URL}" ]]; then
        local unified_app
        unified_app=$(az containerapp list -g "${RESOURCE_GROUP}" \
            --query "[?contains(name, 'unified')].name" -o tsv 2>/dev/null || echo "")

        # Fallback: get first container app
        if [[ -z "${unified_app}" ]]; then
            unified_app=$(az containerapp list -g "${RESOURCE_GROUP}" \
                --query "[0].name" -o tsv 2>/dev/null || echo "")
        fi

        if [[ -n "${unified_app}" ]]; then
            local fqdn
            fqdn=$(az containerapp show -n "${unified_app}" -g "${RESOURCE_GROUP}" \
                --query properties.configuration.ingress.fqdn -o tsv 2>/dev/null || echo "")
            if [[ -n "${fqdn}" ]]; then
                API_URL="https://${fqdn}"
            fi
        fi
    fi

    if [[ -z "${API_URL}" ]]; then
        log_error "Could not determine API URL"
        return 1
    fi

    local attempt=1

    log_info "Waiting for unified container to become healthy..."
    log_info "URL: ${API_URL}/health"
    PROGRESS_START_TIME=$(date +%s)

    while [[ ${attempt} -le ${HEALTH_CHECK_MAX_ATTEMPTS} ]]; do
        local http_code
        http_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "${API_URL}/health" 2>/dev/null || echo "000")

        if [[ "${http_code}" == "200" ]]; then
            clear_progress
            unset PROGRESS_START_TIME
            log_success "Unified container is healthy!"
            return 0
        fi

        show_progress ${attempt} ${HEALTH_CHECK_MAX_ATTEMPTS} "HTTP ${http_code}"
        sleep "${HEALTH_CHECK_INTERVAL}"
        ((attempt++))
    done

    clear_progress
    unset PROGRESS_START_TIME
    log_error "Container did not become healthy within ${HEALTH_CHECK_MAX_ATTEMPTS} attempts"
    log_error "Check container logs:"
    log_error "  az containerapp logs show -n <app-name> -g ${RESOURCE_GROUP} --follow"
    return 1
}

# Phase 5: Run verification tests
phase_run_verification() {
    log_phase 5 "Verification Tests"

    if [[ "${DRY_RUN}" == "true" ]]; then
        log_info "[DRY RUN] Would run verification tests"
        return 0
    fi

    # Test health endpoint
    log_info "Testing health endpoint..."
    local health_response
    health_response=$(curl -s --max-time 10 "${API_URL}/health" 2>/dev/null || echo "")

    if [[ -n "${health_response}" ]]; then
        echo "  Response: ${health_response}"
        log_success "Health endpoint responding"
    else
        log_warning "Health endpoint not responding"
    fi

    # Test code execution
    log_info "Testing Python code execution..."
    local exec_response
    exec_response=$(curl -s --max-time 30 -X POST "${API_URL}/exec" \
        -H "x-api-key: ${API_KEY}" \
        -H "Content-Type: application/json" \
        -d '{"code":"print(\"Hello from Azure Container Apps!\")","lang":"py"}' 2>/dev/null || echo "")

    if [[ -n "${exec_response}" ]]; then
        echo "  Response: ${exec_response}"

        if echo "${exec_response}" | grep -q "Hello from Azure Container Apps!"; then
            log_success "Python execution test passed!"
        else
            log_warning "Execution returned response but output may differ - check above"
        fi
    else
        log_warning "Execution test did not return a response"
    fi

    return 0
}

# =============================================================================
# SECTION 6: Cleanup/Uninstall Functions
# =============================================================================

# Uninstall/cleanup function
run_uninstall() {
    log_phase 0 "Uninstall/Cleanup"

    if [[ -z "${RESOURCE_GROUP}" ]]; then
        # Try to find existing resource groups
        echo "Existing Code Interpreter resource groups:"
        az group list --query "[?starts_with(name, 'code-interpreter')].name" -o table 2>/dev/null || true
        echo ""
        read -p "Enter resource group to delete: " RESOURCE_GROUP
    fi

    if [[ -z "${RESOURCE_GROUP}" ]]; then
        log_error "Resource group name required"
        return 1
    fi

    # Check if resource group exists
    if ! az group show --name "${RESOURCE_GROUP}" &>/dev/null; then
        log_warning "Resource group ${RESOURCE_GROUP} does not exist"
        return 0
    fi

    # List resources to be deleted
    log_info "Resources in ${RESOURCE_GROUP} that will be deleted:"
    az resource list -g "${RESOURCE_GROUP}" --query "[].{Name:name, Type:type}" -o table 2>/dev/null || true
    echo ""

    if [[ "${NON_INTERACTIVE}" != "true" ]]; then
        echo -e "${RED}${BOLD}WARNING: This will permanently delete all resources in the resource group!${NC}"
        if ! confirm "Are you sure you want to delete ${RESOURCE_GROUP}?"; then
            log_info "Uninstall cancelled"
            return 0
        fi

        # Double confirmation for production
        if [[ "${RESOURCE_GROUP}" == *"prod"* ]]; then
            echo -e "${RED}${BOLD}This appears to be a PRODUCTION resource group!${NC}"
            read -p "Type the resource group name to confirm deletion: " confirm_name
            if [[ "${confirm_name}" != "${RESOURCE_GROUP}" ]]; then
                log_info "Confirmation failed - uninstall cancelled"
                return 0
            fi
        fi
    fi

    if [[ "${DRY_RUN}" == "true" ]]; then
        log_info "[DRY RUN] Would delete resource group ${RESOURCE_GROUP}"
        return 0
    fi

    log_info "Deleting resource group ${RESOURCE_GROUP}..."
    log_info "This may take several minutes..."

    if ! az group delete --name "${RESOURCE_GROUP}" --yes --no-wait 2>> "${LOG_FILE}"; then
        log_error "Failed to initiate resource group deletion"
        return 1
    fi

    log_success "Resource group deletion initiated"
    log_info "Deletion is running in the background and may take a few minutes to complete"

    # Clean up local credentials file
    local creds_file="${SCRIPT_DIR}/.credentials.${ENVIRONMENT}"
    if [[ -f "${creds_file}" ]]; then
        rm -f "${creds_file}"
        log_info "Removed local credentials file: ${creds_file}"
    fi

    return 0
}

# =============================================================================
# SECTION 7: Save Credentials and Summary
# =============================================================================

# Save deployment credentials
save_credentials() {
    local creds_file="${SCRIPT_DIR}/.credentials.${ENVIRONMENT}"

    cat > "${creds_file}" << EOF
# Code Interpreter ACA Credentials - ${ENVIRONMENT} (Unified Architecture)
# Generated: $(date -Iseconds)
# Resource Group: ${RESOURCE_GROUP}
# Deployment: ${DEPLOYMENT_NAME:-unknown}
# Architecture: Single Container (API + Executor combined)

export API_KEY="${API_KEY}"
export MASTER_API_KEY="${MASTER_API_KEY}"
export API_URL="${API_URL}"
export ACR_NAME="${ACR_NAME}"
export ACR_SERVER="${ACR_SERVER}"
export RESOURCE_GROUP="${RESOURCE_GROUP}"
export ENVIRONMENT="${ENVIRONMENT}"
EOF

    chmod 600 "${creds_file}"
    log_info "Credentials saved to: ${creds_file}"
}

# Print completion summary
print_completion_summary() {
    local duration=$1

    echo ""
    echo -e "${GREEN}${BOLD}======================================${NC}"
    echo -e "${GREEN}${BOLD}  Deployment Complete!${NC}"
    echo -e "${GREEN}${BOLD}======================================${NC}"
    echo ""
    echo "Architecture:      Unified (Single Container)"
    echo "API URL:           ${API_URL}"
    echo "Resource Group:    ${RESOURCE_GROUP}"
    echo "Environment:       ${ENVIRONMENT}"
    echo "Duration:          ${duration} seconds"
    echo ""
    echo "Credentials file:  ${SCRIPT_DIR}/.credentials.${ENVIRONMENT}"
    echo "Log file:          ${LOG_FILE}"
    echo ""
    echo -e "${BOLD}Architecture Details:${NC}"
    echo "  - Single container with API + 12 language runtimes"
    echo "  - Serialized execution (1 request per replica)"
    echo "  - ACA-managed scaling based on concurrent requests"
    echo ""
    echo -e "${BOLD}Quick Start:${NC}"
    echo "  source ${SCRIPT_DIR}/.credentials.${ENVIRONMENT}"
    echo "  curl \${API_URL}/health"
    echo "  curl -X POST \${API_URL}/exec \\"
    echo "    -H \"x-api-key: \${API_KEY}\" \\"
    echo "    -H \"Content-Type: application/json\" \\"
    echo "    -d '{\"code\":\"print(42)\",\"language\":\"py\"}'"
    echo ""
    echo -e "${BOLD}View Logs:${NC}"
    echo "  az containerapp logs show -n \$(az containerapp list -g ${RESOURCE_GROUP} --query '[0].name' -o tsv) -g ${RESOURCE_GROUP} --follow"
    echo ""
    echo -e "${BOLD}Check Replicas:${NC}"
    echo "  az containerapp replica list -n \$(az containerapp list -g ${RESOURCE_GROUP} --query '[0].name' -o tsv) -g ${RESOURCE_GROUP} -o table"
    echo ""
}

# =============================================================================
# SECTION 8: Help and Argument Parsing
# =============================================================================

# Show help
show_help() {
    cat << EOF
${BOLD}Code Interpreter Azure Container Apps Setup Script (Unified Architecture)${NC}
Version: ${SCRIPT_VERSION}

${BOLD}USAGE:${NC}
    ${SCRIPT_NAME} [OPTIONS]

${BOLD}DESCRIPTION:${NC}
    Unified end-to-end deployment script for Code Interpreter API to Azure Container Apps.
    Uses the UNIFIED single-container architecture:
    - Single container with API + 12 language runtimes
    - Serialized execution (1 request per replica) for isolation
    - ACA-managed scaling based on concurrent requests

    Handles infrastructure provisioning, Docker image building (via ACR Tasks), and
    deployment verification. No local Docker installation required.

${BOLD}OPTIONS:${NC}
    -h, --help              Show this help message
    -e, --env <env>         Environment to deploy (dev|prod) [default: dev]
    -g, --rg <name>         Resource group name [default: code-interpreter-<env>]
    -l, --location <region> Azure region [default: eastus]

    --skip-build            Skip image build (use existing image in ACR)
    --skip-deploy           Only build and push image, skip infrastructure deployment
    --uninstall             Remove all Azure resources for the environment
    --cleanup               Alias for --uninstall

    --api-key <key>         Use provided API key instead of generating
    --master-key <key>      Use provided master key instead of generating

    --non-interactive       Run without prompts (use defaults)
    --verbose               Enable verbose output
    --dry-run               Show what would be done without executing

${BOLD}EXAMPLES:${NC}
    # Interactive setup for development
    ${SCRIPT_NAME}

    # Non-interactive production deployment
    ${SCRIPT_NAME} --env prod --non-interactive

    # Deploy to specific resource group and region
    ${SCRIPT_NAME} --rg my-codeinterp-dev --location westus2

    # Update image only (skip infrastructure)
    ${SCRIPT_NAME} --skip-deploy

    # Deploy infrastructure, skip image build (use existing)
    ${SCRIPT_NAME} --skip-build

    # Cleanup/uninstall
    ${SCRIPT_NAME} --uninstall --env dev

${BOLD}PREREQUISITES:${NC}
    - Azure CLI >= ${MIN_AZ_CLI_VERSION}
    - Azure subscription with appropriate permissions
    - No local Docker required (builds happen in Azure)

${BOLD}FILES:${NC}
    infra/aca/parameters/dev-unified.json   - Development environment parameters
    infra/aca/parameters/prod-unified.json  - Production environment parameters
    infra/aca/main-unified.bicep            - Infrastructure as Code template
    infra/aca/Dockerfile.unified            - Unified Docker image (API + runtimes)
    infra/aca/.credentials.<env>            - Generated credentials (gitignored)
    infra/aca/setup-*.log                   - Deployment log files

EOF
}

# Parse command line arguments
parse_arguments() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            -h|--help)
                show_help
                exit 0
                ;;
            -e|--env)
                ENVIRONMENT="$2"
                shift 2
                ;;
            -g|--rg)
                RESOURCE_GROUP="$2"
                shift 2
                ;;
            -l|--location)
                LOCATION="$2"
                shift 2
                ;;
            --skip-build)
                SKIP_BUILD=true
                shift
                ;;
            --skip-deploy)
                SKIP_DEPLOY=true
                shift
                ;;
            --uninstall|--cleanup)
                UNINSTALL=true
                shift
                ;;
            --api-key)
                API_KEY="$2"
                shift 2
                ;;
            --master-key)
                MASTER_API_KEY="$2"
                shift 2
                ;;
            --non-interactive)
                NON_INTERACTIVE=true
                shift
                ;;
            --verbose)
                VERBOSE=true
                shift
                ;;
            --dry-run)
                DRY_RUN=true
                shift
                ;;
            *)
                log_error "Unknown option: $1"
                echo "Run '${SCRIPT_NAME} --help' for usage"
                exit 1
                ;;
        esac
    done

    # Validate environment
    if [[ "${ENVIRONMENT}" != "dev" ]] && [[ "${ENVIRONMENT}" != "prod" ]]; then
        log_error "Invalid environment: ${ENVIRONMENT}. Must be 'dev' or 'prod'"
        exit 1
    fi
}

# =============================================================================
# SECTION 9: Main Entry Point
# =============================================================================

main() {
    # Initialize log file
    init_log "$@"

    # Parse arguments
    parse_arguments "$@"

    # Handle uninstall
    if [[ "${UNINSTALL}" == "true" ]]; then
        run_prerequisite_checks || exit 1
        run_uninstall
        exit $?
    fi

    # Run prerequisite checks
    run_prerequisite_checks || exit 1

    # Run interactive setup
    run_interactive_setup || exit 1

    local start_time
    start_time=$(date +%s)

    # New deployment flow: Create ACR first, build images, then deploy infrastructure
    # This ensures images exist in ACR before Container Apps try to pull them

    if [[ "${SKIP_DEPLOY}" != "true" ]]; then
        # Phase 1: Create resource group and ACR
        phase_create_acr || exit 1

        # Phase 2: Build images to ACR (before deploying container apps)
        phase_build_images_acr || exit 1

        # Phase 3: Deploy full infrastructure (Container Apps will pull from ACR)
        phase_deploy_infrastructure || exit 1
    else
        # If skipping deploy, we need to get ACR info from existing deployment
        log_info "Skipping infrastructure deployment, using existing resources..."
        ACR_NAME=$(az acr list -g "${RESOURCE_GROUP}" --query "[0].name" -o tsv 2>/dev/null || echo "")
        if [[ -z "${ACR_NAME}" ]]; then
            log_error "Could not find ACR in resource group ${RESOURCE_GROUP}"
            log_error "Run without --skip-deploy to create infrastructure first"
            exit 1
        fi
        ACR_SERVER="${ACR_NAME}.azurecr.io"

        # Get API URL (look for unified container app or fallback to first app)
        local unified_app
        unified_app=$(az containerapp list -g "${RESOURCE_GROUP}" \
            --query "[?contains(name, 'unified')].name" -o tsv 2>/dev/null || echo "")
        if [[ -z "${unified_app}" ]]; then
            unified_app=$(az containerapp list -g "${RESOURCE_GROUP}" \
                --query "[0].name" -o tsv 2>/dev/null || echo "")
        fi
        if [[ -n "${unified_app}" ]]; then
            local fqdn
            fqdn=$(az containerapp show -n "${unified_app}" -g "${RESOURCE_GROUP}" \
                --query properties.configuration.ingress.fqdn -o tsv 2>/dev/null || echo "")
            if [[ -n "${fqdn}" ]]; then
                API_URL="https://${fqdn}"
            fi
        fi

        log_info "Using existing ACR: ${ACR_NAME}"
        log_info "Using existing API URL: ${API_URL}"

        # Only build image if not skipping
        phase_build_images_acr || exit 1
    fi

    # Phase 4: Wait for containers to be ready
    phase_wait_for_ready || exit 1

    # Phase 5: Run verification tests
    phase_run_verification || exit 1

    # Save credentials
    save_credentials

    local end_time
    end_time=$(date +%s)
    local duration=$((end_time - start_time))

    # Print completion summary
    print_completion_summary "${duration}"

    return 0
}

# Run main
main "$@"
