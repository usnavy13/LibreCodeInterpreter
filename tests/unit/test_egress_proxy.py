"""Unit tests for the sandbox egress proxy."""

import asyncio
import socket

import pytest

from src.services.sandbox.egress_proxy import (
    EgressProxy,
    _is_private_ip,
    _matches_allowlist,
    _normalize_host,
)


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# --- Pure-function tests (no proxy server needed) ---------------------------


class TestAllowlistMatching:
    def test_exact_match(self):
        assert _matches_allowlist("pypi.org", {"pypi.org"})

    def test_subdomain_match(self):
        assert _matches_allowlist("files.pythonhosted.org", {"pythonhosted.org"})

    def test_subdomain_match_two_levels(self):
        assert _matches_allowlist("a.b.example.com", {"example.com"})

    def test_unrelated_host_rejected(self):
        assert not _matches_allowlist("evil.com", {"pypi.org"})

    def test_substring_does_not_match(self):
        # `evilpypi.org` is NOT a subdomain of `pypi.org`.
        assert not _matches_allowlist("evilpypi.org", {"pypi.org"})

    def test_case_insensitive(self):
        assert _matches_allowlist("PyPI.ORG", {"pypi.org"})

    def test_normalize_strips_brackets(self):
        assert _normalize_host("[::1]") == "::1"


class TestPrivateIpDetection:
    def test_loopback(self):
        assert _is_private_ip("127.0.0.1")

    def test_rfc1918_10(self):
        assert _is_private_ip("10.0.0.1")

    def test_rfc1918_172(self):
        assert _is_private_ip("172.16.5.5")

    def test_rfc1918_192(self):
        assert _is_private_ip("192.168.1.1")

    def test_link_local(self):
        assert _is_private_ip("169.254.169.254")

    def test_public_ipv4_not_private(self):
        assert not _is_private_ip("8.8.8.8")

    def test_hostname_returns_false(self):
        # Hostnames aren't IP literals.
        assert not _is_private_ip("pypi.org")


# --- Proxy server tests (start a real EgressProxy + drive it as a client) ---


async def _send_connect(
    proxy_port: int, target: str
) -> tuple[bytes, asyncio.StreamReader, asyncio.StreamWriter]:
    """Open a TCP connection to the proxy, send a CONNECT, return status bytes."""
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
    writer.write(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
    await writer.drain()

    raw = b""
    while b"\r\n\r\n" not in raw:
        chunk = await asyncio.wait_for(reader.read(1024), timeout=2)
        if not chunk:
            break
        raw += chunk
    return raw, reader, writer


@pytest.mark.asyncio
async def test_disallowed_host_returns_403():
    port = _free_port()
    proxy = EgressProxy(port=port, allowlist={"good.test"})
    await proxy.start()
    try:
        status, _r, w = await _send_connect(port, "evil.com:443")
        w.close()
        assert b"403" in status, status
    finally:
        await proxy.stop()


@pytest.mark.asyncio
async def test_private_ip_literal_returns_403():
    port = _free_port()
    proxy = EgressProxy(port=port, allowlist={"good.test"})
    await proxy.start()
    try:
        status, _r, w = await _send_connect(port, "10.0.0.1:443")
        w.close()
        assert b"403" in status, status
    finally:
        await proxy.stop()


@pytest.mark.asyncio
async def test_loopback_literal_returns_403():
    port = _free_port()
    proxy = EgressProxy(port=port, allowlist={"good.test"})
    await proxy.start()
    try:
        status, _r, w = await _send_connect(port, "127.0.0.1:443")
        w.close()
        assert b"403" in status, status
    finally:
        await proxy.stop()


@pytest.mark.asyncio
async def test_non_connect_method_returns_405():
    port = _free_port()
    proxy = EgressProxy(port=port, allowlist={"good.test"})
    await proxy.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"GET /something HTTP/1.1\r\nHost: pypi.org\r\n\r\n")
        await writer.drain()
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = await asyncio.wait_for(reader.read(1024), timeout=2)
            if not chunk:
                break
            raw += chunk
        writer.close()
        assert b"405" in raw, raw
    finally:
        await proxy.stop()


@pytest.mark.asyncio
async def test_allowed_host_unresolvable_returns_502():
    """Allowlist passes but DNS fails => 502, NOT 403. Confirms the allowlist
    check accepts the host before we try to connect."""
    port = _free_port()
    proxy = EgressProxy(port=port, allowlist={"definitely-not-a-real-tld.test"})
    await proxy.start()
    try:
        status, _r, w = await _send_connect(port, "definitely-not-a-real-tld.test:443")
        w.close()
        # Allowlist passed; resolution failed -> 502
        assert b"502" in status, status
        assert b"403" not in status
    finally:
        await proxy.stop()


@pytest.mark.asyncio
async def test_subdomain_allowed_via_parent(monkeypatch):
    """`a.good.test` should match allowlist entry `good.test` and proceed
    past the allowlist check (resolves to 502 since it doesn't exist)."""
    port = _free_port()
    proxy = EgressProxy(port=port, allowlist={"good.test"})
    await proxy.start()
    try:
        status, _r, w = await _send_connect(port, "a.good.test:443")
        w.close()
        assert b"403" not in status, status
    finally:
        await proxy.stop()


@pytest.mark.asyncio
async def test_tunnel_pipes_bytes_when_allowed(monkeypatch):
    """Successful CONNECT then bidirectional byte pipe.

    We bypass the private-IP guard so we can test the tunnel against a local
    echo server. The allowlist itself is what enforces this in production.
    """
    from src.services.sandbox import egress_proxy as _ep

    monkeypatch.setattr(_ep, "_is_private_ip", lambda host: False)

    # Start a tiny echo server on localhost.
    echo_port = _free_port()
    echo_received: bytearray = bytearray()

    async def echo_handler(reader, writer):
        try:
            data = await asyncio.wait_for(reader.read(64), timeout=2)
            echo_received.extend(data)
            writer.write(data)
            await writer.drain()
        finally:
            writer.close()

    echo_server = await asyncio.start_server(echo_handler, "127.0.0.1", echo_port)

    proxy_port = _free_port()
    proxy = EgressProxy(port=proxy_port, allowlist={"127.0.0.1"})
    await proxy.start()
    try:
        status, reader, writer = await _send_connect(
            proxy_port, f"127.0.0.1:{echo_port}"
        )
        assert b"200" in status, status

        writer.write(b"ping\n")
        await writer.drain()

        echoed = await asyncio.wait_for(reader.read(64), timeout=2)
        assert echoed == b"ping\n"
        writer.close()
    finally:
        await proxy.stop()
        echo_server.close()
        await echo_server.wait_closed()
