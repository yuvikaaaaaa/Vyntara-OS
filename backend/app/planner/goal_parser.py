"""IOS Planner — Goal Parser."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from app.ai_core.router.model_router import ModelRouter
from app.ai_core.types import ChatMessage, ChatRequest, GenerationConfig, ModelCapability, RoutingContext
from app.planner.base import BasePlanner
from app.planner.exceptions import GoalParsingError
from app.planner.interfaces import IGoalParser
from app.planner.types import Constraint, ConstraintType, Goal, ParsedGoal, SuccessCriterion

_PARSE_INSTRUCTIONS = """Analyze the following goal and extract a structured objective.
Return ONLY valid JSON matching this schema, no explanation, no markdown fences:

{
  "objective": "one-sentence restatement of the core objective",
  "constraints": [{"type": "deadline|resource_limit|prerequisite_order|scheduling|dependency", "description": "...", "value": null}],
  "resources": ["resource1", "resource2"],
  "assumptions": ["assumption1"],
  "success_criteria": [{"description": "...", "measurable": true}]
}

Goal: {goal_text}"""


class GoalParser(BasePlanner, IGoalParser):
    """
    Converts a raw natural-language Goal into a structured ParsedGoal.

    Uses AI Core's ModelRouter for LLM-assisted extraction of objective,
    constraints, deadlines, resources, assumptions, and success criteria.
    Falls back to a lightweight heuristic parser if the LLM call fails or
    returns malformed JSON, so goal parsing never hard-blocks planning.
    """

    def __init__(self, model_router: ModelRouter, *, model_id: str | None = None) -> None:
        super().__init__()
        self._router = model_router
        self._model_id = model_id

    async def parse(self, goal: Goal) -> ParsedGoal:
        async with self._span("parse_goal"):
            if not goal.text or not goal.text.strip():
                raise GoalParsingError("Goal text cannot be empty.")

            try:
                structured = await self._llm_parse(goal.text)
            except Exception as exc:
                self._log.warning("goal_llm_parse_failed_using_heuristic", exc=str(exc))
                structured = self._heuristic_parse(goal.text)

            parsed = ParsedGoal(
                objective=structured.get("objective") or goal.text.strip(),
                constraints=self._build_constraints(structured.get("constraints", [])),
                deadline=self._extract_deadline(structured.get("constraints", [])),
                resources=structured.get("resources", []) or [],
                assumptions=structured.get("assumptions", []) or [],
                success_criteria=[
                    SuccessCriterion(
                        description=c.get("description", ""),
                        measurable=bool(c.get("measurable", False)),
                    )
                    for c in structured.get("success_criteria", []) or []
                ],
                raw_text=goal.text,
                confidence=structured.get("_confidence", 0.8),
            )
            self._log.info(
                "goal_parsed",
                objective_len=len(parsed.objective),
                constraints=len(parsed.constraints),
                confidence=parsed.confidence,
            )
            return parsed

    # ------------------------------------------------------------------
    # LLM-assisted parsing
    # ------------------------------------------------------------------

    async def _llm_parse(self, goal_text: str) -> dict:
        prompt = _PARSE_INSTRUCTIONS.replace("{goal_text}", goal_text)
        request = ChatRequest(
            messages=[ChatMessage(role="user", content=prompt)],
            model_id=self._model_id or "",
            config=GenerationConfig(temperature=0.1, max_tokens=600),
            timeout_seconds=30.0,
        )
        routing_ctx = None
        if not self._model_id:
            routing_ctx = RoutingContext(
                required_capabilities=[ModelCapability.TEXT_GENERATION, ModelCapability.JSON_MODE]
            )
        response = await self._router.chat(request, routing_context=routing_ctx)
        content = response.content.strip()
        # Strip markdown fences if the model added them despite instructions
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.MULTILINE).strip()
        data = json.loads(content)
        data["_confidence"] = 0.85
        return data

    # ------------------------------------------------------------------
    # Heuristic fallback
    # ------------------------------------------------------------------

    def _heuristic_parse(self, goal_text: str) -> dict:
        """
        Lightweight regex-based extraction used when LLM parsing fails.
        Produces a lower-confidence but structurally valid ParsedGoal.
        """
        constraints: list[dict] = []

        deadline_match = re.search(
            r"\b(by|before|deadline[:\s]+)\s*([\w\s,]+?\d{4}|\btoday\b|\btomorrow\b)",
            goal_text,
            re.IGNORECASE,
        )
        if deadline_match:
            constraints.append(
                {
                    "type": ConstraintType.DEADLINE.value,
                    "description": deadline_match.group(0).strip(),
                    "value": None,
                }
            )

        resource_matches = re.findall(r"using ([\w\s,]+?)(?:\.|,|$)", goal_text, re.IGNORECASE)
        resources = [r.strip() for r in resource_matches if r.strip()]

        return {
            "objective": goal_text.strip(),
            "constraints": constraints,
            "resources": resources,
            "assumptions": [],
            "success_criteria": [],
            "_confidence": 0.4,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _build_constraints(raw: list[dict]) -> list[Constraint]:
        constraints: list[Constraint] = []
        for c in raw:
            try:
                ctype = ConstraintType(c.get("type", "scheduling"))
            except ValueError:
                ctype = ConstraintType.SCHEDULING
            constraints.append(
                Constraint(
                    type=ctype,
                    description=c.get("description", ""),
                    value=c.get("value"),
                )
            )
        return constraints

    @staticmethod
    def _extract_deadline(raw_constraints: list[dict]) -> datetime | None:
        for c in raw_constraints:
            if c.get("type") == ConstraintType.DEADLINE.value and c.get("value"):
                try:
                    return datetime.fromisoformat(c["value"])
                except (ValueError, TypeError):
                    continue
        return None