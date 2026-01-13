"""
Python state handling for the executor service.

Adapted from docker/repl_server.py for subprocess-based execution.
Provides state serialization/deserialization for Python REPL-like sessions.
"""

import base64
import logging
import os
import signal
import sys
import threading
import traceback
import time
from io import StringIO
from contextlib import redirect_stdout, redirect_stderr
from types import ModuleType
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Import cloudpickle for state serialization
try:
    import cloudpickle
    CLOUDPICKLE_AVAILABLE = True
except ImportError:
    CLOUDPICKLE_AVAILABLE = False
    logger.warning("cloudpickle not available - Python state persistence disabled")

# Import lz4 for state compression
try:
    import lz4.frame
    LZ4_AVAILABLE = True
except ImportError:
    LZ4_AVAILABLE = False
    logger.warning("lz4 not available - Python state will be uncompressed")

# State format version
STATE_VERSION_UNCOMPRESSED = 1
STATE_VERSION_LZ4 = 2
STATE_VERSION_HEADER_SIZE = 1

# State size limits
MAX_STATE_SIZE_BYTES = 50 * 1024 * 1024  # 50MB

# Keys to exclude from state serialization
EXCLUDED_KEYS = {
    '__builtins__', '__name__', '__doc__', '__package__',
    '__loader__', '__spec__', '__annotations__', '__cached__',
    '__file__', '__warningregistry__',
}

# Pre-loaded module names (to exclude from state)
PRELOADED_MODULE_NAMES = {
    'numpy', 'np', 'pandas', 'pd', 'matplotlib', 'plt',
    'scipy', 'sklearn', 'cv2', 'PIL', 'Image',
    'sympy', 'sp', 'networkx', 'nx', 'statsmodels', 'sm',
    'json', 'os', 'sys', 're', 'math', 'datetime',
    'collections', 'itertools', 'functools', 'pathlib',
}


def deserialize_state(state_b64: str) -> Dict[str, Any]:
    """
    Deserialize base64-encoded cloudpickle state.

    Args:
        state_b64: Base64-encoded pickled state (may be lz4 compressed)

    Returns:
        Dictionary of variable name -> value

    Raises:
        ValueError: If state is invalid or cloudpickle unavailable
    """
    if not state_b64:
        return {}

    if not CLOUDPICKLE_AVAILABLE:
        raise ValueError("cloudpickle not available for state deserialization")

    try:
        state_bytes = base64.b64decode(state_b64)

        if len(state_bytes) > MAX_STATE_SIZE_BYTES:
            raise ValueError(f"State too large: {len(state_bytes)} bytes")

        # Check version header
        if len(state_bytes) >= STATE_VERSION_HEADER_SIZE:
            version = state_bytes[0]
            payload = state_bytes[STATE_VERSION_HEADER_SIZE:]

            if version == STATE_VERSION_LZ4:
                if not LZ4_AVAILABLE:
                    raise ValueError("lz4 not available but state is compressed")
                decompressed = lz4.frame.decompress(payload)
                return cloudpickle.loads(decompressed)
            elif version == STATE_VERSION_UNCOMPRESSED:
                return cloudpickle.loads(payload)

        # Fallback: try raw cloudpickle
        return cloudpickle.loads(state_bytes)

    except Exception as e:
        raise ValueError(f"Failed to deserialize state: {e}")


def serialize_state(namespace: Dict[str, Any]) -> Tuple[Optional[str], List[str]]:
    """
    Serialize namespace to base64-encoded cloudpickle with lz4 compression.

    Args:
        namespace: Execution namespace dictionary

    Returns:
        Tuple of (base64_state or None, list of error messages)
    """
    if not CLOUDPICKLE_AVAILABLE:
        return None, ["cloudpickle not available"]

    errors = []
    serializable = {}

    # Build excluded keys set
    excluded = EXCLUDED_KEYS | PRELOADED_MODULE_NAMES

    for key, value in namespace.items():
        # Skip internal keys
        if key.startswith('_') or key in excluded:
            continue

        # Skip modules
        if isinstance(value, ModuleType):
            continue

        try:
            # Test if value is serializable
            _ = cloudpickle.dumps(value)
            serializable[key] = value
        except Exception as e:
            error_msg = str(e)[:100]
            errors.append(f"Cannot serialize '{key}' ({type(value).__name__}): {error_msg}")

    if not serializable:
        return None, errors

    try:
        pickled_bytes = cloudpickle.dumps(serializable)

        # Compress with lz4 if available
        if LZ4_AVAILABLE:
            compressed = lz4.frame.compress(pickled_bytes, compression_level=0)
            version = STATE_VERSION_LZ4
            payload = compressed
        else:
            version = STATE_VERSION_UNCOMPRESSED
            payload = pickled_bytes

        # Prepend version byte
        state_bytes = bytes([version]) + payload

        if len(state_bytes) > MAX_STATE_SIZE_BYTES:
            return None, [f"State too large: {len(state_bytes)} bytes"]

        return base64.b64encode(state_bytes).decode('ascii'), errors

    except Exception as e:
        return None, [f"Failed to serialize state: {e}"]


