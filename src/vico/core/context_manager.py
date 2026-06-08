"""
Context Manager
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

from vico.core.types import (
    ContentBlock,
    Message,
    TextBlock,
    TokenUsage,
    ToolResultBlock,
    ToolUseBlock,
)

CHARS_PER_TOKEN = 4  # default ratio for ASCII / Latin text
CJK_CHARS_PER_TOKEN = 1.5  # CJK ideographs are ~1.5 chars per token
TOOL_DEF_OVERHEAD = 3000  # estimated tokens for tool definitions


def _count_cjk(text: str) -> int:
    """Count CJK Unified Ideographs / Hiragana / Katakana / Hangul code points.

    Counting via codepoint ranges is faster than calling unicodedata for every
    character and is good enough for token-budget estimation.
    """
    n = 0
    for ch in text:
        cp = ord(ch)
        if (
            0x4E00 <= cp <= 0x9FFF  # CJK Unified Ideographs
            or 0x3400 <= cp <= 0x4DBF  # CJK Extension A
            or 0x20000 <= cp <= 0x2A6DF  # CJK Extension B
            or 0x3040 <= cp <= 0x30FF  # Hiragana + Katakana
            or 0xAC00 <= cp <= 0xD7AF  # Hangul Syllables
            or 0xFF00 <= cp <= 0xFFEF  # Full-width forms
        ):
            n += 1
    return n


@dataclass
class ContextStats:
    message_count: int
    estimated_tokens: int
    max_tokens: int
    usage_percent: float
    is_near_limit: bool


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
        tool_calls: list[dict[str, Any]] | None = None,  # [{id, name, input}]
    ) -> None:
        blocks: list[ContentBlock] = []
        if text:
            blocks.append(TextBlock(text=text))
        for tc in tool_calls or []:
            blocks.append(ToolUseBlock(id=tc["id"], name=tc["name"], input=tc["input"]))

        # Store as plain string when there are no tool calls
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
        tool_name: str,  # kept for symmetry / logging
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
        """Estimate token count, accounting for CJK characters.

        Pure ASCII is ~4 chars/token; CJK is ~1.5 chars/token.  We split the
        text into CJK vs non-CJK budgets so a mostly-Chinese conversation
        won't blow past the compression threshold undetected.
        """
        if not text:
            return 1
        cjk = _count_cjk(text)
        other = len(text) - cjk
        est = int(cjk / CJK_CHARS_PER_TOKEN) + (other // CHARS_PER_TOKEN)
        return max(1, est)

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
        return self.estimate_tokens(raw) + 4  # +4 for role/formatting overhead

    def estimate_total_tokens(self, system_prompt: str) -> int:
        system_tokens = self.estimate_tokens(system_prompt)
        msg_tokens = sum(self.estimate_message_tokens(m) for m in self._messages)
        return system_tokens + msg_tokens + TOOL_DEF_OVERHEAD

    # ─── Context Compression ──────────────────────────────────────────────

    def maybe_compress(self, system_prompt: str) -> bool:
        """Compress context if approaching the token limit.

        Keeps recent messages by walking backwards and accumulating token
        estimates until the budget is consumed.  Uses role="user" for the
        summary message to satisfy strict user/assistant alternation rules.

        Returns True if compression occurred.
        """
        total = self.estimate_total_tokens(system_prompt)
        budget = self._max_tokens - self._reserve_tokens

        if total / budget < self._compression_threshold:
            return False

        system_tokens = self.estimate_tokens(system_prompt)
        recent_budget = budget - system_tokens - TOOL_DEF_OVERHEAD - 200
        if recent_budget <= 0:
            recent_budget = budget // 4

        kept: list[Message] = []
        used = 0
        for msg in reversed(self._messages):
            msg_tokens = self.estimate_message_tokens(msg)
            if used + msg_tokens > recent_budget and kept:
                break
            kept.insert(0, msg)
            used += msg_tokens

        removed_count = len(self._messages) - len(kept)
        if removed_count <= 0:
            return False

        summary = Message(
            role="user",
            content=(
                f"[Context note: {removed_count} earlier messages were summarized to save space. "
                "The conversation continues below.]"
            ),
            id=self._gen_id(),
            timestamp=self._now_ms(),
        )
        self._messages = [summary, *kept]
        return True

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
        return uuid.uuid4().hex[:8]

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)
