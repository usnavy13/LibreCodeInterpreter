#!/usr/bin/env python3
"""Run functional tests against a remote API endpoint.

This script bypasses the tests/conftest.py which overrides API_KEY.

Usage:
    python scripts/run_functional_tests.py \
        --api-base "https://example.com" \
        --api-key "your-api-key"
"""

import argparse
import asyncio
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Dict, List, Tuple

import httpx

# Language snippets: (code, expected_substring_in_stdout)
LANGUAGE_SNIPPETS: Dict[str, Tuple[str, str]] = {
    "py": ("print('py: sum(1..10)=', sum(range(1,11)))", "55"),
    "js": ("console.log('js: sum(1..10)=' + (1+2+3+4+5+6+7+8+9+10));", "55"),
    "ts": ("console.log('ts: sum(1..10)=' + (1+2+3+4+5+6+7+8+9+10));", "55"),
    "go": (
        'package main\n\nimport (\n\t"fmt"\n)\n\nfunc main() {\n\ts := 0\n\t'
        'for i := 1; i <= 10; i++ {\n\t\ts += i\n\t}\n\t'
        'fmt.Printf("go: sum(1..10)=%d\\n", s)\n}',
        "55",
    ),
    "java": (
        "public class Code { public static void main(String[] args){ "
        'int s=0; for(int i=1;i<=10;i++) s+=i; System.out.println("java: sum(1..10)="+s); } }',
        "55",
    ),
    "c": (
        '#include <stdio.h>\nint main(){int s=0; for(int i=1;i<=10;i++) s+=i; '
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


@dataclass
class TestResult:
    name: str
    passed: bool
    message: str
    duration_ms: float


class FunctionalTester:
    def __init__(self, api_base: str, api_key: str, timeout: int = 60):
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.results: List[TestResult] = []

    def headers(self) -> Dict[str, str]:
        return {"x-api-key": self.api_key, "Content-Type": "application/json"}

    async def run_all(self):
        async with httpx.AsyncClient(
            base_url=self.api_base, timeout=self.timeout, verify=False
        ) as client:
            # Health tests
            await self.test_health(client)
            await self.test_health_detailed(client)

            # Language execution tests
            for lang, (code, expected) in LANGUAGE_SNIPPETS.items():
                await self.test_language_execution(client, lang, code, expected)

            # State persistence tests
            await self.test_state_persistence(client)

            # File tests
            await self.test_file_upload_download(client)

        self.print_summary()

    async def test_health(self, client: httpx.AsyncClient):
        start = time.perf_counter()
        try:
            r = await client.get("/health")
            passed = r.status_code == 200 and "status" in r.json()
            msg = f"Status: {r.status_code}" if passed else f"Failed: {r.text[:100]}"
        except Exception as e:
            passed = False
            msg = str(e)
        self.results.append(TestResult(
            "health_check", passed, msg, (time.perf_counter() - start) * 1000
        ))

    async def test_health_detailed(self, client: httpx.AsyncClient):
        start = time.perf_counter()
        try:
            r = await client.get("/health/detailed", headers=self.headers())
            passed = r.status_code in [200, 503]
            msg = f"Status: {r.status_code}" if passed else f"Failed: {r.text[:100]}"
        except Exception as e:
            passed = False
            msg = str(e)
        self.results.append(TestResult(
            "health_detailed", passed, msg, (time.perf_counter() - start) * 1000
        ))

    async def test_language_execution(
        self, client: httpx.AsyncClient, lang: str, code: str, expected: str
    ):
        start = time.perf_counter()
        try:
            entity_id = f"test-{uuid.uuid4().hex[:8]}"
            r = await client.post(
                "/exec",
                headers=self.headers(),
                json={"code": code, "lang": lang, "entity_id": entity_id},
            )
            if r.status_code == 200:
                data = r.json()
                stdout = data.get("stdout", "")
                if expected in stdout:
                    passed = True
                    msg = f"OK - output contains '{expected}'"
                else:
                    passed = False
                    msg = f"Expected '{expected}' in stdout, got: {stdout[:100]}"
            else:
                passed = False
                msg = f"Status {r.status_code}: {r.text[:100]}"
        except Exception as e:
            passed = False
            msg = str(e)
        self.results.append(TestResult(
            f"exec_{lang}", passed, msg, (time.perf_counter() - start) * 1000
        ))

    async def test_state_persistence(self, client: httpx.AsyncClient):
        start = time.perf_counter()
        entity_id = f"state-test-{uuid.uuid4().hex[:8]}"
        try:
            # Step 1: Create variable
            r1 = await client.post(
                "/exec",
                headers=self.headers(),
                json={"code": "test_var = 42", "lang": "py", "entity_id": entity_id},
            )
            if r1.status_code != 200:
                self.results.append(TestResult(
                    "state_persistence", False, f"Step 1 failed: {r1.text[:100]}",
                    (time.perf_counter() - start) * 1000
                ))
                return

            has_state = r1.json().get("has_state", False)

            # Step 2: Use variable
            r2 = await client.post(
                "/exec",
                headers=self.headers(),
                json={"code": "print(test_var + 1)", "lang": "py", "entity_id": entity_id},
            )
            if r2.status_code != 200:
                self.results.append(TestResult(
                    "state_persistence", False, f"Step 2 failed: {r2.text[:100]}",
                    (time.perf_counter() - start) * 1000
                ))
                return

            stdout = r2.json().get("stdout", "")
            if "43" in stdout:
                passed = True
                msg = f"OK - state persisted (has_state={has_state})"
            else:
                passed = False
                msg = f"Expected '43' in stdout, got: {stdout[:100]}, stderr: {r2.json().get('stderr', '')[:100]}"

        except Exception as e:
            passed = False
            msg = str(e)
        self.results.append(TestResult(
            "state_persistence", passed, msg, (time.perf_counter() - start) * 1000
        ))

    async def test_file_upload_download(self, client: httpx.AsyncClient):
        start = time.perf_counter()
        entity_id = f"file-test-{uuid.uuid4().hex[:8]}"
        try:
            # Upload
            files = {"files": ("test.txt", b"hello world", "text/plain")}
            r = await client.post(
                "/upload",
                headers={"x-api-key": self.api_key},
                files=files,
                data={"entity_id": entity_id},
            )
            if r.status_code != 200:
                self.results.append(TestResult(
                    "file_upload", False, f"Upload failed: {r.text[:100]}",
                    (time.perf_counter() - start) * 1000
                ))
                return

            data = r.json()
            session_id = data.get("session_id")
            file_list = data.get("files", [])
            if not file_list:
                self.results.append(TestResult(
                    "file_upload", False, "No files in response",
                    (time.perf_counter() - start) * 1000
                ))
                return

            file_id = file_list[0].get("fileId")

            # Download
            r2 = await client.get(
                f"/download/{session_id}/{file_id}",
                headers=self.headers(),
            )
            if r2.status_code == 200 and r2.content == b"hello world":
                passed = True
                msg = "OK - upload and download successful"
            else:
                passed = False
                msg = f"Download failed: status={r2.status_code}"

        except Exception as e:
            passed = False
            msg = str(e)
        self.results.append(TestResult(
            "file_upload_download", passed, msg, (time.perf_counter() - start) * 1000
        ))

    def print_summary(self):
        passed = sum(1 for r in self.results if r.passed)
        failed = len(self.results) - passed

        print("\n" + "=" * 70)
        print("FUNCTIONAL TEST RESULTS")
        print("=" * 70)
        print(f"Endpoint: {self.api_base}")
        print("=" * 70)

        for r in self.results:
            status = "PASS" if r.passed else "FAIL"
            print(f"[{status}] {r.name:30} ({r.duration_ms:7.1f}ms) - {r.message[:50]}")

        print("=" * 70)
        print(f"TOTAL: {passed}/{len(self.results)} passed, {failed} failed")
        print(f"Success rate: {passed/len(self.results)*100:.1f}%")
        print("=" * 70)

        return failed == 0


def main():
    parser = argparse.ArgumentParser(description="Run functional tests")
    parser.add_argument("--api-base", required=True, help="API base URL")
    parser.add_argument("--api-key", required=True, help="API key")
    parser.add_argument("--timeout", type=int, default=60, help="Request timeout")
    args = parser.parse_args()

    tester = FunctionalTester(args.api_base, args.api_key, args.timeout)
    asyncio.run(tester.run_all())


if __name__ == "__main__":
    main()
