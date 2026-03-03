#!/usr/bin/env python3
"""Programmatic Tool Calling (PTC) Server for nsjail sandbox execution.

This script runs INSIDE the nsjail sandbox and provides a Python execution
environment where code can call externally-defined tools. Tool calls are
serialized as JSON over stdin/stdout, allowing the host process to fulfill
them and send results back.

Protocol:
1. Host sends initial request via stdin:
   {"code": "...", "tools": [{"name": "...", "description": "...", "parameters": {...}}]}

2. Code executes. When a tool stub is called, PTC server writes to stdout:
   {"type": "tool_calls", "calls": [{"id": "...", "name": "...", "input": {...}}]}

3. Host reads tool_calls, fulfills them, and writes results to stdin:
   {"type": "tool_results", "results": [{"call_id": "...", "result": ..., "is_error": false}]}

4. Code continues. On completion, PTC server writes:
   {"type": "completed", "stdout": "...", "stderr": "..."}

5. On error, PTC server writes:
   {"type": "error", "error": "..."}
"""

import asyncio
import json
import os
import sys
import traceback
import uuid
from io import StringIO

DELIMITER = "\n---PTC_END---\n"

# Keep references to the REAL stdin/stdout for protocol communication.
# User code's print() will be redirected to a StringIO capture buffer.
_real_stdin = sys.stdin
_real_stdout = sys.stdout
_real_stderr = sys.stderr


def _write_message(msg: dict) -> None:
    """Write a JSON message to the host via the real stdout."""
    data = json.dumps(msg) + DELIMITER
    _real_stdout.write(data)
    _real_stdout.flush()


def _read_message() -> dict:
    """Read a JSON message from the host via the real stdin."""
    buf = ""
    while True:
        line = _real_stdin.readline()
        if not line:
            raise EOFError("stdin closed")
        buf += line
        if DELIMITER in buf:
            json_part = buf.split(DELIMITER)[0]
            return json.loads(json_part)


# Pending tool calls collected during async execution
_pending_calls = []
_tool_results_map = {}  # call_id -> result


def _make_tool_stub(tool_name: str) -> callable:
    """Create an async function stub for a tool."""

    async def tool_stub(**kwargs):
        call_id = uuid.uuid4().hex[:12]
        call_info = {
            "id": call_id,
            "name": tool_name,
            "input": kwargs,
        }
        _pending_calls.append(call_info)

        # Wait for result - the main loop will flush calls and read results
        while call_id not in _tool_results_map:
            await asyncio.sleep(0.01)

        result_info = _tool_results_map.pop(call_id)
        if result_info.get("is_error"):
            raise RuntimeError(
                result_info.get("error_message", "Tool call failed")
            )
        return result_info.get("result")

    tool_stub.__name__ = tool_name
    tool_stub.__qualname__ = tool_name
    return tool_stub


async def _execute_with_tools(
    code: str, tools: list, user_stdout: StringIO, user_stderr: StringIO
) -> dict:
    """Execute code with tool stubs, capturing user output."""
    global _pending_calls, _tool_results_map

    _pending_calls = []
    _tool_results_map = {}

    # Build namespace with tool stubs
    namespace = {"__builtins__": __builtins__, "__name__": "__main__"}

    try:
        import json as _json

        namespace["json"] = _json
    except ImportError:
        pass

    for tool in tools:
        namespace[tool["name"]] = _make_tool_stub(tool["name"])

    # Wrap user code in async function
    indented_code = "\n".join("    " + line for line in code.split("\n"))
    wrapped_code = f"async def __ptc_main__():\n{indented_code}\n"

    try:
        compiled = compile(wrapped_code, "<ptc_code>", "exec")
        exec(compiled, namespace)
    except SyntaxError as e:
        return {"type": "error", "error": f"SyntaxError: {e}"}

    main_func = namespace["__ptc_main__"]
    main_task = asyncio.ensure_future(main_func())

    try:
        while not main_task.done():
            # Let the task run briefly to accumulate batched calls
            await asyncio.sleep(0.05)

            if _pending_calls and not main_task.done():
                calls_to_send = list(_pending_calls)
                _pending_calls.clear()

                _write_message({
                    "type": "tool_calls",
                    "calls": calls_to_send,
                })

                # Wait for results from host
                response = _read_message()

                if response.get("type") != "tool_results":
                    return {
                        "type": "error",
                        "error": f"Expected tool_results, got "
                        f"{response.get('type')}",
                    }

                for result in response.get("results", []):
                    _tool_results_map[result["call_id"]] = result

        # Task completed
        main_task.result()
        return {"type": "completed"}

    except Exception as e:
        tb = traceback.format_exc()
        return {
            "type": "error",
            "error": str(e),
            "stderr_extra": tb,
        }


def main():
    """Main entry point for PTC server."""
    try:
        os.chdir("/mnt/data")
    except OSError:
        pass

    # Read initial request
    try:
        request = _read_message()
    except Exception as e:
        _write_message({
            "type": "error",
            "error": f"Failed to read initial request: {e}",
        })
        return

    code = request.get("code", "")
    tools = request.get("tools", [])

    if not code:
        _write_message({"type": "error", "error": "No code provided"})
        return

    # Redirect sys.stdout and sys.stderr so user print() calls
    # are captured, not mixed with our protocol messages.
    user_stdout = StringIO()
    user_stderr = StringIO()
    sys.stdout = user_stdout
    sys.stderr = user_stderr

    try:
        result = asyncio.run(
            _execute_with_tools(code, tools, user_stdout, user_stderr)
        )
    except Exception as e:
        result = {
            "type": "error",
            "error": str(e),
        }

    # Restore real stdout for final message
    sys.stdout = _real_stdout
    sys.stderr = _real_stderr

    # Attach captured user output
    result["stdout"] = user_stdout.getvalue()
    stderr_val = user_stderr.getvalue()
    if result.get("stderr_extra"):
        stderr_val += result.pop("stderr_extra")
    result["stderr"] = stderr_val

    _write_message(result)


if __name__ == "__main__":
    main()
