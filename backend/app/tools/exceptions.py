"""IOS Tools — Exceptions."""
from __future__ import annotations

from app.core.exceptions import IosBaseException


class ToolError(IosBaseException):
    http_status = 500
    code = "TOOL_ERROR"


class ToolNotFoundError(ToolError):
    http_status = 404
    code = "TOOL_NOT_FOUND"


class ToolAlreadyRegisteredError(ToolError):
    http_status = 409
    code = "TOOL_ALREADY_REGISTERED"


class ToolExecutionError(ToolError):
    code = "TOOL_EXECUTION_ERROR"


class ToolTimeoutError(ToolError):
    code = "TOOL_TIMEOUT"


class ToolCancelledError(ToolError):
    http_status = 409
    code = "TOOL_CANCELLED"


class ToolValidationError(ToolError):
    http_status = 422
    code = "TOOL_VALIDATION_ERROR"


class ToolPermissionError(ToolError):
    http_status = 403
    code = "TOOL_PERMISSION_DENIED"


class ToolSandboxError(ToolError):
    code = "TOOL_SANDBOX_ERROR"


class ToolResourceLimitExceededError(ToolSandboxError):
    http_status = 422
    code = "TOOL_RESOURCE_LIMIT_EXCEEDED"


class NoToolAvailableError(ToolError):
    http_status = 503
    code = "NO_TOOL_AVAILABLE"


class ToolMaxRetriesExceededError(ToolError):
    code = "TOOL_MAX_RETRIES_EXCEEDED"


class ToolDisabledError(ToolError):
    http_status = 403
    code = "TOOL_DISABLED"