def preload_python_libraries() -> Dict[str, Any]:
    """
    Pre-import common Python libraries.

    Returns:
        Dictionary of module name/alias -> module
    """
    preloaded = {}

    libraries = [
        ("numpy", "np"),
        ("pandas", "pd"),
        ("matplotlib", None),
        ("matplotlib.pyplot", "plt"),
        ("scipy", None),
        ("sklearn", None),
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

    for module_name, alias in libraries:
        try:
            module = __import__(module_name.split('.')[0])
            for part in module_name.split('.')[1:]:
                module = getattr(module, part)

            preloaded[module_name] = module
            if alias:
                preloaded[alias] = module
        except ImportError:
            pass
        except Exception:
            pass

    # Configure matplotlib for non-interactive backend
    try:
        import matplotlib
        matplotlib.use('Agg')
    except Exception:
        pass

    return preloaded


# Pre-load libraries at module import time
PRELOADED_MODULES = preload_python_libraries()


class TimeoutError(Exception):
    """Raised when execution times out."""
    pass


def timeout_handler(signum, frame):
    """Signal handler for execution timeout."""
    raise TimeoutError("Execution timed out")


def execute_python_code(
    code: str,
    timeout: int = 30,
    working_dir: str = "/mnt/data",
    initial_state: Optional[str] = None,
    capture_state: bool = True,
) -> Dict[str, Any]:
    """
    Execute Python code with optional state persistence.

    Args:
        code: Python code to execute
        timeout: Maximum execution time in seconds
        working_dir: Working directory
        initial_state: Base64-encoded state to restore
        capture_state: Whether to capture state after execution

    Returns:
        Dict with exit_code, stdout, stderr, execution_time_ms, state, state_errors
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
    try:
        original_dir = os.getcwd()
    except OSError:
        original_dir = None  # Current dir doesn't exist (container startup)
    try:
        os.chdir(working_dir)
    except Exception:
        pass

    # Set up output capture
    stdout_capture = StringIO()
    stderr_capture = StringIO()

    exit_code = 0
    timed_out = False

    # Check if we're in the main thread (signal only works there)
    in_main_thread = threading.current_thread() is threading.main_thread()
    old_handler = None

    # Set up timeout handler (only if in main thread)
    if in_main_thread:
        old_handler = signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(timeout)

    try:
        # Create namespace with preloaded modules and restored state
        namespace = {
            '__builtins__': __builtins__,
            '__name__': '__main__',
        }
        namespace.update(PRELOADED_MODULES)

        if restored_state:
            namespace.update(restored_state)

        # Execute code
        with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
            try:
                # Try to compile as expression first
                compiled = compile(code, '<code>', 'eval')
                result = eval(compiled, namespace)
                if result is not None:
                    print(repr(result))
            except SyntaxError:
                # Execute as statements
                compiled = compile(code, '<code>', 'exec')
                exec(compiled, namespace)

    except TimeoutError:
        exit_code = 124
        stderr_capture.write(f"TimeoutError: Execution exceeded {timeout} seconds\n")
        timed_out = True

    except SyntaxError as e:
        exit_code = 1
        stderr_capture.write(f"SyntaxError: {e}\n")

    except Exception:
        exit_code = 1
        tb = traceback.format_exc()
        stderr_capture.write(tb)

    finally:
        # Cancel timeout (only if we set it up)
        if in_main_thread and old_handler is not None:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)

        # Restore working directory
        if original_dir:
            try:
                os.chdir(original_dir)
            except Exception:
                pass

        # Clean up matplotlib figures
        try:
            import matplotlib.pyplot as plt
            plt.close('all')
        except Exception:
            pass

    execution_time_ms = int((time.perf_counter() - start_time) * 1000)

    result = {
        "exit_code": exit_code,
        "stdout": stdout_capture.getvalue(),
        "stderr": stderr_capture.getvalue(),
        "execution_time_ms": execution_time_ms,
        "timed_out": timed_out,
    }

    # Capture state if requested
    if capture_state and namespace is not None:
        state_b64, serialize_errors = serialize_state(namespace)
        if state_b64:
            result["state"] = state_b64
        state_errors.extend(serialize_errors)

    if state_errors:
        result["state_errors"] = state_errors

    return result
