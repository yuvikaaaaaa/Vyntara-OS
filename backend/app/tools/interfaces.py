"""IOS Tools — Interfaces."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.tools.types import (
    ToolCapability,
    ToolExecution,
    ToolHealth,
    ToolMetadata,
    ToolRequest,
    ToolResponse,
)


class ITool(ABC):
    """
    Contract every concrete tool implementation must satisfy.

    Concrete tools (Python execution, filesystem, HTTP, shell, browser,
    GitHub, etc.) are separate plugin modules built on top of this
    framework — this module defines only the contract they implement.
    """

    @property
    @abstractmethod
    def metadata(self) -> ToolMetadata: ...

    @abstractmethod
    async def run(self, arguments: dict[str, Any]) -> Any:
        """
        Execute the tool's core logic and return a raw output value.

        Must raise a ToolError subclass (or let exceptions propagate) on
        failure — the framework's ToolExecutor is responsible for
        catching, classifying, and converting exceptions into a
        ToolResponse; the tool itself does not construct ToolResponse.
        """

    @abstractmethod
    async def health_check(self) -> ToolHealth: ...


class IToolRegistry(ABC):
    """Contract for tool registration and discovery."""

    @abstractmethod
    def register(self, tool: ITool) -> None: ...

    @abstractmethod
    def unregister(self, tool_name: str) -> None: ...

    @abstractmethod
    def get(self, tool_name: str) -> ITool | None: ...

    @abstractmethod
    def find_by_capability(self, capability: ToolCapability) -> list[ITool]: ...

    @abstractmethod
    def list_all(self) -> list[ITool]: ...

    @abstractmethod
    def set_enabled(self, tool_name: str, enabled: bool) -> None: ...


class IToolSelector(ABC):
    """Contract for choosing the best tool among capability-matching candidates."""

    @abstractmethod
    async def select(
        self, request: ToolRequest, candidates: list[ITool]
    ) -> ITool: ...


class IToolValidator(ABC):
    """Contract for pre-execution request validation."""

    @abstractmethod
    async def validate(self, request: ToolRequest, tool: ITool) -> None:
        """Raise ToolValidationError / ToolPermissionError on failure."""


class IToolSandbox(ABC):
    """
    Contract for execution isolation.

    Implementation-independent: a concrete sandbox may be a plain asyncio
    timeout wrapper, a subprocess with resource limits, or a container —
    the framework only depends on this contract.
    """

    @abstractmethod
    async def run_isolated(
        self,
        tool: ITool,
        arguments: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> Any:
        """Execute tool.run(arguments) under enforced isolation/limits."""

    @abstractmethod
    async def cancel(self, execution_id: str) -> bool: ...


class IToolExecutor(ABC):
    """Contract for executing a single tool request end-to-end."""

    @abstractmethod
    async def execute(self, request: ToolRequest, tool: ITool) -> ToolExecution: ...


class IToolManager(ABC):
    """Contract for the sole Tool Execution Framework orchestration layer."""

    @abstractmethod
    async def invoke(self, request: ToolRequest) -> ToolResponse: ...