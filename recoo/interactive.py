"""Interactive tool checklist.

When the user runs with `-i/--interactive`, recoo shows the currently
selected tools (after any profile/flag filtering) as a multi-column
checklist and lets them toggle tools off (or on) before the run starts.

Two front-ends:
* a TTY arrow-key UI (←/→/↑/↓ move, SPACE toggle, ENTER run) when stdin
  is a real terminal and termios is available;
* a numbered fallback (type numbers to toggle, ENTER to run) otherwise.

Both lay tools out in a grid sized to the terminal width, so long tool
lists stay on screen instead of scrolling off the bottom.
"""
from __future__ import annotations

import shutil
import sys

from .config import Config
from .ui import C

NAME_W = 18                 # visible width reserved for a tool name
COL_GAP = 2                 # spaces between columns


def _is_tty() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        return False


def _term_cols() -> int:
    return shutil.get_terminal_size((100, 40)).columns


def _ncols(cell_w: int) -> int:
    """How many columns fit in the current terminal."""
    return max(1, (_term_cols() - 2) // (cell_w + COL_GAP))


def select(cfg: Config) -> bool:
    """Let the user refine the enabled set. Returns False if they abort."""
    tools = cfg.tools
    if _is_tty():
        try:
            return _arrow_ui(tools)
        except Exception:
            pass  # fall back to numbered mode on any terminal issue
    return _numbered_ui(tools)


def _cell(label_num, t, cursor: str = "", focus: bool = False) -> str:
    """Render one fixed-visible-width grid cell (colours don't count)."""
    box_plain = "[x]" if t.enabled else "[ ]"
    box = ((C.GREEN if t.enabled else C.GREY) + box_plain + C.RESET)
    num = ((C.CYAN if t.enabled else C.GREY) + f"{label_num:>2}" + C.RESET)
    name_plain = t.name[:NAME_W].ljust(NAME_W)
    if focus:
        name = C.BOLD + name_plain + C.RESET
    elif t.enabled:
        name = name_plain
    else:
        name = C.GREY + name_plain + C.RESET
    cur = (C.CYAN + cursor + C.RESET) if cursor.strip() else " "
    return f"{cur}{num} {box} {name}"


# --------------------------------------------------------------------- #
# Arrow-key UI (termios / raw mode) — grid navigation
# --------------------------------------------------------------------- #

def _arrow_ui(tools) -> bool:
    import termios
    import tty

    cell_w = 1 + 2 + 1 + 3 + 1 + NAME_W      # cursor+num+sp+box+sp+name
    ncols = _ncols(cell_w)
    idx = 0
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    prev_lines = [0]

    def build():
        on = sum(t.enabled for t in tools)
        lines = [
            f"{C.BOLD}recoo · select tools{C.RESET}  "
            f"{C.GREEN}{on}{C.RESET}/{len(tools)} will run    "
            f"{C.DIM}— made by cataract{C.RESET}",
            f"{C.DIM}←/→/↑/↓ move · SPACE toggle · a all · n none · "
            f"ENTER run · q quit{C.RESET}",
            "",
        ]
        row = []
        for i, t in enumerate(tools):
            cur = "❯" if i == idx else " "
            row.append(_cell(i + 1, t, cursor=cur, focus=(i == idx)))
            if len(row) == ncols:
                lines.append("  " + (" " * COL_GAP).join(row))
                row = []
        if row:
            lines.append("  " + (" " * COL_GAP).join(row))
        lines.append("")
        return lines

    def draw(first: bool):
        if not first:
            sys.stdout.write(f"\033[{prev_lines[0]}A")
        sys.stdout.write("\033[J")
        lines = build()
        sys.stdout.write("\n".join(lines) + "\n")
        sys.stdout.flush()
        prev_lines[0] = len(lines)

    n = len(tools)
    print()
    draw(first=True)
    try:
        tty.setraw(fd)
        while True:
            ch = sys.stdin.read(1)
            if ch == "\x1b":
                seq = sys.stdin.read(2)
                if seq == "[A":      idx = (idx - ncols) % n   # up
                elif seq == "[B":    idx = (idx + ncols) % n   # down
                elif seq == "[C":    idx = (idx + 1) % n       # right
                elif seq == "[D":    idx = (idx - 1) % n       # left
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
            elif ch in ("q", "Q", "\x03"):
                return False
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
            draw(first=False)
            tty.setraw(fd)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write("\n")


# --------------------------------------------------------------------- #
# Numbered fallback UI — grid layout
# --------------------------------------------------------------------- #

def _grid(cells) -> None:
    cell_w = 2 + 1 + 3 + 1 + NAME_W          # num+sp+box+sp+name
    ncols = _ncols(cell_w)
    for start in range(0, len(cells), ncols):
        print("  " + (" " * COL_GAP).join(cells[start:start + ncols]))


def _numbered_ui(tools) -> bool:
    """Fallback checklist: enabled tools first, numbered, laid out in a
    grid so the list fits on screen even with many tools.
    """
    while True:
        ordered = ([t for t in tools if t.enabled] +
                   [t for t in tools if not t.enabled])
        n_on = sum(t.enabled for t in tools)

        print(f"\n{C.BOLD}recoo · select tools{C.RESET}  "
              f"{C.GREEN}{n_on}{C.RESET}/{len(tools)} will run    "
              f"{C.DIM}— made by cataract{C.RESET}\n")

        print(f"{C.GREEN}── will run (type a number to REMOVE) "
              f"──────────────{C.RESET}")
        run_cells = [_cell(i + 1, t) for i, t in enumerate(ordered[:n_on])]
        _grid(run_cells)

        if n_on < len(ordered):
            print(f"\n{C.GREY}── available (type a number to ADD) "
                  f"────────────────{C.RESET}")
            add_cells = [_cell(n_on + j + 1, t)
                         for j, t in enumerate(ordered[n_on:])]
            _grid(add_cells)

        print(f"\n{C.DIM}numbers toggle (e.g. 3,5) · 'a' all · 'n' none · "
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
            if part.isdigit() and 1 <= int(part) <= len(ordered):
                ordered[int(part) - 1].enabled ^= True
