"""Tool definition types — schema and descriptor sent to the LLM."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolParameterSchema:
    type: str
    properties: dict[str, Any]
    required: list[str] = field(default_factory=list)
    additional_properties: bool = False

    def to_dict(self) -> dict[str, Any]:
        schema: dict[str, Any] = {
            "type": self.type,
            "properties": self.properties,
        }
        if self.required:
            schema["required"] = self.required
        if not self.additional_properties:
            schema["additionalProperties"] = False
        return schema


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: ToolParameterSchema

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters.to_dict(),
            },
        }
