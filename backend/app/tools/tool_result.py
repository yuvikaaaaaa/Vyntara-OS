"""IOS Tools — Tool Result Normalizer."""
from __future__ import annotations

import json
from typing import Any

from app.tools.base import BaseToolComponent
from app.tools.exceptions import ToolError
from app.tools.types import ToolExecution, ToolResponse, ToolStatus


class ToolResult(BaseToolComponent):
    """
    Normalizes ToolExecution records into final ToolResponse objects and
    plain-JSON-serialisable dicts for cross-boundary consumption (e.g.
    handed to the Agent Engine's AgentResult.output, or serialised into
    an audit log entry).

    This component performs no execution of its own — it is a pure
    transformation step consumed by ToolManager after ToolExecutor
    produces a ToolExecution.

    Responsibilities:
    - **Serialization** — convert arbitrary tool output into a
      JSON-safe representation, falling back to ``str()`` for
      non-serialisable objects rather than raising.
    - **Metadata preservation** — attach execution timing, attempt count,
      and tool identity onto the normalized response's metadata dict.
    - **Timing** — surface duration_ms consistently regardless of which
      terminal status (COMPLETE/FAILED/TIMED_OUT/CANCELLED) produced it.
    - **Error normalization** — convert any exception type (ToolError
      subclasses or arbitrary Python exceptions bubbling up from a
      concrete plugin) into a consistent ``{error, error_code}`` shape.
    - **Response conversion** — a single ``normalize()`` entry point
      that always returns a well-formed ToolResponse, even when the
      underlying ToolExecution has no response attached (defensive
      guard against a malformed executor implementation).
    """

    def normalize(self, execution: ToolExecution) -> ToolResponse:
        """
        Produce the final, normalized ToolResponse for a ToolExecution.

        Always returns a ToolResponse — never raises — since this is the
        last step before a result is handed back to a caller that may
        not be prepared to catch further exceptions.
        """
        if execution.response is not None:
            response = execution.response
        else:
            # Defensive fallback: executor produced no response object
            # (should not normally happen, but never surface a bare None).
            response = ToolResponse(
                request_id=execution.request.id,
                tool_name=execution.request.tool_name,
                success=False,
                error=execution.error or "Tool execution produced no response.",
                error_code="NO_RESPONSE",
                duration_ms=execution.duration_ms,
            )

        response.output = self._safe_serialize(response.output)
        response.metadata = {
            **response.metadata,
            "attempt": execution.attempt,
            "status": execution.status.value,
            "started_at": execution.started_at.isoformat() if execution.started_at else None,
            "completed_at": execution.completed_at.isoformat() if execution.completed_at else None,
        }
        return response

    def to_dict(self, response: ToolResponse) -> dict[str, Any]:
        """Flatten a ToolResponse into a plain JSON-serialisable dict."""
        return {
            "request_id": response.request_id,
            "tool_name": response.tool_name,
            "success": response.success,
            "output": response.output,
            "output_text": response.output_text,
            "error": response.error,
            "error_code": response.error_code,
            "duration_ms": response.duration_ms,
            "metadata": response.metadata,
            "created_at": response.created_at.isoformat(),
        }

    def from_exception(
        self,
        request_id: str,
        tool_name: str,
        exc: Exception,
        *,
        duration_ms: int | None = None,
    ) -> ToolResponse:
        """
        Standardized conversion of an arbitrary exception into a
        ToolResponse, used by callers (e.g. ToolManager) that catch an
        exception outside the normal executor flow (validation errors,
        selection errors) and still need a uniform response shape.
        """
        error_code = exc.code if isinstance(exc, ToolError) else exc.__class__.__name__
        return ToolResponse(
            request_id=request_id,
            tool_name=tool_name,
            success=False,
            error=str(exc),
            error_code=error_code,
            duration_ms=duration_ms,
        )

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def _safe_serialize(self, value: Any) -> Any:
        """
        Ensure ``value`` is JSON-serialisable, recursively falling back
        to string conversion for anything json.dumps cannot handle.
        """
        try:
            json.dumps(value)
            return value
        except (TypeError, ValueError):
            return self._coerce(value)

    def _coerce(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {str(k): self._safe_serialize(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self._safe_serialize(v) for v in value]
        if hasattr(value, "__dict__"):
            try:
                return self._safe_serialize(vars(value))
            except Exception:
                return str(value)
        return str(value)