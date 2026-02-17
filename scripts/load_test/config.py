"""Configuration for load testing and VM sizing recommendations."""

import os
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

# Cloud VM type mappings based on CPU cores and memory
# Format: {(min_cores, min_memory_gb): vm_type}

AZURE_VM_TYPES: Dict[Tuple[int, int], str] = {
    (2, 4): "Standard_D2s_v3",
    (2, 8): "Standard_D2s_v3",
    (4, 8): "Standard_D4s_v3",
    (4, 16): "Standard_D4s_v3",
    (8, 16): "Standard_D8s_v3",
    (8, 32): "Standard_D8s_v3",
    (16, 32): "Standard_D16s_v3",
    (16, 64): "Standard_D16s_v3",
    (32, 64): "Standard_D32s_v3",
    (32, 128): "Standard_D32s_v3",
    (48, 96): "Standard_D48s_v3",
    (64, 128): "Standard_D64s_v3",
}

AWS_INSTANCE_TYPES: Dict[Tuple[int, int], str] = {
    (2, 4): "m5.large",
    (2, 8): "m5.large",
    (4, 8): "m5.xlarge",
    (4, 16): "m5.xlarge",
    (8, 16): "m5.2xlarge",
    (8, 32): "m5.2xlarge",
    (16, 32): "m5.4xlarge",
    (16, 64): "m5.4xlarge",
    (32, 64): "m5.8xlarge",
    (32, 128): "m5.8xlarge",
    (48, 96): "m5.12xlarge",
    (64, 128): "m5.16xlarge",
}

GCP_MACHINE_TYPES: Dict[Tuple[int, int], str] = {
    (2, 4): "n2-standard-2",
    (2, 8): "n2-standard-2",
    (4, 8): "n2-standard-4",
    (4, 16): "n2-standard-4",
    (8, 16): "n2-standard-8",
    (8, 32): "n2-standard-8",
    (16, 32): "n2-standard-16",
    (16, 64): "n2-standard-16",
    (32, 64): "n2-standard-32",
    (32, 128): "n2-standard-32",
    (48, 96): "n2-standard-48",
    (64, 128): "n2-standard-64",
}

# Performance thresholds for breaking point detection
DEFAULT_THRESHOLDS = {
    "p99_latency_ms": 1000,  # P99 latency threshold
    "error_rate_percent": 5.0,  # Max acceptable error rate
    "throughput_degradation_percent": 20.0,  # Max throughput drop from peak
}

# Supported languages
SUPPORTED_LANGUAGES = ["py", "js", "ts", "go", "java", "c", "cpp", "php", "rs", "r", "f90", "d"]


@dataclass
class EnvironmentProfile:
    """Target environment configuration."""

    name: str
    base_url: str
    api_key_env_var: str = "API_KEY"
    expected_p50_latency_ms: float = 50.0
    expected_p99_latency_ms: float = 500.0
    expected_throughput_rps: float = 20.0
    max_error_rate_percent: float = 1.0

    def get_api_key(self) -> str:
        """Get API key from environment."""
        return os.environ.get(self.api_key_env_var, "")


# Predefined environment profiles
ENVIRONMENTS: Dict[str, EnvironmentProfile] = {
    "dev": EnvironmentProfile(
        name="development",
        base_url="https://localhost",
        api_key_env_var="API_KEY",
        expected_p50_latency_ms=100.0,
        expected_p99_latency_ms=1000.0,
        expected_throughput_rps=10.0,
        max_error_rate_percent=5.0,
    ),
    "staging": EnvironmentProfile(
        name="staging",
        base_url="https://staging.example.com",
        api_key_env_var="STAGING_API_KEY",
        expected_p50_latency_ms=75.0,
        expected_p99_latency_ms=750.0,
        expected_throughput_rps=15.0,
        max_error_rate_percent=2.0,
    ),
    "prod": EnvironmentProfile(
        name="production",
        base_url="https://api.example.com",
        api_key_env_var="PROD_API_KEY",
        expected_p50_latency_ms=50.0,
        expected_p99_latency_ms=500.0,
        expected_throughput_rps=20.0,
        max_error_rate_percent=1.0,
    ),
}


def get_vm_type(
    cpu_cores: int,
    memory_gb: int,
    provider: str = "azure"
) -> str:
    """Get recommended VM type for given resources."""
    vm_maps = {
        "azure": AZURE_VM_TYPES,
        "aws": AWS_INSTANCE_TYPES,
        "gcp": GCP_MACHINE_TYPES,
    }

    vm_map = vm_maps.get(provider.lower(), AZURE_VM_TYPES)

    # Find the smallest VM that meets requirements
    candidates = []
    for (cores, memory), vm_type in vm_map.items():
        if cores >= cpu_cores and memory >= memory_gb:
            candidates.append((cores, memory, vm_type))

    if not candidates:
        # Return largest available
        max_key = max(vm_map.keys(), key=lambda x: (x[0], x[1]))
        return vm_map[max_key]

    # Return smallest that meets requirements
    candidates.sort(key=lambda x: (x[0], x[1]))
    return candidates[0][2]


def get_all_vm_recommendations(cpu_cores: int, memory_gb: int) -> Dict[str, str]:
    """Get VM recommendations for all cloud providers."""
    return {
        "azure": get_vm_type(cpu_cores, memory_gb, "azure"),
        "aws": get_vm_type(cpu_cores, memory_gb, "aws"),
        "gcp": get_vm_type(cpu_cores, memory_gb, "gcp"),
    }


@dataclass
class LoadTestDefaults:
    """Default configuration values."""

    min_concurrency: int = 1
    max_concurrency: int = 50
    concurrency_step: int = 5
    requests_per_step: int = 100
    warmup_requests: int = 10
    timeout_seconds: int = 60
    monitor_interval_seconds: float = 1.0
    enable_sandbox_stats: bool = True
    output_dir: str = "./load_test_results"

    # Thresholds
    p99_latency_threshold_ms: float = 1000.0
    error_rate_threshold_percent: float = 5.0
    throughput_degradation_threshold_percent: float = 20.0

    # Languages to test
    default_languages: List[str] = field(default_factory=lambda: ["py"])

    # Categories to test
    default_categories: List[str] = field(
        default_factory=lambda: ["cpu", "memory", "io"]
    )


DEFAULTS = LoadTestDefaults()
