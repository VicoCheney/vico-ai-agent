"""Abstract base class for all LLM instances.

Separated from ``vico.core.types`` so that the type-definition module
contains only pure data types (dataclasses and type aliases), while
behavioural contracts (abstract interfaces) live alongside their
concrete implementations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from vico.llm.types.request import LLMRequest
from vico.llm.types.stream import StreamChunk


class LLM(ABC):
    """Abstract base class for all LLM instances (provider + model + config).

    Subclasses that hold resources (e.g. HTTP connection pools via
    AsyncOpenAI) should override ``aclose()`` to release them.
    ``AgentLoop.run()`` calls ``aclose()`` in its ``finally`` block so
    that Ctrl+C during streaming does not leak connections.
    """

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def stream(self, request: LLMRequest) -> AsyncIterator[StreamChunk]: ...

    @abstractmethod
    def get_max_context_tokens(self) -> int: ...

    @abstractmethod
    def supports_tool_use(self) -> bool: ...

    @abstractmethod
    def supports_vision(self) -> bool: ...

    async def aclose(self) -> None:
        """Release resources held by this LLM instance.

        Default implementation is a no-op; subclasses with open HTTP
        clients or connection pools should override this to close them.
        Called by ``AgentLoop.run()`` on exit / cancellation.
        """
