"""IOS Tools — Tool Validator."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.tools.base import BaseToolComponent
from app.tools.exceptions import ToolPermissionError, ToolValidationError
from app.tools.interfaces import ITool, IToolValidator
from app.tools.types import ToolRequest

# Argument keys/value patterns that are never permitted regardless of
# schema, guarding against path traversal / injection style payloads
# reaching a concrete tool plugin.
_UNSAFE_STRING_MARKERS = ("../", "..\\", "\x00")


@dataclass
class FieldValidationIssue:
    field: str
    message: str


class ToolValidator(BaseToolComponent, IToolValidator):
    """
    Pre-execution validation gate for tool requests.

    Validates:
    - **Schema conformance** — request.arguments against
      tool.metadata.input_schema (lightweight JSON-Schema-subset checker:
      type, required, enum — sufficient for tool argument validation
      without pulling in a full JSON Schema library dependency).
    - **Permission** — required_permission (if any) must be present in
      request.permissions.
    - **Execution policy** — requested timeout_seconds must not exceed
      tool.metadata.max_timeout_seconds.
    - **Safety constraints** — string argument values are scanned for
      path-traversal / null-byte markers as a defence-in-depth measure;
      deep payload sanitisation remains the concrete tool plugin's
      responsibility, this is a first line of defence only.

    Raises typed exceptions rather than returning a boolean so that
    ToolManager can propagate a precise, structured error back to the
    caller without re-deriving the failure reason.
    """

    async def validate(self, request: ToolRequest, tool: ITool) -> None:
        async with self._span("validate_request", tool_name=tool.metadata.name):
            self._validate_permission(request, tool)
            self._validate_timeout_policy(request, tool)
            issues = self._validate_schema(request.arguments, tool.metadata.input_schema)
            issues.extend(self._validate_safety(request.arguments))

            if issues:
                raise ToolValidationError(
                    f"Validation failed for tool '{tool.metadata.name}': "
                    f"{len(issues)} issue(s).",
                    details={
                        "tool_name": tool.metadata.name,
                        "issues": [f"{i.field}: {i.message}" for i in issues],
                    },
                )

            self._log.debug("request_validated", tool_name=tool.metadata.name)

    # ------------------------------------------------------------------
    # Permission
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_permission(request: ToolRequest, tool: ITool) -> None:
        required = tool.metadata.required_permission
        if required and required not in set(request.permissions):
            raise ToolPermissionError(
                f"Permission '{required}' is required to invoke tool "
                f"'{tool.metadata.name}'.",
                details={"tool_name": tool.metadata.name, "required_permission": required},
            )

    # ------------------------------------------------------------------
    # Timeout policy
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_timeout_policy(request: ToolRequest, tool: ITool) -> None:
        if request.timeout_seconds is None:
            return
        if request.timeout_seconds > tool.metadata.max_timeout_seconds:
            raise ToolValidationError(
                f"Requested timeout ({request.timeout_seconds}s) exceeds "
                f"tool '{tool.metadata.name}' maximum "
                f"({tool.metadata.max_timeout_seconds}s).",
                details={"tool_name": tool.metadata.name},
            )
        if request.timeout_seconds <= 0:
            raise ToolValidationError(
                "timeout_seconds must be a positive number.",
                details={"tool_name": tool.metadata.name},
            )

    # ------------------------------------------------------------------
    # Schema validation (lightweight JSON-Schema subset)
    # ------------------------------------------------------------------

    def _validate_schema(
        self, arguments: dict[str, Any], schema: dict[str, Any]
    ) -> list[FieldValidationIssue]:
        if not schema:
            return []

        issues: list[FieldValidationIssue] = []
        properties: dict[str, Any] = schema.get("properties", {})
        required_fields: list[str] = schema.get("required", [])

        for field_name in required_fields:
            if field_name not in arguments:
                issues.append(
                    FieldValidationIssue(field_name, "required field is missing")
                )

        for field_name, value in arguments.items():
            field_schema = properties.get(field_name)
            if field_schema is None:
                continue  # unknown fields are tolerated, not rejected
            issues.extend(self._check_field(field_name, value, field_schema))

        return issues

    def _check_field(
        self, field_name: str, value: Any, field_schema: dict[str, Any]
    ) -> list[FieldValidationIssue]:
        issues: list[FieldValidationIssue] = []
        expected_type = field_schema.get("type")
        if expected_type and not self._type_matches(value, expected_type):
            issues.append(
                FieldValidationIssue(
                    field_name,
                    f"expected type '{expected_type}', got '{type(value).__name__}'",
                )
            )

        enum_values = field_schema.get("enum")
        if enum_values is not None and value not in enum_values:
            issues.append(
                FieldValidationIssue(
                    field_name, f"value not in allowed set {enum_values}"
                )
            )

        minimum = field_schema.get("minimum")
        if minimum is not None and isinstance(value, (int, float)) and value < minimum:
            issues.append(
                FieldValidationIssue(field_name, f"value below minimum {minimum}")
            )

        maximum = field_schema.get("maximum")
        if maximum is not None and isinstance(value, (int, float)) and value > maximum:
            issues.append(
                FieldValidationIssue(field_name, f"value above maximum {maximum}")
            )

        return issues

    @staticmethod
    def _type_matches(value: Any, expected_type: str) -> bool:
        type_map: dict[str, type | tuple[type, ...]] = {
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
            "array": list,
            "object": dict,
            "null": type(None),
        }
        expected = type_map.get(expected_type)
        if expected is None:
            return True  # unknown declared type — do not block on it
        if expected_type == "integer" and isinstance(value, bool):
            return False  # bool is technically int in Python; exclude
        return isinstance(value, expected)

    # ------------------------------------------------------------------
    # Safety constraints
    # ------------------------------------------------------------------

    def _validate_safety(
        self, arguments: dict[str, Any]
    ) -> list[FieldValidationIssue]:
        issues: list[FieldValidationIssue] = []
        for key, value in arguments.items():
            if isinstance(value, str):
                for marker in _UNSAFE_STRING_MARKERS:
                    if marker in value:
                        issues.append(
                            FieldValidationIssue(
                                key, f"value contains disallowed pattern '{marker!r}'"
                            )
                        )
        return issues