"""Terminal output: colours, banner, and a small leveled logger.

Stdlib only — no external dependency so recoo stays easy to run.
"""
from __future__ import annotations

import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(s: str) -> str:
    """Strip ANSI colour codes for file/non-tty output."""
    return _ANSI.sub("", s)


class C:
    """ANSI colour codes. Disabled (emptied) when output is not a TTY."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    GREY = "\033[90m"

    @classmethod
    def disable(cls) -> None:
        for name in ("RESET", "BOLD", "DIM", "RED", "GREEN", "YELLOW",
                     "BLUE", "MAGENTA", "CYAN", "GREY"):
            setattr(cls, name, "")


BANNER = r"""

   ____  ___  _________  ____
  / __ \/ _ \/ ___/ __ \/ __ \
 / /_/ /  __/ /__/ /_/ / /_/ /
 \____/\___/\___/\____/\____/

 recoo · modular recon automation · v{ver}
 made by cataract
"""


class Logger:
    """Tiny leveled logger with timestamps and colour."""

    LEVELS = {"debug": 10, "info": 20, "warn": 30, "error": 40, "quiet": 100}

    def __init__(self, level: str = "info", use_color: bool = True):
        if not use_color:
            C.disable()
        self.threshold = self.LEVELS.get(level, 20)
        self._start = time.time()
        self._fh = None  # optional persistent log file handle

    def attach_file(self, path) -> None:
        """Mirror every message (full fidelity, no colour) to a log file.

        The file always records debug-level detail regardless of the
        console threshold, so you can ``tail -f`` it to watch a long or
        seemingly-stuck run from another terminal.
        """
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            self._fh = p.open("a", buffering=1)  # line-buffered
            self._fh.write(f"\n{'='*60}\n"
                           f"# recoo run @ "
                           f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                           f"{'='*60}\n")
        except Exception:
            self._fh = None

    def _to_file(self, tag: str, msg: str) -> None:
        if self._fh is None:
            return
        try:
            self._fh.write(f"{self._stamp()} {tag:<5} {_plain(msg)}\n")
        except Exception:
            pass

    def _stamp(self) -> str:
        return datetime.now().strftime("%H:%M:%S")

    def _emit(self, level: str, tag: str, color: str, msg: str) -> None:
        # The file gets every message in full; the console respects the level.
        self._to_file(tag, msg)
        if self.LEVELS[level] < self.threshold:
            return
        stream = sys.stderr if level in ("warn", "error") else sys.stdout
        stream.write(f"{C.GREY}{self._stamp()}{C.RESET} "
                     f"{color}{tag:<5}{C.RESET} {msg}\n")
        stream.flush()

    def debug(self, msg: str) -> None:
        self._emit("debug", "·", C.GREY, f"{C.DIM}{msg}{C.RESET}")

    def info(self, msg: str) -> None:
        self._emit("info", "[*]", C.CYAN, msg)

    def ok(self, msg: str) -> None:
        self._emit("info", "[+]", C.GREEN, msg)

    def warn(self, msg: str) -> None:
        self._emit("warn", "[!]", C.YELLOW, msg)

    def error(self, msg: str) -> None:
        self._emit("error", "[x]", C.RED, msg)

    def phase(self, name: str, desc: str) -> None:
        if self._fh is not None:
            try:
                self._fh.write(f"\n--- {name}  {desc} ---\n")
            except Exception:
                pass
        if self.LEVELS["info"] < self.threshold:
            return
        line = "─" * 58
        sys.stdout.write(
            f"\n{C.BLUE}{line}{C.RESET}\n"
            f"{C.BOLD}{C.BLUE}▌ {name}{C.RESET}  {C.DIM}{desc}{C.RESET}\n"
            f"{C.BLUE}{line}{C.RESET}\n")
        sys.stdout.flush()

    def banner(self, version: str) -> None:
        if self.LEVELS["info"] < self.threshold:
            return
        sys.stdout.write(C.CYAN + BANNER.format(ver=version) + C.RESET + "\n")
        sys.stdout.flush()

    def elapsed(self) -> str:
        secs = int(time.time() - self._start)
        h, rem = divmod(secs, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h}h{m:02d}m{s:02d}s"
        if m:
            return f"{m}m{s:02d}s"
        return f"{s}s"
