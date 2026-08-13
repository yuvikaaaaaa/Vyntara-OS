"""IOS Tools Plugins — Shell Execution Tool."""
from __future__ import annotations

import asyncio
from typing import Any

from app.tools.exceptions import ToolExecutionError, ToolTimeoutError, ToolValidationError
from app.tools.plugins.base_tool import BaseToolPlugin
from app.tools.types import ToolCapability, ToolMetadata, ToolType

_DEFAULT_TIMEOUT_SECONDS = 30
_MAX_OUTPUT_CHARS = 50_000


class ShellTool(BaseToolPlugin):
    """
    Async shell command execution tool.

    Uses ``asyncio.create_subprocess_shell`` so the running event loop is
    never blocked while the subprocess executes. Enforces a hard
    wall-clock timeout via ``asyncio.wait_for``; on timeout the process
    group is terminated (SIGTERM, escalating to SIGKILL if it does not
    exit promptly) rather than left running as an orphan.

    Captures both stdout and stderr as decoded UTF-8 text (with
    replacement-character fallback for undecodable bytes), truncated
    defensively to prevent a runaway command from flooding downstream
    context assembly. Exit code is always reported, including on
    non-zero exit (a non-zero exit is *not* itself treated as a tool
    execution failure — the caller inspects ``exit_code`` — since many
    legitimate shell workflows use non-zero exit codes as a normal
    signal, e.g. ``grep`` finding no matches).

    Platform note: relies on POSIX process-group semantics
    (``os.setsid`` / ``os.killpg``) for clean timeout termination where
    available, falling back to direct process termination on platforms
    lacking process-group support (e.g. Windows), which may leave child
    processes spawned by the shell command running after a timeout on
    those platforms.
    """

    def __init__(self, *, default_timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS) -> None:
        metadata = ToolMetadata(
            name="shell_executor",
            display_name="Shell Executor",
            description="Execute a shell command and capture stdout, stderr, and exit code.",
            tool_type=ToolType.CODE_EXECUTION,
            capabilities=[ToolCapability.EXECUTE],
            input_schema={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120},
                    "cwd": {"type": "string"},
                },
                "required": ["command"],
            },
            default_timeout_seconds=default_timeout_seconds,
            max_timeout_seconds=120,
            tags=["shell", "code-execution"],
        )
        super().__init__(metadata)
        self._default_timeout = default_timeout_seconds

    async def run(self, arguments: dict[str, Any]) -> Any:
        async with self._span("run"):
            command = arguments.get("command")
            if not command or not isinstance(command, str):
                raise ToolValidationError("'command' argument must be a non-empty string.")

            timeout = arguments.get("timeout_seconds", self._default_timeout)
            cwd = arguments.get("cwd")

            process = await self._start_process(command, cwd)

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError as exc:
                await self._terminate(process)
                raise ToolTimeoutError(
                    f"Shell command exceeded timeout of {timeout}s.",
                    details={"command": command, "timeout_seconds": timeout},
                ) from exc
            except Exception as exc:
                await self._terminate(process)
                raise ToolExecutionError(
                    f"Shell command execution failed: {exc}",
                    details={"command": command},
                ) from exc

            return {
                "exit_code": process.returncode,
                "stdout": self._decode_truncate(stdout_bytes),
                "stderr": self._decode_truncate(stderr_bytes),
                "command": command,
            }

    # ------------------------------------------------------------------
    # Process lifecycle
    # ------------------------------------------------------------------

    async def _start_process(
        self, command: str, cwd: str | None
    ) -> asyncio.subprocess.Process:
        import os

        kwargs: dict[str, Any] = {
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
        }
        if cwd:
            kwargs["cwd"] = cwd
        if hasattr(os, "setsid"):
            kwargs["preexec_fn"] = os.setsid  # enable process-group kill on timeout

        try:
            return await asyncio.create_subprocess_shell(command, **kwargs)
        except Exception as exc:
            raise ToolExecutionError(
                f"Failed to start shell command: {exc}",
                details={"command": command},
            ) from exc

    async def _terminate(self, process: asyncio.subprocess.Process) -> None:
        import os
        import signal

        try:
            if hasattr(os, "killpg") and process.pid:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            else:
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                if hasattr(os, "killpg") and process.pid:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                else:
                    process.kill()
        except (ProcessLookupError, PermissionError):
            pass
        except Exception as exc:
            self._log.warning("shell_process_terminate_failed", exc=str(exc))

    # ------------------------------------------------------------------
    # Output handling
    # ------------------------------------------------------------------

    @staticmethod
    def _decode_truncate(raw: bytes | None) -> str:
        if not raw:
            return ""
        text = raw.decode("utf-8", errors="replace")
        if len(text) > _MAX_OUTPUT_CHARS:
            text = text[:_MAX_OUTPUT_CHARS] + "…[truncated]"
        return text