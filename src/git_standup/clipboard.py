"""Clipboard copy and single-keypress input — no third-party dependencies."""

from __future__ import annotations

import shutil
import subprocess
import sys


def _clipboard_command() -> list[str] | None:
    """Return the OS clipboard command, or None when none is available."""
    if sys.platform == "darwin":
        if shutil.which("pbcopy"):
            return ["pbcopy"]
        return None
    if sys.platform.startswith("win"):
        if shutil.which("clip"):
            return ["clip"]
        return None
    # Linux / other Unix: prefer Wayland, then X11 tools.
    candidates = (
        ["wl-copy"],
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
    )
    for command in candidates:
        if shutil.which(command[0]):
            return command
    return None


def clipboard_available() -> bool:
    """Return True when an OS clipboard tool is available."""
    return _clipboard_command() is not None


def copy_to_clipboard(text: str) -> bool:
    """Copy text to the OS clipboard. Return True on success."""
    command = _clipboard_command()
    if command is None:
        return False
    try:
        subprocess.run(command, input=text, text=True, check=True)
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def read_single_key() -> str:
    """Read a single keypress without waiting for Enter.

    Falls back to a line-based read when raw terminal mode is unavailable
    (e.g. non-interactive input or unsupported platform).
    """
    if not sys.stdin.isatty():
        return sys.stdin.readline().strip()[:1]

    try:  # Windows
        import msvcrt  # type: ignore[import-not-found]

        return msvcrt.getwch()
    except ImportError:
        pass

    try:  # Unix
        import termios
        import tty
    except ImportError:
        return sys.stdin.readline().strip()[:1]

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
