"""Built-in tools — file I/O, search, and shell execution.

All tools inherit from the ``Tool`` abstract base class defined in
``vico.core.types``.  New tools should be added here and appended to
``BUILTIN_TOOLS`` so the agent loop picks them up automatically.
"""

from __future__ import annotations

from vico.tools.bash import BashTool
from vico.tools.edit import EditTool
from vico.tools.read import ReadTool
from vico.tools.search import SearchTool
from vico.tools.write import WriteTool

BUILTIN_TOOLS = [
    ReadTool(),
    SearchTool(),
    WriteTool(),
    EditTool(),
    BashTool(),
]

__all__ = [
    "BUILTIN_TOOLS",
    "BashTool",
    "EditTool",
    "ReadTool",
    "SearchTool",
    "WriteTool",
]
