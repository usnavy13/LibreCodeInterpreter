#!/usr/bin/env python3
"""REPL Server for pre-warmed Python execution.

This script runs inside the container and provides a persistent Python interpreter
that eliminates interpreter startup time for each execution.

Protocol:
- Reads JSON requests from stdin until delimiter
- Executes code in isolated namespace
- Returns JSON response with stdout/stderr/exit_code
- Loops forever, handling one request at a time

Request format:
    {"code": "print('hello')", "timeout": 30, "working_dir": "/mnt/data"}
    ---END---

Response format:
    {"exit_code": 0, "stdout": "hello\\n", "stderr": "", "execution_time_ms": 5}
    ---END---
"""

import sys
import os
import json
import signal
import traceback
import time
import base64
from io import StringIO
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime
from types import ModuleType

# Import cloudpickle for state serialization
try:
    import cloudpickle
    CLOUDPICKLE_AVAILABLE = True
except ImportError:
    CLOUDPICKLE_AVAILABLE = False

# Import lz4 for state compression
try:
    import lz4.frame
    LZ4_AVAILABLE = True
except ImportError:
    LZ4_AVAILABLE = False

# State format version (v2 = lz4 compressed)
STATE_VERSION_UNCOMPRESSED = 1
STATE_VERSION_LZ4 = 2
STATE_VERSION_HEADER_SIZE = 1  # 1 byte version prefix

# Delimiter for message framing
DELIMITER = "\n---END---\n"

# Pre-import common libraries at startup to amortize import cost
# These imports happen once when the container starts, not per-execution
PRELOADED_MODULES = {}

# State persistence configuration
MAX_STATE_SIZE_BYTES = 50 * 1024 * 1024  # 50MB max state size

# Keys to exclude from state serialization
EXCLUDED_KEYS = {
    '__builtins__', '__name__', '__doc__', '__package__',
    '__loader__', '__spec__', '__annotations__', '__cached__',
    '__file__', '__warningregistry__',
}


def preload_libraries():
    """Pre-import common libraries to eliminate per-execution import overhead."""
    libraries = [
        # Core data science
        ("numpy", "np"),
        ("pandas", "pd"),

        # Visualization
        ("matplotlib", None),
        ("matplotlib.pyplot", "plt"),

        # Scientific computing
        ("scipy", None),
        ("scipy.stats", None),
        ("scipy.optimize", None),

        # Machine learning
        ("sklearn", None),
        ("sklearn.linear_model", None),
        ("sklearn.datasets", None),

        # Image processing
        ("cv2", None),
        ("PIL", None),
        ("PIL.Image", "Image"),

        # Symbolic math
        ("sympy", "sp"),

        # Other common packages
        ("networkx", "nx"),
        ("statsmodels", None),
        ("statsmodels.api", "sm"),

        # Standard library (fast, but preload anyway)
        ("json", None),
        ("os", None),
        ("sys", None),
        ("re", None),
        ("math", None),
        ("datetime", None),
        ("collections", None),
        ("itertools", None),
        ("functools", None),
        ("pathlib", None),
    ]

    loaded_count = 0
    for module_name, alias in libraries:
        try:
            module = __import__(module_name.split('.')[0])
            # Handle submodules
            for part in module_name.split('.')[1:]:
                module = getattr(module, part)

            PRELOADED_MODULES[module_name] = module
            if alias:
                PRELOADED_MODULES[alias] = module
            loaded_count += 1
        except ImportError as e:
            # Some packages may not be installed, that's OK
            pass
        except Exception as e:
            pass

    # Configure matplotlib for non-interactive backend
    try:
        import matplotlib
        matplotlib.use('Agg')
    except:
        pass

    return loaded_count


class TimeoutError(Exception):
    """Raised when execution times out."""
    pass


def timeout_handler(signum, frame):
    """Signal handler for execution timeout."""
    raise TimeoutError("Execution timed out")


def deserialize_state(state_b64: str) -> dict:
    """Deserialize base64-encoded cloudpickle state (with optional lz4 compression).

    Args:
        state_b64: Base64-encoded pickled state (may be lz4 compressed)

    Returns:
        Dictionary of variable name -> value

    Raises:
        ValueError: If state is invalid, too large, or cloudpickle unavailable
    """
    if not state_b64:
        return {}

    if not CLOUDPICKLE_AVAILABLE:
        raise ValueError("cloudpickle not available for state deserialization")

    try:
        state_bytes = base64.b64decode(state_b64)
        if len(state_bytes) > MAX_STATE_SIZE_BYTES:
            raise ValueError(f"State too large: {len(state_bytes)} bytes (max {MAX_STATE_SIZE_BYTES})")

        # Check version header
        if len(state_bytes) >= STATE_VERSION_HEADER_SIZE:
            version = state_bytes[0]
            payload = state_bytes[STATE_VERSION_HEADER_SIZE:]

            if version == STATE_VERSION_LZ4:
                # Decompress lz4
                if not LZ4_AVAILABLE:
                    raise ValueError("lz4 not available but state is lz4 compressed")
                decompressed = lz4.frame.decompress(payload)
                return cloudpickle.loads(decompressed)
            elif version == STATE_VERSION_UNCOMPRESSED:
                # Uncompressed v1 format
                return cloudpickle.loads(payload)

        # Fallback: try to load as raw cloudpickle (for backward compatibility)
        return cloudpickle.loads(state_bytes)
    except Exception as e:
        raise ValueError(f"Failed to deserialize state: {e}")


