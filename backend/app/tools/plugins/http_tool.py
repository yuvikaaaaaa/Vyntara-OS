"""IOS Tools Plugins — HTTP Tool."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from app.tools.exceptions import ToolExecutionError, ToolTimeoutError, ToolValidationError
from app.tools.plugins.base_tool import BaseToolPlugin
from app.tools.types import ToolCapability, ToolHealth, ToolMetadata, ToolStatus, ToolType

_METHODS = {"GET", "POST", "PUT", "DELETE"}
_MAX_RESPONSE_BODY_CHARS = 100_000


class HTTPTool(BaseToolPlugin):
    """
    Async outbound HTTP tool.

    Implementation-independent: uses ``httpx.AsyncClient`` internally but
    exposes only a generic ``method/url/headers/params/json_body`` request
    contract, so callers (agents) never depend on the underlying HTTP
    library. Response bodies are truncated defensively to prevent a
    single call from flooding downstream context assembly.

    No permission tier is required by default (network access is
    considered a baseline capability); deployments requiring stricter
    control can register this tool with a custom
    ``ToolMetadata.required_permission`` override at registration time.
    """

    def __init__(self, *, default_timeout_seconds: float = 30.0) -> None:
        metadata = ToolMetadata(
            name="http",
            display_name="HTTP Client",
            description="Perform GET, POST, PUT, DELETE HTTP requests to external endpoints.",
            tool_type=ToolType.NETWORK,
            capabilities=[ToolCapability.READ, ToolCapability.WRITE],
            input_schema={
                "type": "object",
                "properties": {
                    "method": {"type": "string", "enum": sorted(_METHODS)},
                    "url": {"type": "string"},
                    "headers": {"type": "object"},
                    "params": {"type": "object"},
                    "json_body": {"type": "object"},
                },
                "required": ["method", "url"],
            },
            default_timeout_seconds=int(default_timeout_seconds),
            max_timeout_seconds=120,
            tags=["http", "network"],
        )
        super().__init__(metadata)
        self._default_timeout = default_timeout_seconds

    async def run(self, arguments: dict[str, Any]) -> Any:
        async with self._span("run", method=str(arguments.get("method"))):
            method = str(arguments.get("method", "")).upper()
            if method not in _METHODS:
                raise ToolValidationError(
                    f"Unsupported HTTP method '{method}'. Must be one of {sorted(_METHODS)}."
                )

            url = arguments.get("url")
            if not url:
                raise ToolValidationError("'url' argument is required.")

            headers = arguments.get("headers") or {}
            params = arguments.get("params") or {}
            json_body = arguments.get("json_body")
            timeout = arguments.get("timeout_seconds", self._default_timeout)

            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.request(
                        method,
                        url,
                        headers=headers,
                        params=params,
                        json=json_body if method in ("POST", "PUT") else None,
                    )
            except httpx.TimeoutException as exc:
                raise ToolTimeoutError(
                    f"HTTP request to '{url}' timed out after {timeout}s.",
                    details={"url": url, "method": method},
                ) from exc
            except httpx.RequestError as exc:
                raise ToolExecutionError(
                    f"HTTP request to '{url}' failed: {exc}",
                    details={"url": url, "method": method},
                ) from exc

            return self._build_result(response)

    async def health_check(self) -> ToolHealth:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.get("https://www.google.com", timeout=5.0)
            return ToolHealth(
                tool_name=self._metadata.name,
                is_healthy=True,
                status=ToolStatus.COMPLETE,
            )
        except Exception as exc:
            return ToolHealth(
                tool_name=self._metadata.name,
                is_healthy=False,
                status=ToolStatus.FAILED,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _build_result(response: httpx.Response) -> dict:
        body_text = response.text
        truncated = len(body_text) > _MAX_RESPONSE_BODY_CHARS
        if truncated:
            body_text = body_text[:_MAX_RESPONSE_BODY_CHARS] + "…[truncated]"

        json_body: Any = None
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                json_body = response.json()
            except ValueError:
                json_body = None

        return {
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "text": body_text,
            "json": json_body,
            "truncated": truncated,
            "elapsed_ms": int(response.elapsed.total_seconds() * 1000),
        }