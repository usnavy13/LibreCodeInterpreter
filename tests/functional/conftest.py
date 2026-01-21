"""Functional test fixtures for live API testing.

These tests run against a real API endpoint (local or remote).
Configure via environment variables:
    API_BASE: Base URL (default: http://localhost:8000)
    API_KEY: API key for authentication
    API_TIMEOUT: Request timeout in seconds (default: 60)

Example:
    API_BASE="https://code-exec.eastus.cloudapp.azure.com" \
    API_KEY="sk-your-api-key" \
    pytest tests/functional/ -v
"""

import os
import uuid
from typing import AsyncGenerator, Dict, Tuple

import httpx
import pytest
import pytest_asyncio

# Configuration from environment
API_BASE = os.environ.get("API_BASE", "http://localhost:8000")
API_KEY = os.environ.get("API_KEY", "test-api-key-for-development-only")
API_TIMEOUT = int(os.environ.get("API_TIMEOUT", "60"))


# Language snippets: (code, expected_substring_in_stdout)
# All compute sum(1..10) = 55 for consistency
LANGUAGE_SNIPPETS: Dict[str, Tuple[str, str]] = {
    "py": ("print('py: sum(1..10)=', sum(range(1,11)))", "55"),
    "js": ("console.log('js: sum(1..10)=' + (1+2+3+4+5+6+7+8+9+10));", "55"),
    "ts": ("console.log('ts: sum(1..10)=' + (1+2+3+4+5+6+7+8+9+10));", "55"),
    "go": (
        'package main\n\nimport (\n\t"fmt"\n)\n\nfunc main() {\n\ts := 0\n\t'
        "for i := 1; i <= 10; i++ {\n\t\ts += i\n\t}\n\t"
        'fmt.Printf("go: sum(1..10)=%d\\n", s)\n}',
        "55",
    ),
    "java": (
        "public class Code { public static void main(String[] args){ "
        'int s=0; for(int i=1;i<=10;i++) s+=i; System.out.println("java: sum(1..10)="+s); } }',
        "55",
    ),
    "c": (
        "#include <stdio.h>\nint main(){int s=0; for(int i=1;i<=10;i++) s+=i; "
        'printf("c: sum(1..10)=%d\\n", s); return 0;}',
        "55",
    ),
    "cpp": (
        "#include <iostream>\nint main(){int s=0; for(int i=1;i<=10;i++) s+=i; "
        'std::cout << "cpp: sum(1..10)=" << s << std::endl; return 0;}',
        "55",
    ),
    "php": (
        '<?php $s=0; for($i=1;$i<=10;$i++){ $s+=$i; } echo "php: sum(1..10)=$s\\n";',
        "55",
    ),
    "rs": (
        "fn main(){ let mut s = 0; for i in 1..=10 { s += i; } "
        'println!("rs: sum(1..10)={}", s); }',
        "55",
    ),
    "r": ("cat('r: sum(1..10)=', sum(1:10), '\\n')", "55"),
    "f90": (
        "program sum\n  integer :: s, i\n  s = 0\n  do i = 1, 10\n     s = s + i\n  end do\n"
        '  print *, "f90: sum(1..10)=", s\nend program sum\n',
        "55",
    ),
    "d": (
        'import std.stdio;\nvoid main(){ int s=0; foreach(i; 1..11) s+=i; writeln("d: sum(1..10)=", s); }',
        "55",
    ),
}


@pytest.fixture(scope="session")
def api_base() -> str:
    """API base URL."""
    return API_BASE.rstrip("/")


@pytest.fixture(scope="session")
def api_key() -> str:
    """API key for authentication."""
    return API_KEY


@pytest.fixture(scope="session")
def auth_headers(api_key: str) -> Dict[str, str]:
    """Standard authentication headers."""
    return {
        "x-api-key": api_key,
        "Content-Type": "application/json",
    }


@pytest_asyncio.fixture
async def async_client(api_base: str) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Async HTTP client for functional tests."""
    client = httpx.AsyncClient(
        base_url=api_base,
        timeout=API_TIMEOUT,
        verify=False,  # Allow self-signed certs
    )
    try:
        yield client
    finally:
        try:
            await client.aclose()
        except RuntimeError:
            # Ignore "Event loop is closed" errors during teardown
            pass


@pytest.fixture
def unique_session_id() -> str:
    """Generate unique session ID for test isolation."""
    return f"func-test-{uuid.uuid4().hex[:12]}"


@pytest.fixture
def unique_entity_id() -> str:
    """Generate unique entity ID for test isolation."""
    return f"entity-{uuid.uuid4().hex[:8]}"


@pytest.fixture(params=list(LANGUAGE_SNIPPETS.keys()))
def language_test_case(request):
    """Parametrized fixture for all 12 languages."""
    lang = request.param
    code, expected = LANGUAGE_SNIPPETS[lang]
    return {"lang": lang, "code": code, "expected_output": expected}
