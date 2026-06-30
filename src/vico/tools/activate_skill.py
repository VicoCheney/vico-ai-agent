"""activate_skill — load a Skill pack into the conversation context.

The tool itself validates and returns the requested Skill body in metadata.
AgentLoop performs the actual context injection after writing the tool result,
preserving the required assistant tool_call → tool result message ordering.
"""

from __future__ import annotations

from typing import Any

from vico.core.skill_provider import ISkillProvider
from vico.tools.base import Tool
from vico.tools.types.definition import ToolDefinition, ToolParameterSchema
from vico.tools.types.execution import ToolExecutionContext, ToolResult, ToolRiskLevel


class ActivateSkillTool(Tool):
    """Activate a discovered Skill by ID. Risk level: low."""

    def __init__(self, skill_loader: ISkillProvider) -> None:
        self._skill_loader = skill_loader

    @property
    def risk_level(self) -> ToolRiskLevel:
        return "low"

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="activate_skill",
            description=(
                "Load the full instructions for a discovered Skill when the user's task clearly "
                "matches that Skill. Prefer this structured tool over emitting <use_skill> text. "
                "Do not call it for Skills marked disable_model_invocation/manual-only."
            ),
            parameters=ToolParameterSchema(
                type="object",
                properties={
                    "skill_id": {
                        "type": "string",
                        "description": "The Skill ID to activate, e.g. code-review.",
                    },
                    "arguments": {
                        "type": "string",
                        "description": "Optional user arguments to pass into the Skill instructions.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Brief reason this Skill matches the current task.",
                    },
                },
                required=["skill_id"],
            ),
        )

    async def execute(
        self,
        params: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        skill_id = str(params["skill_id"]).strip()
        arguments = str(params.get("arguments", "")).strip()
        reason = str(params.get("reason", "")).strip()

        if not skill_id:
            return ToolResult(success=False, output="", error="skill_id is required.")

        content = self._skill_loader.get_skill_content(skill_id)
        if not content:
            return ToolResult(success=False, output="", error=f"Skill not found: {skill_id}")

        if content.meta.disable_model_invocation:
            return ToolResult(
                success=False,
                output="",
                error=(
                    f"Skill '{content.meta.skill_id}' is manual-only. "
                    f"Ask the user to run `/skill {content.meta.skill_id}` if they want it."
                ),
            )

        output = f"Activated skill '{content.meta.skill_id}' ({content.meta.name})."
        if reason:
            output += f"\nReason: {reason}"

        return ToolResult(
            success=True,
            output=output,
            metadata={
                "skill_activation": True,
                "skill_id": content.meta.skill_id,
                "skill_name": content.meta.name,
                "skill_body": content.body,
                "skill_arguments": arguments,
                "skill_dir": str(content.meta.skill_dir),
            },
        )
