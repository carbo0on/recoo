"""Interactive tool checklist.

When the user runs with `-i/--interactive`, recoo shows the currently
selected tools (after any profile/flag filtering) as a checklist and lets
them toggle individual tools off (or on) before the run starts.

Two front-ends:
* a TTY arrow-key UI (↑/↓ move, SPACE toggle, ENTER run) when stdin is a
  real terminal and termios is available;
* a numbered fallback (type numbers to toggle, ENTER to run) otherwise.
"""
from __future__ import annotations

import sys
from typing import List

from .config import Config
from .engine import STAGE_DESC
from .ui import C


def _is_tty() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        return False


def select(cfg: Config) -> bool:
    """Let the user refine the enabled set. Returns False if they abort."""
    tools = cfg.tools
    if _is_tty():
        try:
            return _arrow_ui(tools)
        except Exception:
            pass  # fall back to numbered mode on any terminal issue
    return _numbered_ui(tools)


# --------------------------------------------------------------------- #
# Arrow-key UI (termios / raw mode)
# --------------------------------------------------------------------- #

def _arrow_ui(tools) -> bool:
    import termios
    import tty

    idx = 0
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)

    def draw(first: bool):
        lines = 2 + len(tools) + 4  # header + footer
        if not first:
            sys.stdout.write(f"\033[{lines}A")  # move cursor up to redraw
        sys.stdout.write("\033[J")  # clear below
        on = sum(t.enabled for t in tools)
        print(f"{C.BOLD}recoo · select tools{C.RESET}  "
              f"{C.GREEN}{on}{C.RESET}/{len(tools)} enabled")
        print(f"{C.DIM}↑/↓ move · SPACE toggle · a all · n none · "
              f"ENTER run · q quit{C.RESET}\n")
        last_stage = None
        for i, t in enumerate(tools):
            cursor = f"{C.CYAN}❯{C.RESET}" if i == idx else " "
            box = (f"{C.GREEN}[x]{C.RESET}" if t.enabled
                   else f"{C.GREY}[ ]{C.RESET}")
            stag = "" if t.stage == last_stage else f" {C.DIM}({t.stage}){C.RESET}"
            last_stage = t.stage
            name = (f"{C.BOLD}{t.name}{C.RESET}" if i == idx else t.name)
            print(f" {cursor} {box} {name:<22}{stag}")
        print()

    print()
    draw(first=True)
    try:
        tty.setraw(fd)
        while True:
            ch = sys.stdin.read(1)
            if ch == "\x1b":               # escape sequence (arrow keys)
                seq = sys.stdin.read(2)
                if seq == "[A":
                    idx = (idx - 1) % len(tools)
                elif seq == "[B":
                    idx = (idx + 1) % len(tools)
            elif ch == " ":
                tools[idx].enabled = not tools[idx].enabled
            elif ch in ("a", "A"):
                for t in tools:
                    t.enabled = True
            elif ch in ("n", "N"):
                for t in tools:
                    t.enabled = False
            elif ch in ("\r", "\n"):
                return True
            elif ch in ("q", "Q", "\x03"):  # q or Ctrl-C
                return False
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
            draw(first=False)
            tty.setraw(fd)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write("\n")


# --------------------------------------------------------------------- #
# Numbered fallback UI
# --------------------------------------------------------------------- #

def _numbered_ui(tools) -> bool:
    while True:
        print(f"\n{C.BOLD}recoo · select tools{C.RESET}  "
              f"({sum(t.enabled for t in tools)}/{len(tools)} enabled)\n")
        last_stage = None
        for i, t in enumerate(tools, 1):
            if t.stage != last_stage:
                print(f"{C.BLUE}▌ {t.stage}{C.RESET}")
                last_stage = t.stage
            box = (f"{C.GREEN}[x]{C.RESET}" if t.enabled
                   else f"{C.GREY}[ ]{C.RESET}")
            print(f"  {C.DIM}{i:>2}{C.RESET} {box} {t.name:<22} "
                  f"{C.DIM}{t.desc}{C.RESET}")
        print(f"\n{C.DIM}toggle: numbers (e.g. 3,5,7) · 'a' all · 'n' none · "
              f"ENTER run · 'q' quit{C.RESET}")
        try:
            choice = input("recoo> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        if choice == "":
            return True
        if choice in ("q", "quit"):
            return False
        if choice == "a":
            for t in tools:
                t.enabled = True
            continue
        if choice == "n":
            for t in tools:
                t.enabled = False
            continue
        for part in choice.replace(" ", ",").split(","):
            if part.isdigit() and 1 <= int(part) <= len(tools):
                tools[int(part) - 1].enabled ^= True
