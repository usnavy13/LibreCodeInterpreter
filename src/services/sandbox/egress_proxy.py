"""HTTP CONNECT proxy with hostname allowlist for sandbox egress.

Runs as an asyncio task inside the API process. Sandboxes that have
network access enabled get `HTTPS_PROXY=http://127.0.0.1:<port>` injected
into their env; pip, npm, go, cargo all honor that variable. The proxy:

- Only handles `CONNECT host:port HTTP/1.1` (HTTPS tunneling). The proxy
  never sees the encrypted body — TLS terminates between the sandbox and
  the upstream. Allowlist enforcement happens on the requested host name.
- Refuses to open tunnels to private IP ranges (RFC 1918, loopback, link-local)
  even if a public hostname resolves to one. This stops trivial SSRF against
  Redis/MinIO/etc. on the same docker network.
- Refuses any request whose host doesn't match the allowlist.

Allowlist defaults cover Python (PyPI), Node (npmjs), Go modules, and
Rust crates so `pip install`, `npm install`, `go get`, `cargo add` work
out of the box. Add more via SANDBOX_EGRESS_ALLOWLIST=host1,host2.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import Iterable, Optional, Set

import structlog

logger = structlog.get_logger(__name__)


# Defaults cover the major package ecosystems for skills. Operators can
# extend via SANDBOX_EGRESS_ALLOWLIST. Subdomains are matched as suffixes
# (e.g., `pypi.org` permits `files.pypi.org`).
DEFAULT_ALLOWLIST: tuple[str, ...] = (
    # Python (PyPI)
    "pypi.org",
    "files.pythonhosted.org",
    "pythonhosted.org",
    # Node (npm + npx)
    "registry.npmjs.org",
    "registry.npmjs.com",
    "npmjs.org",
    "npmjs.com",
    # Go modules
    "proxy.golang.org",
    "sum.golang.org",
    "golang.org",
    # Rust crates
    "crates.io",
    "static.crates.io",
    "index.crates.io",
)


def _normalize_host(host: str) -> str:
    """Lowercase and strip an optional surrounding `[ipv6]` notation."""
    host = host.strip().lower()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return host


def _is_private_ip(host: str) -> bool:
    """True if `host` is an IP literal that's loopback, private, link-local, or otherwise non-public."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _matches_allowlist(host: str, allowlist: Set[str]) -> bool:
    """True if `host` exactly matches an entry or is a subdomain of one."""
    host = _normalize_host(host)
    if host in allowlist:
        return True
    # Subdomain match: `files.pypi.org` is allowed when `pypi.org` is in the list.
    return any(host.endswith("." + entry) for entry in allowlist)


