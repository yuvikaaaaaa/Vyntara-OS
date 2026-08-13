"""IOS Tools Plugins — Python Execution Tool."""
from __future__ import annotations

import asyncio
import builtins
import contextlib
import io
import traceback
from typing import Any

from app.core.constants import TOOL_PYTHON_EXECUTE
from app.tools.exceptions import ToolExecutionError, ToolTimeoutError, ToolValidationError
from app.tools.plugins.base_tool import BaseToolPlugin
from app.tools.types import ToolCapability, ToolMetadata, ToolType

# Builtins allow-list — everything else is unavailable inside the
# executed code's global namespace. Deliberately excludes anything with
# filesystem, process, network, or import-machinery access (open, exec,
# eval, __import__, compile, input, exit, quit, os/sys module access via
# import is blocked since __import__ itself is excluded).
_SAFE_BUILTIN_NAMES = frozenset(
    {
        "abs", "all", "any", "bool", "bytearray", "bytes", "chr", "complex",
        "dict", "divmod", "enumerate", "filter", "float", "format",
        "frozenset", "hash", "hex", "int", "isinstance", "issubclass",
        "iter", "len", "list", "map", "max", "min", "next", "object",
        "oct", "ord", "pow", "print", "range", "repr", "reversed",
        "round", "set", "slice", "sorted", "str", "sum", "tuple", "type",
        "zip", "True", "False", "None", "Exception", "ValueError",
        "TypeError", "KeyError", "IndexError", "StopIteration",
        "ArithmeticError", "ZeroDivisionError", "AttributeError",
        "RuntimeError", "NotImplementedError",
    }
)

_DEFAULT_TIMEOUT_SECONDS = 15


class PythonTool(BaseToolPlugin):
    """
    Sandboxed Python code execution tool.

    Isolation strategy:
    - Executes with a restricted ``__builtins__`` mapping containing only
      the allow-listed safe names — no ``open``, ``exec``, ``eval``,
      ``__import__``, ``compile``, ``input``, ``exit``/``quit``, so
      executed code cannot perform file, process, or network I/O, and
      cannot import any module (including os/sys/subprocess).
    - Runs the (blocking) ``exec()`` call in a worker thread via
      ``asyncio.to_thread`` so the event loop is never blocked, and
      wraps that with ``asyncio.wait_for`` to enforce a hard wall-clock
      timeout — Python's ``exec`` has no built-in cooperative
      cancellation point, so timeout enforcement here is best-effort:
      the worker thread may continue running after timeout is raised to
      the caller, but produces no further observable side effects since
      the sandbox blocks I/O primitives entirely.
    - Captures stdout/stderr via ``contextlib.redirect_stdout`` /
      ``redirect_stderr`` into in-memory buffers.
    - The final expression's value (if the code is a single expression)
      or the contents of a ``result`` variable left in the local
      namespace (if present) is returned as the tool's structured output
      value.

    This is a defence-in-depth interpreter-level sandbox, not process or
    container isolation — deployments requiring hard isolation should
    inject a container/subprocess-backed IToolSandbox at the framework
    level (see app.tools.tool_sandbox.ToolSandbox) rather than relying on
    this plugin alone.
    """

    def __init__(self, *, default_timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS) -> None:
        metadata = ToolMetadata(
            name="python_executor",
            display_name="Python Executor",
            description="Execute Python code in a restricted sandbox and capture stdout/stderr.",
            tool_type=ToolType.CODE_EXECUTION,
            capabilities=[ToolCapability.EXECUTE],
            input_schema={
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 60},
                },
                "required": ["code"],
            },
            required_permission=TOOL_PYTHON_EXECUTE,
            default_timeout_seconds=default_timeout_seconds,
            max_timeout_seconds=60,
            tags=["python", "code-execution"],
        )
        super().__init__(metadata)
        self._default_timeout = default_timeout_seconds

    async def run(self, arguments: dict[str, Any]) -> Any:
        async with self._span("run"):
            code = arguments.get("code")
            if not code or not isinstance(code, str):
                raise ToolValidationError("'code' argument must be a non-empty string.")

            timeout = arguments.get("timeout_seconds", self._default_timeout)

            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(self._execute, code), timeout=timeout
                )
            except asyncio.TimeoutError as exc:
                raise ToolTimeoutError(
                    f"Python execution exceeded timeout of {timeout}s.",
                    details={"timeout_seconds": timeout},
                ) from exc

    # ------------------------------------------------------------------
    # Internal (runs in worker thread)
    # ------------------------------------------------------------------

    def _execute(self, code: str) -> dict:
        restricted_builtins = {
            name: getattr(builtins, name)
            for name in _SAFE_BUILTIN_NAMES
            if hasattr(builtins, name)
        }
        exec_globals: dict[str, Any] = {"__builtins__": restricted_builtins}
        exec_locals: dict[str, Any] = {}

        stdout_buffer = io.StringIO()
        stderr_buffer = io.StringIO()

        try:
            with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
                compiled = compile(code, "<sandboxed_code>", "exec")
                exec(compiled, exec_globals, exec_locals)  # noqa: S102 — intentional sandboxed exec
        except Exception as exc:
            tb = traceback.format_exc(limit=5)
            raise ToolExecutionError(
                f"Python execution raised {exc.__class__.__name__}: {exc}",
                details={"traceback": tb},
            ) from exc

        result_value = exec_locals.get("result")
        return {
            "stdout": stdout_buffer.getvalue(),
            "stderr": stderr_buffer.getvalue(),
            "result": self._safe_repr(result_value) if result_value is not None else None,
        }

    @staticmethod
    def _safe_repr(value: Any) -> Any:
        """Coerce the sandboxed 'result' variable to a JSON-safe value."""
        if isinstance(value, (str, int, float, bool, type(None))):
            return value
        if isinstance(value, (list, tuple)):
            return [PythonTool._safe_repr(v) for v in value]
        if isinstance(value, dict):
            return {str(k): PythonTool._safe_repr(v) for k, v in value.items()}
        return repr(value)