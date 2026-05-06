"""Local smoke tests for docker/ptc_bash_server.py.

These are unit-level tests that run the bash PTC server as a subprocess
on the host (with PTC_BASH_DIR pointed at a temp dir). They verify the
end-to-end protocol — bash code calls the generated wrapper functions,
the server forwards them as `tool_calls` on its outer stdout, the test
sends `tool_results` back on stdin, and bash receives and prints the
result.

Skipped automatically when `bash` or `jq` aren't on PATH.
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

_PTC_BASH_PATH = (
    Path(__file__).resolve().parent.parent.parent / "docker" / "ptc_bash_server.py"
)

_REQUIRED = ("bash", "jq")
_missing = [b for b in _REQUIRED if shutil.which(b) is None]
pytestmark = pytest.mark.skipif(
    bool(_missing), reason=f"Missing required binaries: {_missing}"
)

DELIMITER = "\n---PTC_END---\n"


def _run_bash_ptc(
    code: str,
    tools: list,
    tool_responder,
    tmp_path: Path,
    timeout: float = 15.0,
) -> dict:
    """Spawn ptc_bash_server.py and drive its protocol from this process.

    `tool_responder(call)` is invoked for each tool_call message and must
    return the JSON-serializable value to send back as the result.
    """
    env = os.environ.copy()
    env["PTC_BASH_DIR"] = str(tmp_path)

    proc = subprocess.Popen(
        [sys.executable, str(_PTC_BASH_PATH)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    initial = json.dumps({"code": code, "tools": tools}) + DELIMITER
    assert proc.stdin is not None
    proc.stdin.write(initial.encode())
    proc.stdin.flush()

    deadline = time.monotonic() + timeout
    out_buf = b""
    final_message: dict = {}

    try:
        while time.monotonic() < deadline:
            assert proc.stdout is not None
            chunk = proc.stdout.read1(4096)
            if not chunk:
                if proc.poll() is not None:
                    break
                continue
            out_buf += chunk

            while DELIMITER.encode() in out_buf:
                msg_bytes, _, rest = out_buf.partition(DELIMITER.encode())
                out_buf = rest
                msg = json.loads(msg_bytes.decode())

                if msg.get("type") == "tool_calls":
                    results = []
                    for call in msg["calls"]:
                        try:
                            value = tool_responder(call)
                            results.append(
                                {
                                    "call_id": call["id"],
                                    "result": value,
                                    "is_error": False,
                                }
                            )
                        except Exception as exc:
                            results.append(
                                {
                                    "call_id": call["id"],
                                    "result": None,
                                    "is_error": True,
                                    "error_message": str(exc),
                                }
                            )
                    response = (
                        json.dumps({"type": "tool_results", "results": results})
                        + DELIMITER
                    )
                    proc.stdin.write(response.encode())
                    proc.stdin.flush()
                else:
                    final_message = msg
                    break

            if final_message:
                break

        if not final_message:
            proc.kill()
            proc.wait(timeout=2)
            raise AssertionError(
                "ptc_bash_server did not return a final message before timeout. "
                f"stderr: {proc.stderr.read().decode(errors='replace') if proc.stderr else ''}"
            )
    finally:
        try:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=2)
        except Exception:
            pass

    return final_message


def test_no_tools_simple_echo(tmp_path):
    """Bash code that doesn't call any tool should complete with stdout."""
    result = _run_bash_ptc(
        code='echo "hello world"',
        tools=[],
        tool_responder=lambda call: None,
        tmp_path=tmp_path,
    )
    assert result["type"] == "completed"
    assert "hello world" in result["stdout"]


def test_single_tool_call_round_trip(tmp_path):
    """Bash function calls a tool, prints the JSON result."""
    code = """
result=$(weather_lookup '{"city":"NYC"}')
echo "got: $result"
"""
    calls_seen = []

    def responder(call):
        calls_seen.append(call)
        # Return a JSON-serializable value
        return {"temp": 72, "condition": "sunny"}

    tools = [{"name": "weather_lookup", "description": "Get weather"}]
    result = _run_bash_ptc(
        code=code, tools=tools, tool_responder=responder, tmp_path=tmp_path
    )

    assert result["type"] == "completed"
    assert len(calls_seen) == 1
    assert calls_seen[0]["name"] == "weather_lookup"
    assert calls_seen[0]["input"] == {"city": "NYC"}
    # The bash code echoed the JSON result
    assert '"temp"' in result["stdout"]
    assert '"sunny"' in result["stdout"]


def test_multiple_sequential_tool_calls(tmp_path):
    """Bash calls two different tools sequentially; both round-trip cleanly."""
    code = """
a=$(get_temperature '{"city":"NYC"}')
b=$(get_humidity '{"city":"NYC"}')
echo "T=$a"
echo "H=$b"
"""
    counter = {"i": 0}

    def responder(call):
        counter["i"] += 1
        if call["name"] == "get_temperature":
            return 72
        return 50

    tools = [
        {"name": "get_temperature", "description": "Temp"},
        {"name": "get_humidity", "description": "Humid"},
    ]
    result = _run_bash_ptc(
        code=code, tools=tools, tool_responder=responder, tmp_path=tmp_path
    )

    assert result["type"] == "completed"
    assert counter["i"] == 2
    assert "T=72" in result["stdout"]
    assert "H=50" in result["stdout"]


def test_bash_nonzero_exit_returns_error(tmp_path):
    """Bash code with `exit 7` should yield status=error with stderr."""
    code = "echo before; exit 7"
    result = _run_bash_ptc(
        code=code, tools=[], tool_responder=lambda c: None, tmp_path=tmp_path
    )
    assert result["type"] == "error"
    assert "exited with code 7" in result["error"]
    assert "before" in result["stdout"]


def test_invalid_tool_name_not_wrapped(tmp_path):
    """Tools whose names aren't valid bash identifiers are silently skipped.

    We assert the bash code can't call them — bash reports 'command not found'
    on stderr but the script still completes (exit 127 since the last command
    failed).
    """
    code = "weird-name '{}'"
    tools = [{"name": "weird-name", "description": "Has a hyphen"}]
    result = _run_bash_ptc(
        code=code, tools=tools, tool_responder=lambda c: None, tmp_path=tmp_path
    )
    # Bash exits non-zero because the function isn't defined.
    assert result["type"] == "error"
    assert (
        "command not found" in result["stderr"].lower()
        or "not found" in result["stderr"].lower()
    )
