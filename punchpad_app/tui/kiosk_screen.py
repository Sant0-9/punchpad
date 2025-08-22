from __future__ import annotations

import os
import sys
import shutil
import time
from typing import Callable


# Render a fullscreen banner text. Center if terminal dimensions allow.
# status: ok_in, ok_out, blocked, locked, error
# Returns a single string payload (with newlines) to print.
def render_banner(status: str, line1: str, line2: str | None = None) -> str:
    size = shutil.get_terminal_size(fallback=(80, 24))
    width, height = size.columns, size.lines

    # Simple mapping to title lines
    titles = {
        "ok_in": "PUNCHED IN",
        "ok_out": "PUNCHED OUT",
        "blocked": "Duplicate punch blocked",
        "locked": "Locked",
        "error": "Error",
        "queued": "Queued",
    }
    title = titles.get(status, status.upper())

    # If very small terminal, do simple output
    if width < 30 or height < 7:
        lines = [title, line1]
        if line2:
            lines.append(line2)
        return "\n".join(lines) + "\n"

    # Build a framed banner
    border = "=" * min(width, max(30, len(title) + 8))
    content_lines = [title, "", line1]
    if line2:
        content_lines.append(line2)

    # Determine vertical centering
    total_content = len(content_lines) + 2  # include border spacing
    top_pad = max(0, (height - total_content) // 2)

    out_lines: list[str] = []
    out_lines.extend([""] * top_pad)

    def center(s: str) -> str:
        if len(s) >= width:
            return s[:width]
        pad = (width - len(s)) // 2
        return (" " * pad) + s

    out_lines.append(center(border))
    for ln in content_lines:
        out_lines.append(center(ln))
    out_lines.append(center(border))

    # Ensure final newline
    return "\n".join(out_lines) + "\n"


# prompt_pin reads digits with masking, supporting Backspace and Enter.
# - getch_func: () -> str that returns a single character
# - echo: if True, prints '*' per digit; if False, prints nothing
# Raises KeyboardInterrupt on Esc or Ctrl+C.
def prompt_pin(getch_func: Callable[[], str], echo: bool = False) -> str:
    buf: list[str] = []

    def is_backspace(ch: str) -> bool:
        return ch in ("\b", "\x08", "\x7f")

    while True:
        ch = getch_func()
        if ch == "\r" or ch == "\n":
            # Submit on Enter
            return "".join(buf)
        if ch == "\x1b" or ch == "\x03":
            # Esc or Ctrl+C → abort
            raise KeyboardInterrupt
        if is_backspace(ch):
            if buf:
                buf.pop()
                if echo:
                    # Move back, overwrite with space, move back
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()
            continue
        if ch.isdigit():
            buf.append(ch)
            if echo:
                sys.stdout.write("*")
                sys.stdout.flush()
            continue
        # Ignore any other chars


# Utilities for kiosk run mode

def _clear_screen() -> None:
    # ANSI clear; works on most terminals; fallback to os.system if needed
    try:
        sys.stdout.write("\x1b[2J\x1b[H")
        sys.stdout.flush()
    except Exception:
        os.system("cls" if os.name == "nt" else "clear")


def _sleep_ms(ms: int) -> None:
    time.sleep(max(0, ms) / 1000.0)
