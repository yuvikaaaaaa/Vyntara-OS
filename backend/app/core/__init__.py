"""
Intelligence Operating System — Core Package
============================================
Re-exports the most commonly used symbols from the ``core`` sub-package so
that other modules can write short imports:

    from app.core import get_settings, get_logger, IosBaseException

instead of the longer form:

    from app.core.config import get_settings
    from app.core.logging import get_logger
    from app.core.exceptions import IosBaseException
"""

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    AgentError,
    AgentTimeoutError,
    AuthenticationError,
    AuthorizationError,
    IosBaseException,
    NotFoundError,
    RAGError,
    RateLimitExceededError,
    ValidationError,
)
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
    hash_password,
    verify_password,
)

__all__ = [
    # Config
    "Settings",
    "get_settings",
    # Logging
    "get_logger",
    # Exceptions
    "IosBaseException",
    "AgentError",
    "AgentTimeoutError",
    "AuthenticationError",
    "AuthorizationError",
    "NotFoundError",
    "RAGError",
    "RateLimitExceededError",
    "ValidationError",
    # Security
    "create_access_token",
    "create_refresh_token",
    "decode_access_token",
    "hash_password",
    "verify_password",
]