def serialize_state(namespace: dict) -> tuple:
    """Serialize namespace to base64-encoded cloudpickle with lz4 compression.

    Attempts to serialize all user-defined variables, skipping
    those that fail (with warnings). Uses lz4 compression for smaller state size.

    Args:
        namespace: Execution namespace dictionary

    Returns:
        Tuple of (base64_state or None, list of warning messages)
    """
    if not CLOUDPICKLE_AVAILABLE:
        return None, ["cloudpickle not available for state serialization"]

    errors = []
    serializable = {}

    # Build set of excluded keys including preloaded module names
    excluded = EXCLUDED_KEYS | set(PRELOADED_MODULES.keys())

    for key, value in namespace.items():
        # Skip internal and preloaded keys
        if key.startswith('_') or key in excluded:
            continue

        # Skip modules (they're already preloaded or should be imported fresh)
        if isinstance(value, ModuleType):
            continue

        try:
            # Test if value is serializable
            _ = cloudpickle.dumps(value)
            serializable[key] = value
        except Exception as e:
            error_msg = str(e)[:100]  # Truncate long error messages
            errors.append(f"Cannot serialize '{key}' ({type(value).__name__}): {error_msg}")

    if not serializable:
        return None, errors

    try:
        # Serialize with cloudpickle
        pickled_bytes = cloudpickle.dumps(serializable)

        # Compress with lz4 if available, otherwise use uncompressed
        if LZ4_AVAILABLE:
            compressed = lz4.frame.compress(pickled_bytes, compression_level=0)  # Fast compression
            version = STATE_VERSION_LZ4
            payload = compressed
        else:
            version = STATE_VERSION_UNCOMPRESSED
            payload = pickled_bytes

        # Prepend version byte
        state_bytes = bytes([version]) + payload

        if len(state_bytes) > MAX_STATE_SIZE_BYTES:
            return None, [f"Serialized state too large: {len(state_bytes)} bytes (max {MAX_STATE_SIZE_BYTES})"]

        return base64.b64encode(state_bytes).decode('ascii'), errors
    except Exception as e:
        return None, [f"Failed to serialize state: {e}"]


def create_execution_namespace(initial_state: dict = None):
    """Create a fresh namespace for code execution with preloaded modules.

    Args:
        initial_state: Optional dict of variables to restore from previous execution

    Returns:
        Namespace dict ready for exec()
    """
    namespace = {
        '__builtins__': __builtins__,
        '__name__': '__main__',
    }

    # Add preloaded modules to namespace
    namespace.update(PRELOADED_MODULES)

    # Restore previous state if provided
    if initial_state:
        namespace.update(initial_state)

    return namespace