async def _resolve_first_addr(host: str, port: int) -> Optional[tuple[str, int]]:
    """Resolve `host` once and return the first concrete (ip, port) pair.

    We resolve here instead of letting `asyncio.open_connection` do it so we can
    reject the tunnel early if the host resolves only to private IPs. Returns
    None if the host fails to resolve or all addresses are private.
    """
    loop = asyncio.get_event_loop()
    try:
        infos = await loop.getaddrinfo(
            host,
            port,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except (socket.gaierror, OSError):
        return None
    for family, _stype, _proto, _canon, sockaddr in infos:
        ip = sockaddr[0]
        if _is_private_ip(ip):
            continue
        return ip, port
    return None


async def _pipe(
    src: asyncio.StreamReader,
    dst: asyncio.StreamWriter,
) -> None:
    """Copy bytes from `src` to `dst` until EOF or write failure."""
    try:
        while True:
            chunk = await src.read(65536)
            if not chunk:
                break
            dst.write(chunk)
            await dst.drain()
    except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
        pass
    finally:
        try:
            dst.close()
        except Exception:
            pass


class EgressProxy:
    """An asyncio CONNECT proxy with hostname allowlist enforcement.

    Bind to 127.0.0.1 only — sandboxes share the host network namespace
    when network access is enabled, so 127.0.0.1 is reachable from inside.
    No external listener.
    """

    def __init__(
        self,
        port: int,
        allowlist: Iterable[str] = DEFAULT_ALLOWLIST,
        bind_host: str = "127.0.0.1",
    ):
        self.port = port
        self.bind_host = bind_host
        self.allowlist: Set[str] = {h.strip().lower() for h in allowlist if h.strip()}
        self._server: Optional[asyncio.base_events.Server] = None
        self._serve_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if self._server is not None:
            return
        self._server = await asyncio.start_server(
            self._handle_client,
            host=self.bind_host,
            port=self.port,
        )
        self._serve_task = asyncio.create_task(self._server.serve_forever())
        logger.info(
            "Sandbox egress proxy started",
            bind=f"{self.bind_host}:{self.port}",
            allowlist_size=len(self.allowlist),
        )

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        try:
            await self._server.wait_closed()
        except Exception:
            pass
        if self._serve_task is not None:
            self._serve_task.cancel()
            try:
                await self._serve_task
            except (asyncio.CancelledError, Exception):
                pass
        self._server = None
        self._serve_task = None
        logger.info("Sandbox egress proxy stopped")

    async def _handle_client(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        peer = client_writer.get_extra_info("peername")
        try:
            request_line = await asyncio.wait_for(client_reader.readline(), timeout=5)
        except (asyncio.TimeoutError, ConnectionError):
            client_writer.close()
            return

        if not request_line:
            client_writer.close()
            return

        # Drain headers (we don't act on them, but clients send them).
        try:
            while True:
                line = await asyncio.wait_for(client_reader.readline(), timeout=5)
                if not line or line == b"\r\n":
                    break
        except asyncio.TimeoutError:
            await self._reply_and_close(client_writer, 408, "Request Timeout")
            return

        method, _, target = request_line.decode("latin-1", errors="replace").partition(
            " "
        )
        method = method.upper()
        if method != "CONNECT":
            # Plain HTTP proxying isn't supported (and shouldn't be needed —
            # pip etc. all use HTTPS). Reject with a clear status.
            logger.warning(
                "Egress proxy refused non-CONNECT request",
                method=method,
                peer=peer,
            )
            await self._reply_and_close(client_writer, 405, "Method Not Allowed")
            return

        target_host_port = target.split(" ", 1)[0]
        host, _, port_str = target_host_port.rpartition(":")
        try:
            port = int(port_str)
        except ValueError:
            await self._reply_and_close(client_writer, 400, "Bad Request")
            return
        host = _normalize_host(host)

        # Allowlist check on the host *before* we resolve it, so audit logs show
        # the requested host even when DNS would have failed.
        if _is_private_ip(host):
            logger.warning(
                "Egress proxy refused private IP literal", host=host, peer=peer
            )
            await self._reply_and_close(client_writer, 403, "Forbidden")
            return
        if not _matches_allowlist(host, self.allowlist):
            logger.warning(
                "Egress proxy refused non-allowlisted host", host=host, peer=peer
            )
            await self._reply_and_close(client_writer, 403, "Forbidden")
            return

        # Resolve and reject if it only points at private space.
        resolved = await _resolve_first_addr(host, port)
        if resolved is None:
            logger.warning(
                "Egress proxy could not resolve host to public address",
                host=host,
                peer=peer,
            )
            await self._reply_and_close(client_writer, 502, "Bad Gateway")
            return

        ip, _ = resolved
        try:
            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(ip, port),
                timeout=10,
            )
        except (asyncio.TimeoutError, OSError) as e:
            logger.warning(
                "Egress proxy upstream connect failed",
                host=host,
                ip=ip,
                error=str(e),
            )
            await self._reply_and_close(client_writer, 502, "Bad Gateway")
            return

        logger.debug(
            "Egress proxy tunnel opened",
            host=host,
            ip=ip,
            port=port,
            peer=peer,
        )

        client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        try:
            await client_writer.drain()
        except ConnectionError:
            upstream_writer.close()
            return

        await asyncio.gather(
            _pipe(client_reader, upstream_writer),
            _pipe(upstream_reader, client_writer),
            return_exceptions=True,
        )

    @staticmethod
    async def _reply_and_close(
        writer: asyncio.StreamWriter, status: int, reason: str
    ) -> None:
        try:
            writer.write(f"HTTP/1.1 {status} {reason}\r\n\r\n".encode("ascii"))
            await writer.drain()
        except (ConnectionError, RuntimeError):
            pass
        try:
            writer.close()
        except Exception:
            pass
