"""IOS Tools Plugins — Built-in Tool Plugins.

Concrete ``ITool`` implementations shipped with the Intelligence
Operating System, built entirely on the reusable Tool Execution
Framework (``app.tools``) and the project's existing infrastructure
(database session layer, structured logging, telemetry).

This package contains only plugin implementations — no framework logic
lives here (that belongs to ``app.tools`` itself), and no orchestration
layer is instantiated here (``ToolManager`` composition is the
responsibility of application startup / dependency-injection wiring).

Usage::

    from app.tools import ToolRegistry
    from app.tools.plugins import register_builtin_plugins

    registry = ToolRegistry()
    register_builtin_plugins(registry)
"""
from __future__ import annotations

from app.tools.interfaces import ITool
from app.tools.tool_registry import ToolRegistry

from app.tools.plugins.base_tool import BaseToolPlugin as BaseTool
from app.tools.plugins.filesystem_tool import FilesystemTool
from app.tools.plugins.http_tool import HTTPTool
from app.tools.plugins.python_tool import PythonTool
from app.tools.plugins.shell_tool import ShellTool
from app.tools.plugins.database_tool import DatabaseTool


def register_builtin_plugins(
    registry: ToolRegistry,
    *,
    filesystem_sandbox_root: str | None = None,
) -> list[str]:
    """
    Register every built-in tool plugin with the given ToolRegistry
    using the registry's own public registration API
    (``ToolRegistry.register()``) — no plugin is ever registered by any
    means other than this public mechanism.

    Idempotent: tools already present in the registry (by name) are
    skipped rather than re-registered, so this function is safe to call
    multiple times (e.g. across test setup/teardown cycles) without
    raising ``ToolAlreadyRegisteredError``.

    Args:
        registry: Target ToolRegistry instance to register plugins into.
        filesystem_sandbox_root: Optional override for FilesystemTool's
                                  sandbox root directory. Defaults to the
                                  plugin's own built-in default
                                  (``UPLOAD_STORAGE_PATH``) when omitted.

    Returns:
        List of tool names that were newly registered (excludes any
        already-present tools that were skipped).
    """
    candidates: list[ITool] = [
        FilesystemTool(sandbox_root=filesystem_sandbox_root)
        if filesystem_sandbox_root
        else FilesystemTool(),
        HTTPTool(),
        PythonTool(),
        ShellTool(),
        DatabaseTool(),
    ]

    registered: list[str] = []
    for tool in candidates:
        if registry.get(tool.metadata.name) is not None:
            continue
        registry.register(tool)
        registered.append(tool.metadata.name)

    return registered


__all__ = [
    "BaseTool",
    "FilesystemTool",
    "HTTPTool",
    "PythonTool",
    "ShellTool",
    "DatabaseTool",
    "register_builtin_plugins",
]