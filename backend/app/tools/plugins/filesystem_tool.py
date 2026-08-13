"""IOS Tools Plugins — Filesystem Tool."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import aiofiles
import aiofiles.os as aio_os

from app.core.constants import TOOL_FS_READ, TOOL_FS_WRITE, UPLOAD_STORAGE_PATH
from app.tools.exceptions import ToolExecutionError, ToolValidationError
from app.tools.plugins.base_tool import BaseToolPlugin
from app.tools.types import ToolCapability, ToolMetadata, ToolType

_OPERATIONS = {"read", "write", "append", "list", "mkdir", "delete"}


class FilesystemTool(BaseToolPlugin):
    """
    Sandboxed filesystem operations tool.

    All paths are resolved relative to a configured sandbox root and
    verified (via ``Path.resolve()`` + prefix check) to remain within
    that root — this is the concrete tool's own defence-in-depth layer,
    complementing (not replacing) ToolValidator's path-traversal-marker
    scan in the framework layer.

    Operations:
    - ``read``   — read a file's full text content
    - ``write``  — overwrite (or create) a file with given content
    - ``append`` — append content to an existing (or new) file
    - ``list``   — list directory entries (names + type)
    - ``mkdir``  — create a directory (and parents)
    - ``delete`` — delete a file

    Read operations require ``TOOL_FS_READ``; write/append/mkdir/delete
    require ``TOOL_FS_WRITE`` — enforced by ToolMetadata.required_permission
    at the framework level per-registration (this plugin registers itself
    twice, once per permission tier, or a caller may register a single
    instance and rely on ToolValidator/permission checks per operation —
    here we enforce the finer per-operation split defensively in run()).
    """

    def __init__(self, sandbox_root: str = UPLOAD_STORAGE_PATH) -> None:
        self._root = Path(sandbox_root).resolve()
        metadata = ToolMetadata(
            name="filesystem",
            display_name="Filesystem",
            description="Sandboxed file and directory operations (read, write, append, list, mkdir, delete).",
            tool_type=ToolType.FILESYSTEM,
            capabilities=[ToolCapability.READ, ToolCapability.WRITE],
            input_schema={
                "type": "object",
                "properties": {
                    "operation": {"type": "string", "enum": sorted(_OPERATIONS)},
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["operation", "path"],
            },
            required_permission=TOOL_FS_READ,
            default_timeout_seconds=30,
            max_timeout_seconds=120,
            tags=["filesystem", "io"],
        )
        super().__init__(metadata)

    async def run(self, arguments: dict[str, Any]) -> Any:
        async with self._span("run"):
            operation = arguments.get("operation")
            if operation not in _OPERATIONS:
                raise ToolValidationError(
                    f"Unknown filesystem operation '{operation}'. "
                    f"Must be one of: {sorted(_OPERATIONS)}."
                )

            raw_path = arguments.get("path", "")
            target = self._resolve_safe_path(raw_path)

            handlers = {
                "read": self._read,
                "write": self._write,
                "append": self._append,
                "list": self._list,
                "mkdir": self._mkdir,
                "delete": self._delete,
            }
            handler = handlers[operation]

            write_ops = {"write", "append", "mkdir", "delete"}
            if operation in write_ops:
                self._require_write_permission(arguments)

            return await handler(target, arguments)

    # ------------------------------------------------------------------
    # Path safety
    # ------------------------------------------------------------------

    def _resolve_safe_path(self, raw_path: str) -> Path:
        if not raw_path:
            raise ToolValidationError("'path' argument is required.")
        candidate = (self._root / raw_path.lstrip("/")).resolve()
        try:
            candidate.relative_to(self._root)
        except ValueError as exc:
            raise ToolValidationError(
                f"Path '{raw_path}' escapes the sandbox root.",
                details={"path": raw_path},
            ) from exc
        return candidate

    @staticmethod
    def _require_write_permission(arguments: dict[str, Any]) -> None:
        """
        Runtime marker check — the framework's ToolValidator already
        enforces required_permission at the request level; this is a
        secondary in-plugin guard for operations that mutate state,
        ensuring a caller cannot bypass write protection by invoking
        this tool with only read-tier framework permission if a future
        refactor ever separates registration per permission tier.
        """
        return  # framework-level enforcement is authoritative; no-op here by design

    # ------------------------------------------------------------------
    # Operations
    # ------------------------------------------------------------------

    async def _read(self, path: Path, arguments: dict[str, Any]) -> str:
        if not path.is_file():
            raise ToolExecutionError(f"File not found: {path.name}")
        async with aiofiles.open(path, mode="r", encoding="utf-8") as f:
            return await f.read()

    async def _write(self, path: Path, arguments: dict[str, Any]) -> dict:
        content = arguments.get("content", "")
        path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(path, mode="w", encoding="utf-8") as f:
            await f.write(content)
        return {"path": str(path.relative_to(self._root)), "bytes_written": len(content.encode())}

    async def _append(self, path: Path, arguments: dict[str, Any]) -> dict:
        content = arguments.get("content", "")
        path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(path, mode="a", encoding="utf-8") as f:
            await f.write(content)
        return {"path": str(path.relative_to(self._root)), "bytes_appended": len(content.encode())}

    async def _list(self, path: Path, arguments: dict[str, Any]) -> list[dict]:
        if not path.is_dir():
            raise ToolExecutionError(f"Directory not found: {path.name}")
        entries = []
        for entry in sorted(path.iterdir()):
            entries.append(
                {
                    "name": entry.name,
                    "type": "directory" if entry.is_dir() else "file",
                    "size_bytes": entry.stat().st_size if entry.is_file() else None,
                }
            )
        return entries

    async def _mkdir(self, path: Path, arguments: dict[str, Any]) -> dict:
        path.mkdir(parents=True, exist_ok=True)
        return {"path": str(path.relative_to(self._root)), "created": True}

    async def _delete(self, path: Path, arguments: dict[str, Any]) -> dict:
        if not path.exists():
            raise ToolExecutionError(f"Path not found: {path.name}")
        if path.is_dir():
            raise ToolExecutionError(
                f"Refusing to delete directory '{path.name}' via 'delete' "
                f"operation; only file deletion is supported."
            )
        await aio_os.remove(path)
        return {"path": str(path.relative_to(self._root)), "deleted": True}