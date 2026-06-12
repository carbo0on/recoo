"""Workspace: the on-disk output tree, and merge/dedup helpers.

Layout mirrors the methodology so results are easy to navigate:

    <output>/
    ├── seeds/        apex domains, ASNs, CIDRs
    ├── subs/         raw + resolved subdomains
    ├── hosts/        httpx output, live hosts, ports
    ├── urls/         crawled + historical URLs
    ├── js/           JS files + extracted endpoints/secrets
    ├── params/       discovered parameters
    ├── findings/     takeovers, gf hits, nuclei, secrets
    ├── monitoring/   diffs over time
    └── .recoo/       run metadata + logs
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable, List

from .tool import ARTIFACTS

SUBDIRS = ["seeds", "subs", "hosts", "urls", "js", "params",
           "findings", "monitoring", ".recoo"]


class Workspace:
    def __init__(self, root: str):
        self.root = Path(root).resolve()

    def init(self) -> None:
        for d in SUBDIRS:
            (self.root / d).mkdir(parents=True, exist_ok=True)

    def path(self, rel: str) -> Path:
        p = (self.root / rel).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def artifact(self, name: str) -> Path:
        """Resolve a canonical artifact name to its file path."""
        return self.path(ARTIFACTS[name])

    def seed_from(self, domains: Iterable[str]) -> Path:
        """Write the deduplicated seed domain list and return its path."""
        seeds = self.artifact("seeds")
        existing = read_lines(seeds)
        merged = dedup(list(existing) + [d.strip() for d in domains if d.strip()])
        seeds.write_text("\n".join(merged) + ("\n" if merged else ""))
        return seeds

    def save_meta(self, meta: dict) -> None:
        (self.root / ".recoo" / "run.json").write_text(
            json.dumps(meta, indent=2, default=str))


def read_lines(path: Path) -> List[str]:
    if not path.exists():
        return []
    return [ln.rstrip("\n") for ln in path.read_text(errors="ignore").splitlines()
            if ln.strip()]


def dedup(items: Iterable[str]) -> List[str]:
    """Order-preserving unique (mimics `sort -u` but keeps first-seen order)."""
    seen: set = set()
    out: List[str] = []
    for it in items:
        it = it.strip()
        if it and it not in seen:
            seen.add(it)
            out.append(it)
    return out


def count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for ln in path.read_text(errors="ignore").splitlines() if ln.strip())


def anew(target: Path, sources: Iterable[Path]) -> int:
    """Merge ``sources`` into ``target`` keeping only unique lines.

    Returns the number of *new* lines added (handy for diffing/monitoring).
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = set(read_lines(target))
    added: List[str] = []
    for src in sources:
        for line in read_lines(Path(src)):
            if line not in existing:
                existing.add(line)
                added.append(line)
    if added:
        with target.open("a") as fh:
            for line in added:
                fh.write(line + "\n")
    return len(added)
