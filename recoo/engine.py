"""The execution engine: runs the pipeline stage by stage.

Responsibilities
----------------
* resolve command templates against the workspace + settings
* skip tools that are disabled, filtered out, missing binaries, or have
  no input to work on
* run shell commands (and built-in handlers) with timeouts
* merge each tool's output into the canonical artifact that feeds the
  next stage
* run the small "plumbing" post-steps (derive live hosts, classify URLs)
"""
from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional

from . import builtins as bi
from .config import Config
from .tool import ARTIFACTS, Tool
from .ui import C, Logger
from .workspace import Workspace, anew, count_lines, dedup, read_lines


class RunResult:
    def __init__(self) -> None:
        self.ran: List[str] = []
        self.skipped: Dict[str, str] = {}   # name -> reason
        self.failed: Dict[str, str] = {}
        self.new_lines: Dict[str, int] = {}  # artifact -> added


class Engine:
    def __init__(self, cfg: Config, ws: Workspace, log: Logger,
                 single_domain: Optional[str] = None, dry_run: bool = False):
        self.cfg = cfg
        self.ws = ws
        self.log = log
        self.single_domain = single_domain or ""
        self.dry_run = dry_run
        self.result = RunResult()
        self._which_cache: Dict[str, bool] = {}

    # ---- helpers ---------------------------------------------------------

    def _have(self, binary: str) -> bool:
        if binary not in self._which_cache:
            self._which_cache[binary] = shutil.which(binary) is not None
        return self._which_cache[binary]

    def _vars(self) -> Dict[str, str]:
        s = self.cfg.settings
        v = {
            "workspace": str(self.ws.root),
            "domain": self.single_domain,
            "threads": str(s.get("threads", 40)),
            "resolvers": str(s.get("resolvers", "")),
            "wordlist_dns": str(s.get("wordlist_dns", "")),
            "wordlist_content": str(s.get("wordlist_content", "")),
            "wordlist_params": str(s.get("wordlist_params", "")),
            "wordlist_perms": str(s.get("wordlist_perms", "")),
            "github_token": str(s.get("github_token", "")),
        }
        v.update({k: str(val) for k, val in (s.get("vars") or {}).items()})
        return v

    def _render(self, template: str, item: Optional[str],
                output: Optional[Path], inp: Optional[Path]) -> str:
        mapping = self._vars()
        mapping["item"] = item or ""
        mapping["output"] = str(output) if output else ""
        mapping["input"] = str(inp) if inp else ""
        try:
            return template.format(**mapping)
        except KeyError as e:
            raise SystemExit(f"Unknown placeholder {e} in command: {template}")

    def _output_path(self, tool: Tool, item: Optional[str]) -> Optional[Path]:
        if not tool.output:
            return None
        rel = tool.output
        if item is not None:
            safe = "".join(c if c.isalnum() or c in ".-_" else "_" for c in item)
            rel = rel.replace("{item}", safe)
        return self.ws.path(rel)

    # ---- skip checks -----------------------------------------------------

    def _skip_reason(self, tool: Tool) -> Optional[str]:
        if not tool.enabled:
            return "disabled"
        missing = [b for b in tool.bins if not self._have(b)]
        if missing:
            return f"missing binary: {', '.join(missing)}"
        if tool.input:
            src = self.ws.artifact(tool.input)
            if count_lines(src) == 0:
                return f"no input ({ARTIFACTS[tool.input]} empty)"
        return None

    # ---- running a single tool ------------------------------------------

    def _run_cmd(self, cmd: str, timeout: int) -> int:
        if self.dry_run:
            self.log.info(f"{C.DIM}dry-run:{C.RESET} {cmd}")
            return 0
        try:
            proc = subprocess.run(["bash", "-c", cmd], timeout=timeout)
            return proc.returncode
        except subprocess.TimeoutExpired:
            self.log.warn(f"timed out after {timeout}s")
            return 124
        except Exception as e:  # pragma: no cover
            self.log.error(f"exec error: {e}")
            return 1

    def run_tool(self, tool: Tool) -> None:
        reason = self._skip_reason(tool)
        if reason:
            self.result.skipped[tool.name] = reason
            level = self.log.debug if reason == "disabled" else self.log.warn
            level(f"skip {C.BOLD}{tool.name}{C.RESET} ({reason})")
            return

        timeout = tool.timeout or int(self.cfg.settings.get("timeout", 1800))
        inp = self.ws.artifact(tool.input) if tool.input else None

        # Built-in handlers (pure-python plumbing/aggregation steps).
        if tool.builtin:
            self.log.info(f"run  {C.BOLD}{tool.name}{C.RESET} "
                          f"{C.DIM}(builtin: {tool.builtin}){C.RESET}")
            if not self.dry_run:
                bi.dispatch(tool.builtin, self, tool)
            self.result.ran.append(tool.name)
            self._merge(tool, self._collect_outputs(tool))
            return

        # Determine the items to iterate over.
        if tool.mode == "each":
            items = read_lines(inp) if inp else (
                [self.single_domain] if self.single_domain else [])
            if not items:
                self.result.skipped[tool.name] = "no items"
                self.log.warn(f"skip {tool.name} (no items)")
                return
        else:
            items = [None]

        self.log.info(f"run  {C.BOLD}{tool.name}{C.RESET}  "
                      f"{C.DIM}{tool.desc}{C.RESET}")
        produced: List[Path] = []
        start = time.time()
        rc_any_ok = False
        for idx, item in enumerate(items, 1):
            out = self._output_path(tool, item)
            cmd = self._render(tool.cmd, item, out, inp)
            if tool.stdout and out:
                cmd = f"{cmd} >> {_q(str(out))} 2>/dev/null"
            if tool.mode == "each" and len(items) > 1:
                self.log.debug(f"  [{idx}/{len(items)}] {item}")
            rc = self._run_cmd(cmd, timeout)
            rc_any_ok = rc_any_ok or rc == 0
            if out:
                produced.append(out)

        if not rc_any_ok and not self.dry_run:
            self.result.failed[tool.name] = "non-zero exit"
        self.result.ran.append(tool.name)
        self._merge(tool, produced)

        dur = int(time.time() - start)
        if tool.output and not self.dry_run:
            main_out = self._output_path(tool, None if tool.mode != "each" else "")
            n = sum(count_lines(p) for p in produced) if tool.mode == "each" else count_lines(main_out) if main_out else 0
            self.log.ok(f"done {tool.name} · {n} lines · {dur}s")

    def _collect_outputs(self, tool: Tool) -> List[Path]:
        if not tool.output:
            return []
        if "{item}" in tool.output:
            pattern = tool.output.replace("{item}", "*")
            return list(self.ws.root.glob(pattern))
        return [self.ws.path(tool.output)]

    def _merge(self, tool: Tool, produced: List[Path]) -> None:
        if not tool.produces or self.dry_run:
            return
        target = self.ws.artifact(tool.produces)
        added = anew(target, produced)
        self.result.new_lines[tool.produces] = (
            self.result.new_lines.get(tool.produces, 0) + added)
        if added:
            self.log.debug(f"  +{added} -> {ARTIFACTS[tool.produces]}")

    # ---- stage orchestration --------------------------------------------

    def run(self) -> RunResult:
        stages = self.cfg.stages_in_order()
        for stage in stages:
            tools = [t for t in self.cfg.tools if t.stage == stage]
            active = [t for t in tools if t.enabled]
            if not active:
                continue
            self.log.phase(stage, STAGE_DESC.get(stage, ""))
            for tool in tools:
                self.run_tool(tool)
            self._post_stage(stage)
        return self.result

    def _post_stage(self, stage: str) -> None:
        """Plumbing that always runs after a stage if its inputs exist."""
        if self.dry_run:
            return
        if stage == "probe":
            bi.derive_live(self)


STAGE_DESC = {
    "seeds":        "Root/seed asset discovery (ASN, CIDR, apex domains)",
    "subdomains":   "Subdomain enumeration (passive + active + brute)",
    "permutations": "Permutation / alteration discovery",
    "resolve":      "DNS resolution + takeover candidates",
    "probe":        "HTTP probing + tech fingerprint",
    "ports":        "Port scanning on direct assets",
    "screenshots":  "Visual recon: screenshot every live host/port",
    "crawl":        "Live crawling + historical URL mining",
    "urls":         "URL classification + filtering",
    "js":           "Deep JavaScript analysis (endpoints + secrets)",
    "params":       "Hidden parameter discovery",
    "apis":         "Modern API surface (REST/GraphQL/docs)",
    "cloud":        "Cloud / edge / storage discovery",
    "osint":        "OSINT, secrets, code leaks",
    "triage":       "Turn recon into attack vectors (gf + nuclei)",
    "monitoring":   "Continuous recon: diff + notify",
}


def _q(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"
