"""IOS Tools — Public API.

The Tool Execution Framework provides the reusable, provider-independent
infrastructure for registering, selecting, validating, sandboxing, and
executing tools that agents invoke during task execution.

It does NOT implement any concrete tool (Python execution, filesystem,
HTTP, shell, browser, GitHub, etc.) — those are separate plugin modules
built on top of this framework's ITool contract.

Usage::

    from app.tools import ToolManager, ToolRequest, ToolResponse
    from app.tools import ToolRegistry, ToolSelector, ToolValidator
    from app.tools import ToolSandbox, ToolExecutor, ToolResult
"""

# ---------------------------------------------------------------------------
# Shared types
# ---------------------------------------------------------------------------
from app.tools.types import (
    SandboxResourceLimit,
    ToolCapability,
    ToolExecution,
    ToolHealth,
    ToolMetadata,
    ToolMetrics,
    ToolRequest,
    ToolResponse,
    ToolStatus,
    ToolType,
)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
from app.tools.exceptions import (
    NoToolAvailableError,
    ToolAlreadyRegisteredError,
    ToolCancelledError,
    ToolDisabledError,
    ToolError,
    ToolExecutionError,
    ToolMaxRetriesExceededError,
    ToolNotFoundError,
    ToolPermissionError,
    ToolResourceLimitExceededError,
    ToolSandboxError,
    ToolTimeoutError,
    ToolValidationError,
)

# ---------------------------------------------------------------------------
# Interfaces
# ---------------------------------------------------------------------------
from app.tools.interfaces import (
    ITool,
    IToolExecutor,
    IToolManager,
    IToolRegistry,
    IToolSandbox,
    IToolSelector,
    IToolValidator,
)

# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------
from app.tools.base import BaseToolComponent

# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------
from app.tools.tool_registry import ToolRegistry
from app.tools.tool_selector import ToolSelector
from app.tools.tool_validator import ToolValidator
from app.tools.tool_sandbox import ToolSandbox
from app.tools.tool_executor import ToolExecutor
from app.tools.tool_result import ToolResult

# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
from app.tools.tool_manager import ToolManager

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
__all__ = [
    # Types
    "ToolType",
    "ToolCapability",
    "ToolStatus",
    "SandboxResourceLimit",
    "ToolMetadata",
    "ToolRequest",
    "ToolResponse",
    "ToolExecution",
    "ToolHealth",
    "ToolMetrics",
    # Exceptions
    "ToolError",
    "ToolNotFoundError",
    "ToolAlreadyRegisteredError",
    "ToolExecutionError",
    "ToolTimeoutError",
    "ToolCancelledError",
    "ToolValidationError",
    "ToolPermissionError",
    "ToolSandboxError",
    "ToolResourceLimitExceededError",
    "NoToolAvailableError",
    "ToolMaxRetriesExceededError",
    "ToolDisabledError",
    # Interfaces
    "ITool",
    "IToolRegistry",
    "IToolSelector",
    "IToolValidator",
    "IToolSandbox",
    "IToolExecutor",
    "IToolManager",
    # Base
    "BaseToolComponent",
    # Components
    "ToolRegistry",
    "ToolSelector",
    "ToolValidator",
    "ToolSandbox",
    "ToolExecutor",
    "ToolResult",
    # Orchestrator
    "ToolManager",
]