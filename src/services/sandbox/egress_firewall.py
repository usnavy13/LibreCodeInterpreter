"""iptables egress firewall for the sandbox uid.

Without this, enabling ENABLE_SANDBOX_NETWORK shares the API container's
network namespace with sandbox processes, which gives them direct access to
internal services like Redis/S3 on the docker bridge — full SSRF.

The hostname-allowlist proxy only protects HTTPS_PROXY-aware clients
(pip, npm, requests with proxy support). Raw socket calls — `socket.create_connection`,
direct TCP from a malicious skill — bypass the proxy entirely.

This module installs iptables OUTPUT rules that match on the sandbox uid:
  - ALLOW the sandbox uid → 127.0.0.1:<proxy_port>  (so pip etc. work)
  - ALLOW the sandbox uid → 127.0.0.53:53 (DNS via systemd-resolved)
  - REJECT everything else from the sandbox uid

The API process itself runs as root (uid 0), so the proxy's own outbound
traffic to PyPI/npm/etc. is unaffected by these rules.

Requires the container to have CAP_NET_ADMIN. If iptables fails (missing
binary, missing capability) we log a clear error and refuse to enable
network access — better to break loud than silently leak SSRF.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import List

import structlog

logger = structlog.get_logger(__name__)


# Marker comment so we can find and remove our own rules without disturbing
# anyone else's. iptables --comment is supported by every modern build.
_RULE_COMMENT = "code-interpreter-sandbox-egress"


def _run_iptables(args: List[str]) -> tuple[int, str]:
    """Run an iptables command. Returns (exit_code, combined_output)."""
    iptables = shutil.which("iptables")
    if iptables is None:
        return 127, "iptables binary not found"
    try:
        proc = subprocess.run(
            [iptables, *args],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        return 1, "iptables timed out"
    except OSError as exc:
        return 1, f"iptables failed to start: {exc}"
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out.strip()


def remove_existing_rules() -> None:
    """Idempotent cleanup of any rules left over from a previous run.

    iptables doesn't have a 'remove all rules matching comment X' verb, so
    we list the OUTPUT chain and delete one rule at a time by line number.
    Doing this in a loop because line numbers shift after each delete.
    """
    while True:
        code, out = _run_iptables(["-L", "OUTPUT", "--line-numbers", "-n"])
        if code != 0:
            return
        target_line: int | None = None
        for line in out.splitlines():
            if _RULE_COMMENT in line:
                first = line.split(None, 1)[0]
                try:
                    target_line = int(first)
                except ValueError:
                    continue
                break
        if target_line is None:
            return
        _run_iptables(["-D", "OUTPUT", str(target_line)])


def install_sandbox_egress_rules(sandbox_uid: int, proxy_port: int) -> bool:
    """Install iptables rules so the sandbox uid can only reach the proxy.

    Returns True on success, False if iptables isn't available or the rules
    couldn't be installed (e.g., missing CAP_NET_ADMIN).
    """
    # Clean up any rules we might have left from a previous start.
    remove_existing_rules()

    # Order matters: ACCEPT rules must come before the catch-all DROP.
    rules: List[List[str]] = [
        # Allow the sandbox uid to talk to the proxy on loopback.
        [
            "-A",
            "OUTPUT",
            "-m",
            "owner",
            "--uid-owner",
            str(sandbox_uid),
            "-d",
            "127.0.0.1",
            "-p",
            "tcp",
            "--dport",
            str(proxy_port),
            "-m",
            "comment",
            "--comment",
            _RULE_COMMENT,
            "-j",
            "ACCEPT",
        ],
        # Allow DNS to systemd-resolved on loopback (some tools resolve
        # before handing the CONNECT to the proxy).
        [
            "-A",
            "OUTPUT",
            "-m",
            "owner",
            "--uid-owner",
            str(sandbox_uid),
            "-d",
            "127.0.0.53",
            "-p",
            "udp",
            "--dport",
            "53",
            "-m",
            "comment",
            "--comment",
            _RULE_COMMENT,
            "-j",
            "ACCEPT",
        ],
        [
            "-A",
            "OUTPUT",
            "-m",
            "owner",
            "--uid-owner",
            str(sandbox_uid),
            "-d",
            "127.0.0.53",
            "-p",
            "tcp",
            "--dport",
            "53",
            "-m",
            "comment",
            "--comment",
            _RULE_COMMENT,
            "-j",
            "ACCEPT",
        ],
        # Drop everything else from the sandbox uid. This is what blocks
        # direct connections to Redis/S3/internet.
        [
            "-A",
            "OUTPUT",
            "-m",
            "owner",
            "--uid-owner",
            str(sandbox_uid),
            "-m",
            "comment",
            "--comment",
            _RULE_COMMENT,
            "-j",
            "REJECT",
            "--reject-with",
            "icmp-net-unreachable",
        ],
    ]

    for rule in rules:
        code, out = _run_iptables(rule)
        if code != 0:
            logger.error(
                "Failed to install sandbox egress firewall rule; "
                "ROLLING BACK to avoid leaving the rule chain in a partial state",
                rule=rule,
                code=code,
                output=out,
            )
            remove_existing_rules()
            return False

    logger.info(
        "Sandbox egress firewall installed",
        sandbox_uid=sandbox_uid,
        proxy_port=proxy_port,
    )
    return True