def execute_code(
    code: str,
    timeout: int = 30,
    working_dir: str = "/mnt/data",
    initial_state: str = None,
    capture_state: bool = False,
    args: list = None
) -> dict:
    """Execute code in isolated namespace and capture output.

    Args:
        code: Python code to execute
        timeout: Maximum execution time in seconds
        working_dir: Working directory for execution
        initial_state: Base64-encoded cloudpickle state to restore before execution
        capture_state: Whether to capture and return state after execution
        args: Optional list of command line arguments

    Returns:
        Dict with exit_code, stdout, stderr, execution_time_ms, and optionally state/state_errors
    """
    start_time = time.perf_counter()
    state_errors = []
    namespace = None

    # Deserialize initial state if provided
    restored_state = None
    if initial_state:
        try:
            restored_state = deserialize_state(initial_state)
        except ValueError as e:
            state_errors.append(str(e))

    # Change to working directory
    original_dir = os.getcwd()
    try:
        os.chdir(working_dir)
    except:
        pass

    # Set up output capture
    stdout_capture = StringIO()
    stderr_capture = StringIO()

    exit_code = 0

    # Save and set sys.argv if args provided
    original_argv = sys.argv
    if args is not None:
        # Set sys.argv to [script_name] + args (matches file-based execution)
        sys.argv = ['/mnt/data/code.py'] + list(args)

    # Set up timeout handler
    old_handler = signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(timeout)

    try:
        # Create namespace with restored state
        namespace = create_execution_namespace(restored_state)

        # Compile and execute
        with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
            try:
                # First try to compile as expression (for simple evaluations)
                compiled = compile(code, '<code>', 'eval')
                result = eval(compiled, namespace)
                if result is not None:
                    print(repr(result))
            except SyntaxError:
                # Not an expression, execute as statements
                compiled = compile(code, '<code>', 'exec')
                exec(compiled, namespace)

    except TimeoutError as e:
        exit_code = 124  # Standard timeout exit code
        stderr_capture.write(f"TimeoutError: Execution exceeded {timeout} seconds\n")

    except SyntaxError as e:
        exit_code = 1
        stderr_capture.write(f"SyntaxError: {e}\n")

    except Exception as e:
        exit_code = 1
        # Capture full traceback
        tb = traceback.format_exc()
        stderr_capture.write(tb)

    finally:
        # Cancel timeout
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)

        # Restore sys.argv
        sys.argv = original_argv

        # Restore working directory
        try:
            os.chdir(original_dir)
        except:
            pass

        # Clean up matplotlib figures to prevent memory leaks
        try:
            import matplotlib.pyplot as plt
            plt.close('all')
        except:
            pass

    execution_time_ms = int((time.perf_counter() - start_time) * 1000)

    result = {
        "exit_code": exit_code,
        "stdout": stdout_capture.getvalue(),
        "stderr": stderr_capture.getvalue(),
        "execution_time_ms": execution_time_ms,
    }

    # Capture state if requested and execution succeeded (or namespace is available)
    if capture_state and namespace is not None:
        state_b64, serialize_errors = serialize_state(namespace)
        if state_b64:
            result["state"] = state_b64
        state_errors.extend(serialize_errors)

    if state_errors:
        result["state_errors"] = state_errors

    return result


def read_request() -> dict:
    """Read a JSON request from stdin until delimiter.

    Returns:
        Parsed JSON request dict, or None if EOF
    """
    buffer = ""

    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                # EOF
                return None

            buffer += line

            # Check for delimiter
            if DELIMITER in buffer:
                json_part = buffer.split(DELIMITER)[0]
                try:
                    return json.loads(json_part)
                except json.JSONDecodeError as e:
                    # Return error response
                    return {"error": f"Invalid JSON: {e}"}

        except Exception as e:
            return {"error": f"Read error: {e}"}


def write_response(response: dict):
    """Write a JSON response to stdout with delimiter."""
    try:
        json_str = json.dumps(response)
        sys.stdout.write(json_str)
        sys.stdout.write(DELIMITER)
        sys.stdout.flush()
    except Exception as e:
        # Emergency fallback
        sys.stdout.write(json.dumps({
            "exit_code": 1,
            "stdout": "",
            "stderr": f"Response encoding error: {e}",
            "execution_time_ms": 0
        }))
        sys.stdout.write(DELIMITER)
        sys.stdout.flush()


def send_ready_signal():
    """Send a ready signal to indicate REPL is initialized."""
    response = {
        "status": "ready",
        "preloaded_modules": len(PRELOADED_MODULES),
        "python_version": sys.version,
        "working_dir": os.getcwd(),
    }
    write_response(response)


def main():
    """Main REPL loop."""
    # Change to working directory
    try:
        os.chdir("/mnt/data")
    except:
        pass

    # Pre-load libraries
    loaded_count = preload_libraries()

    # Send ready signal
    send_ready_signal()

    # Main loop
    while True:
        try:
            request = read_request()

            if request is None:
                # EOF, exit gracefully
                break

            if "error" in request:
                write_response({
                    "exit_code": 1,
                    "stdout": "",
                    "stderr": request["error"],
                    "execution_time_ms": 0
                })
                continue

            # Extract request parameters
            code = request.get("code", "")
            timeout = request.get("timeout", 30)
            working_dir = request.get("working_dir", "/mnt/data")
            initial_state = request.get("initial_state")
            capture_state = request.get("capture_state", False)
            args = request.get("args")  # List of command line arguments

            # Execute code with optional state persistence
            response = execute_code(
                code,
                timeout,
                working_dir,
                initial_state=initial_state,
                capture_state=capture_state,
                args=args
            )

            # Send response
            write_response(response)

        except Exception as e:
            # Catch-all for unexpected errors
            write_response({
                "exit_code": 1,
                "stdout": "",
                "stderr": f"REPL error: {e}\n{traceback.format_exc()}",
                "execution_time_ms": 0
            })


if __name__ == "__main__":
    main()
