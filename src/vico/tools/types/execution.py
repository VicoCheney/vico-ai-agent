"""Tool execution context and result types."""

from __future__ import annotations

from asyncio import Event
from dataclasses import dataclass, field
from typing import Any, Literal, cast

ToolRiskLevel = Literal["low", "medium", "high"]
ApprovalLabel = Literal["auto approved", "approved", "approved always", "denied"]


@dataclass
class ToolExecutionContext:
    cwd: str
    env: dict[str, str] = field(default_factory=dict)
    cancel_event: Event = field(default_factory=Event)
    timeout_ms: int = 30000

    @property
    def cancelled(self) -> bool:
        """True if the agent has been cancelled."""
        return self.cancel_event.is_set()


@dataclass
class ApprovalInfo:
    label: ApprovalLabel


@dataclass
class SkillActivationInfo:
    skill_id: str
    skill_name: str = ""
    arguments: str = ""
    skill_dir: str = ""
    body: str = ""


@dataclass
class ToolResultMeta:
    approval: ApprovalInfo | None = None
    skill_activation: SkillActivationInfo | None = None


@dataclass
class ToolResult:
    success: bool
    output: str
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    meta: ToolResultMeta = field(default_factory=ToolResultMeta)

    def __post_init__(self) -> None:
        """Populate structured metadata from the legacy metadata dict when present."""
        approval = self.metadata.get("approval")
        if approval in ("auto approved", "approved", "approved always", "denied"):
            self.meta.approval = ApprovalInfo(label=approval)

        if self.metadata.get("skill_activation"):
            self.meta.skill_activation = SkillActivationInfo(
                skill_id=str(self.metadata.get("skill_id", "")),
                skill_name=str(self.metadata.get("skill_name", "")),
                arguments=str(self.metadata.get("skill_arguments", "")),
                skill_dir=str(self.metadata.get("skill_dir", "")),
                body=str(self.metadata.get("skill_body", "")),
            )

    def set_approval(self, label: ApprovalLabel) -> None:
        self.meta.approval = ApprovalInfo(label=label)
        self.metadata["approval"] = label

    def approval_label(self) -> ApprovalLabel | None:
        if self.meta.approval:
            return self.meta.approval.label
        approval = self.metadata.get("approval")
        if approval in ("auto approved", "approved", "approved always", "denied"):
            return cast(ApprovalLabel, approval)
        return None

    def set_skill_activation(self, info: SkillActivationInfo) -> None:
        self.meta.skill_activation = info
        self.metadata.update(
            {
                "skill_activation": True,
                "skill_id": info.skill_id,
                "skill_name": info.skill_name,
                "skill_arguments": info.arguments,
                "skill_dir": info.skill_dir,
                "skill_body": info.body,
            }
        )
