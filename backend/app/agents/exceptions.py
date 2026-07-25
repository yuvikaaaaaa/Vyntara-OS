"""IOS Agents — Exceptions."""
from __future__ import annotations

from app.core.exceptions import IosBaseException


class AgentError(IosBaseException):
    http_status = 500
    code = "AGENT_ERROR"


class AgentNotFoundError(AgentError):
    http_status = 404
    code = "AGENT_NOT_FOUND"


class AgentAlreadyRegisteredError(AgentError):
    http_status = 409
    code = "AGENT_ALREADY_REGISTERED"


class AgentExecutionError(AgentError):
    code = "AGENT_EXECUTION_ERROR"


class AgentTimeoutError(AgentError):
    code = "AGENT_TIMEOUT"


class AgentCancelledError(AgentError):
    http_status = 409
    code = "AGENT_CANCELLED"


class AgentCommunicationError(AgentError):
    code = "AGENT_COMMUNICATION_ERROR"


class AgentCapabilityError(AgentError):
    http_status = 422
    code = "AGENT_CAPABILITY_MISMATCH"


class NoAgentAvailableError(AgentError):
    http_status = 503
    code = "NO_AGENT_AVAILABLE"


class AgentUnhealthyError(AgentError):
    http_status = 503
    code = "AGENT_UNHEALTHY"


class TaskDispatchError(AgentError):
    code = "TASK_DISPATCH_ERROR"


class AgentCoordinationError(AgentError):
    code = "AGENT_COORDINATION_ERROR"


class MaxRetriesExceededError(AgentError):
    code = "AGENT_MAX_RETRIES_EXCEEDED"