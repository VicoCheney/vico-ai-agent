"""Context Manager — manages conversation context within a token budget."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from vico.config.types.config import ContextStats
from vico.core.types import (
    ContentBlock,
    Message,
    TextBlock,
    TokenUsage,
    ToolResultBlock,
    ToolUseBlock,
)
from vico.utils.text_utils import estimate_tokens as _estimate_tokens

logger = logging.getLogger(__name__)

TOOL_DEF_OVERHEAD = 3000  # ~5 tools × ~600 tokens each
_MSG_OVERHEAD_TOKENS = 4  # per-message role/delimiter overhead
_COMPRESSION_SAFETY_MARGIN = 200  # headroom for the summary message itself


class ContextManager:
    """Manages conversation context within a token budget."""

    def __init__(
        self,
        max_tokens: int = 60000,
        reserve_tokens: int = 4096,
        compression_threshold: float = 0.85,
    ) -> None:
        self._messages: list[Message] = []
        self._max_tokens = max_tokens
        self._reserve_tokens = reserve_tokens
        self._compression_threshold = compression_threshold

    # ─── Message Management ─────────────────────────────────────────────────

    def add_user_message(self, text: str) -> None:
        self._messages.append(
            Message(
                role="user",
                content=text,
                id=self._gen_id(),
                timestamp=self._now_ms(),
            )
        )

    def add_assistant_message(
        self,
        text: str,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> None:
        blocks: list[ContentBlock] = []
        if text:
            blocks.append(TextBlock(text=text))
        for tc in tool_calls or []:
            blocks.append(ToolUseBlock(id=tc["id"], name=tc["name"], input=tc["input"]))

        content: str | list[ContentBlock]
        if len(blocks) == 1 and isinstance(blocks[0], TextBlock):
            content = text
        else:
            content = blocks

        self._messages.append(
            Message(
                role="assistant",
                content=content,
                id=self._gen_id(),
                timestamp=self._now_ms(),
            )
        )

    def add_tool_result(
        self,
        tool_use_id: str,
        tool_name: str,
        content: str,
        is_error: bool = False,
    ) -> None:
        self._messages.append(
            Message(
                role="tool",
                content=[ToolResultBlock(tool_use_id=tool_use_id, content=content, is_error=is_error)],
                id=self._gen_id(),
                timestamp=self._now_ms(),
            )
        )

    def update_last_usage(self, usage: TokenUsage) -> None:
        for msg in reversed(self._messages):
            if msg.role == "assistant":
                msg.usage = usage
                break

    def get_messages(self) -> list[Message]:
        return list(self._messages)

    def clear(self) -> None:
        self._messages.clear()

    # ─── Token Estimation ─────────────────────────────────────────────

    def estimate_tokens(self, text: str) -> int:
        return _estimate_tokens(text)

    def estimate_message_tokens(self, msg: Message) -> int:
        if isinstance(msg.content, str):
            raw = msg.content
        else:
            parts: list[str] = []
            for b in msg.content:
                if isinstance(b, TextBlock):
                    parts.append(b.text)
                elif isinstance(b, ToolUseBlock):
                    parts.append(str(b.input))
                elif isinstance(b, ToolResultBlock):
                    parts.append(b.content)
            raw = "".join(parts)
        return self.estimate_tokens(raw) + _MSG_OVERHEAD_TOKENS

    def estimate_total_tokens(self, system_prompt: str) -> int:
        system_tokens = self.estimate_tokens(system_prompt)
        msg_tokens = sum(self.estimate_message_tokens(m) for m in self._messages)
        return system_tokens + msg_tokens + TOOL_DEF_OVERHEAD

    # ─── Context Compression ──────────────────────────────────────────────

    def maybe_compress(self, system_prompt: str) -> bool:
        """Truncate context if approaching the token limit.

        Walks backwards keeping recent message units within budget. Assistant
        tool calls and their following tool results are kept together so the
        provider never receives orphaned tool messages.
        Returns True if truncation occurred.
        """
        total = self.estimate_total_tokens(system_prompt)
        budget = self._max_tokens - self._reserve_tokens

        if budget <= 0 or total / budget < self._compression_threshold:
            return False

        system_tokens = self.estimate_tokens(system_prompt)
        recent_budget = budget - system_tokens - TOOL_DEF_OVERHEAD - _COMPRESSION_SAFETY_MARGIN
        if recent_budget <= 0:
            recent_budget = max(1, budget // 4)

        units = self._message_units()
        kept_units: list[list[Message]] = []
        used = 0
        for unit in reversed(units):
            if self._is_orphan_tool_unit(unit):
                continue
            unit_tokens = sum(self.estimate_message_tokens(msg) for msg in unit)
            if used + unit_tokens > recent_budget and kept_units:
                break
            kept_units.insert(0, unit)
            used += unit_tokens

        kept = [msg for unit in kept_units for msg in unit]
        removed_count = len(self._messages) - len(kept)

        if not kept:
            for unit in reversed(units):
                if not self._is_orphan_tool_unit(unit):
                    kept = list(unit)
                    break
            removed_count = len(self._messages) - len(kept)

        if removed_count <= 0:
            logger.warning(
                "ContextManager: cannot compress further (%d messages remain).",
                len(self._messages),
            )
            return False

        summary = Message(
            role="system",
            content=(
                f"[Context note: {removed_count} earlier messages were omitted to stay within budget. "
                "The conversation continues below.]"
            ),
            id=self._gen_id(),
            timestamp=self._now_ms(),
        )
        self._messages = [summary, *kept]
        return True

    def _message_units(self) -> list[list[Message]]:
        """Group assistant tool calls with their immediate tool results."""
        units: list[list[Message]] = []
        i = 0
        while i < len(self._messages):
            msg = self._messages[i]
            if self._has_tool_use(msg):
                unit = [msg]
                i += 1
                while i < len(self._messages) and self._messages[i].role == "tool":
                    unit.append(self._messages[i])
                    i += 1
                units.append(unit)
                continue
            units.append([msg])
            i += 1
        return units

    @staticmethod
    def _has_tool_use(msg: Message) -> bool:
        return isinstance(msg.content, list) and any(isinstance(block, ToolUseBlock) for block in msg.content)

    @staticmethod
    def _is_orphan_tool_unit(unit: list[Message]) -> bool:
        return bool(unit) and unit[0].role == "tool"

    def debug_messages(self, limit: int = 12) -> list[dict[str, str | int]]:
        """Return a compact tail of messages for CLI diagnostics."""
        rows: list[dict[str, str | int]] = []
        for msg in self._messages[-limit:]:
            if isinstance(msg.content, str):
                preview = msg.content
            else:
                preview = " ".join(block.type for block in msg.content)
            preview = preview.replace("\n", " ").strip()
            if len(preview) > 120:
                preview = preview[:117] + "..."
            rows.append(
                {
                    "role": msg.role,
                    "id": msg.id,
                    "chars": len(preview),
                    "preview": preview,
                }
            )
        return rows

    # ─── Stats ────────────────────────────────────────────────────────────

    def get_stats(self, system_prompt: str) -> ContextStats:
        estimated = self.estimate_total_tokens(system_prompt)
        usage_pct = min((estimated / self._max_tokens) * 100, 100.0)
        return ContextStats(
            message_count=len(self._messages),
            estimated_tokens=estimated,
            max_tokens=self._max_tokens,
            usage_percent=usage_pct,
            is_near_limit=usage_pct >= self._compression_threshold * 100,
        )

    # ─── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _gen_id() -> str:
        return uuid.uuid4().hex[:12]

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)
