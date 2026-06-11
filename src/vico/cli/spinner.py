"""Spinner controllers for the terminal renderer.

Provides two reusable controllers:
  SpinnerController  — single-line phase-progressive spinner (waiting / thinking / generating)
  ToolRowController  — multi-row tool-call spinner with in-place overwrite
"""

from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import Callable

from vico.cli import theme

_RESET = theme.RESET
_DIM = theme.DIM
_ITALIC = theme.ITALIC
_CLEAR_LINE = theme.CLEAR_LINE
_FRAMES = theme.FRAMES


# ─── SpinnerController ────────────────────────────────────────────────────────


class SpinnerController:
    """Manages a single animated spinner line (waiting / thinking / generating).

    Each instance handles one "kind" of spinner; the renderer owns three:
    waiting, thinking, and generating.
    """

    def __init__(self) -> None:
        self.task: asyncio.Task | None = None  # type: ignore[type-arg]
        self.start_time: float = 0.0
        self.active: bool = False
        self._frame_ref: list[int] = [0]  # shared mutable frame counter reference

    def attach_frame_counter(self, counter: list[int]) -> None:
        """Share the renderer-level frame counter so all spinners stay in sync."""
        self._frame_ref = counter

    def reset(self) -> None:
        self.task = None
        self.start_time = 0.0
        self.active = False

    def start(self) -> None:
        """Mark spinner as active and record start time."""
        self.start_time = time.monotonic()
        self.active = True

    def stop_line(self) -> None:
        """Cancel the task and clear the spinner line from stdout."""
        if self.task and not self.task.done():
            self.task.cancel()
        self.task = None
        if self.active:
            sys.stdout.write(f"\r{_CLEAR_LINE}")
            sys.stdout.flush()
            self.active = False

    def cancel_task(self) -> None:
        """Cancel the task only (no line erasure)."""
        if self.task and not self.task.done():
            self.task.cancel()
        self.task = None

    async def run_phase_spinner(self, phases: list[tuple[float, str]]) -> None:
        """Phase-progressive spinner animation loop. Runs until cancelled."""
        try:
            while True:
                await asyncio.sleep(0.08)
                self._frame_ref[0] += 1
                frame = _FRAMES[self._frame_ref[0] % len(_FRAMES)]
                elapsed = time.monotonic() - self.start_time
                label = phases[0][1]
                for threshold, phase_label in phases:
                    if elapsed >= threshold:
                        label = phase_label
                line = f"\r{_CLEAR_LINE}{_DIM}{frame} {label}...  {elapsed:.1f}s{_RESET}"
                sys.stdout.write(line)
                sys.stdout.flush()
        except asyncio.CancelledError:
            pass


# ─── ToolRowController ────────────────────────────────────────────────────────


class ToolRowController:
    """Manages animated spinner rows for in-flight tool calls.

    Each tool call gets a dedicated output row; completed rows are overwritten
    in-place with their final status.
    """

    def __init__(self) -> None:
        self.batch: dict[str, tuple[int, str, str]] = {}
        self.size: int = 0
        self.done_ids: set[str] = set()
        self.overwrite_tasks: list[asyncio.Task] = []  # type: ignore[type-arg]
        self.spinner_task: asyncio.Task | None = None  # type: ignore[type-arg]
        self.render_lock: asyncio.Lock = asyncio.Lock()
        self._frame_ref: list[int] = [0]

    def attach_frame_counter(self, counter: list[int]) -> None:
        """Share the renderer-level frame counter."""
        self._frame_ref = counter

    def clear(self) -> None:
        self.batch.clear()
        self.size = 0
        self.done_ids.clear()
        self.overwrite_tasks.clear()
        self.spinner_task = None

    def stop_spinner(self) -> None:
        if self.spinner_task and not self.spinner_task.done():
            self.spinner_task.cancel()
            self.spinner_task = None

    def overwrite_sync(self, idx: int, new_line: str) -> None:
        """Synchronously overwrite a tool row in-place.

        Atomic w.r.t. spinner loop because asyncio is single-threaded.
        """
        lines_up = self.size - idx
        if lines_up <= 0:
            sys.stdout.write(f"\r{_CLEAR_LINE}{new_line}\n")
        elif lines_up == 1:
            sys.stdout.write(f"\033[1A\r{_CLEAR_LINE}{new_line}\n")
        else:
            sys.stdout.write(f"\033[{lines_up}A\r{_CLEAR_LINE}{new_line}\n\033[{lines_up - 1}B")
        sys.stdout.flush()

    async def overwrite_async(self, idx: int, new_line: str) -> None:
        """Overwrite a tool row in-place under render_lock."""
        async with self.render_lock:
            lines_up = self.size - idx
            if lines_up <= 0:
                sys.stdout.write(f"\r{_CLEAR_LINE}{new_line}")
            else:
                sys.stdout.write(f"\033[{lines_up}A\r{_CLEAR_LINE}{new_line}\n\033[{lines_up - 1}B")
            sys.stdout.flush()

    async def spin_loop(self, fmt_running_fn: Callable[[str, str, str], str]) -> None:
        """Animate spinner rows for all pending tool calls."""

        try:
            while True:
                await asyncio.sleep(0.08)
                self._frame_ref[0] += 1
                frame = _FRAMES[self._frame_ref[0] % len(_FRAMES)]
                async with self.render_lock:
                    pending = sorted(
                        [(tid, info) for tid, info in self.batch.items() if tid not in self.done_ids],
                        key=lambda x: x[1][0],
                    )
                    if not pending:
                        break

                    topmost_idx = pending[0][1][0]
                    lines_to_top = self.size - topmost_idx
                    buf = f"\033[{lines_to_top}A"

                    prev_idx = topmost_idx
                    for _, (idx, name, param) in pending:
                        gap = idx - prev_idx
                        if gap > 0:
                            buf += f"\033[{gap}B"
                        buf += f"\r{_CLEAR_LINE}{fmt_running_fn(frame, name, param)}"
                        prev_idx = idx

                    rows_to_bottom = self.size - prev_idx
                    if rows_to_bottom > 0:
                        buf += f"\033[{rows_to_bottom}B"
                    buf += "\r"

                    sys.stdout.write(buf)
                    sys.stdout.flush()
        finally:
            self.spinner_task = None
