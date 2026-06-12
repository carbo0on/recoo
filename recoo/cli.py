"""Command-line interface for recoo."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__
from .config import Config, apply_cli_filters, load
from .engine import STAGE_DESC, Engine
from .ui import C, Logger
from .workspace import Workspace, count_lines


def _split(value: Optional[str]) -> Optional[List[str]]:
    if not value:
        return None
    return [v.strip() for v in value.split(",") if v.strip()]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="recoo",
        description="Modular, controllable reconnaissance automation. "
                    "Feed it a domain (or a file of domains) and pick "
                    "exactly which tools run.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_EPILOG)

    src = p.add_argument_group("target")
    src.add_argument("input", nargs="?",
                     help="file containing one domain per line")
    src.add_argument("-d", "--domain", help="single target domain")

    out = p.add_argument_group("workspace")
    out.add_argument("-o", "--output", default="recoo-output",
                     help="output directory (default: recoo-output)")
    out.add_argument("-c", "--config", help="path to a config.yaml override")

    sel = p.add_argument_group("tool selection (the control layer)")
    sel.add_argument("--only", help="run ONLY these tools (comma-separated)")
    sel.add_argument("--enable", help="force-enable these tools")
    sel.add_argument("--disable", help="force-disable these tools")
    sel.add_argument("--stages", help="run only these stages")
    sel.add_argument("--skip-stages", help="skip these stages")
    sel.add_argument("--exclude-tags",
                     help="disable tools carrying any of these tags "
                          "(e.g. slow,noisy,needs-key)")

    run = p.add_argument_group("run control")
    run.add_argument("--threads", type=int, help="override global concurrency")
    run.add_argument("--timeout", type=int,
                     help="override per-tool timeout (seconds)")
    run.add_argument("--dry-run", action="store_true",
                     help="print what would run, execute nothing")

    info = p.add_argument_group("inspection")
    info.add_argument("--list-tools", action="store_true",
                      help="show every tool with its state and exit")
    info.add_argument("--list-stages", action="store_true",
                      help="show the pipeline stages and exit")

    log = p.add_argument_group("logging")
    log.add_argument("-v", "--verbose", action="store_true",
                     help="verbose (debug) output")
    log.add_argument("-q", "--quiet", action="store_true",
                     help="errors/warnings only")
    log.add_argument("--no-color", action="store_true")

    p.add_argument("--version", action="version",
                   version=f"recoo {__version__}")
    return p


def _resolve_domains(args, log: Logger) -> List[str]:
    if args.domain:
        return [args.domain.strip()]
    if args.input:
        path = Path(args.input)
        if path.exists() and path.is_file():
            return [ln.strip() for ln in path.read_text().splitlines()
                    if ln.strip()]
        # Not a file: treat the argument as a bare domain.
        if "/" in args.input or args.input.endswith((".txt", ".lst")):
            log.error(f"input file not found: {path}")
            sys.exit(2)
        return [args.input.strip()]
    return []


def _print_tools(cfg: Config) -> None:
    print(f"\n{C.BOLD}recoo tools{C.RESET}  "
          f"({sum(t.enabled for t in cfg.tools)}/{len(cfg.tools)} enabled)\n")
    last_stage = None
    for t in cfg.tools:
        if t.stage != last_stage:
            print(f"{C.BLUE}▌ {t.stage}{C.RESET} "
                  f"{C.DIM}{STAGE_DESC.get(t.stage, '')}{C.RESET}")
            last_stage = t.stage
        dot = f"{C.GREEN}●{C.RESET}" if t.enabled else f"{C.GREY}○{C.RESET}"
        tags = f" {C.DIM}[{','.join(t.tags)}]{C.RESET}" if t.tags else ""
        print(f"  {dot} {C.BOLD}{t.name:<20}{C.RESET} "
              f"{C.DIM}{t.desc}{C.RESET}{tags}")
    print(f"\n{C.DIM}● enabled   ○ disabled · "
          f"toggle with --enable/--disable/--only or in config.yaml{C.RESET}\n")


def _print_stages(cfg: Config) -> None:
    print(f"\n{C.BOLD}recoo pipeline stages{C.RESET}\n")
    for i, stage in enumerate(cfg.stages_in_order(), 1):
        n = sum(t.enabled for t in cfg.tools if t.stage == stage)
        total = sum(1 for t in cfg.tools if t.stage == stage)
        print(f"  {C.CYAN}{i:>2}.{C.RESET} {C.BOLD}{stage:<14}{C.RESET} "
              f"{C.DIM}{STAGE_DESC.get(stage, '')}{C.RESET} "
              f"{C.GREEN}({n}/{total}){C.RESET}")
    print()


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    level = "debug" if args.verbose else "quiet" if args.quiet else "info"
    log = Logger(level=level, use_color=not args.no_color)

    cfg = load(user_config=args.config)
    if args.threads:
        cfg.settings["threads"] = args.threads
    if args.timeout:
        cfg.settings["timeout"] = args.timeout

    apply_cli_filters(
        cfg,
        only=_split(args.only),
        enable=_split(args.enable),
        disable=_split(args.disable),
        stages=_split(args.stages),
        skip_stages=_split(args.skip_stages),
        tags_exclude=_split(args.exclude_tags),
    )

    if args.list_tools:
        _print_tools(cfg)
        return 0
    if args.list_stages:
        _print_stages(cfg)
        return 0

    domains = _resolve_domains(args, log)
    if not domains:
        log.error("no target. Pass a domains file or use -d example.com "
                  "(or --list-tools to inspect).")
        return 2

    log.banner(__version__)
    ws = Workspace(args.output)
    ws.init()
    ws.seed_from(domains)

    enabled = [t.name for t in cfg.tools if t.enabled]
    log.info(f"targets   : {len(domains)} "
             f"({', '.join(domains[:3])}{'…' if len(domains) > 3 else ''})")
    log.info(f"workspace : {ws.root}")
    log.info(f"tools on  : {len(enabled)}/{len(cfg.tools)}  "
             f"[{', '.join(enabled)}]")
    if args.dry_run:
        log.warn("dry-run: no commands will be executed")

    single = domains[0] if len(domains) == 1 else None
    engine = Engine(cfg, ws, log, single_domain=single, dry_run=args.dry_run)
    result = engine.run()

    _summary(log, ws, result)
    ws.save_meta({
        "version": __version__,
        "targets": domains,
        "ran": result.ran,
        "skipped": result.skipped,
        "failed": result.failed,
        "new_lines": result.new_lines,
        "elapsed": log.elapsed(),
    })
    return 0


def _summary(log: Logger, ws: Workspace, result) -> None:
    artifacts = [
        ("subdomains", "subs/all.txt"),
        ("resolved", "subs/resolved.txt"),
        ("live hosts", "hosts/live.txt"),
        ("urls (clean)", "urls/clean.txt"),
        ("js files", "js/js_urls.txt"),
        ("params", "params/all.txt"),
    ]
    print(f"\n{C.BLUE}{'─' * 58}{C.RESET}")
    print(f"{C.BOLD}{C.GREEN}▌ recon complete{C.RESET}  "
          f"{C.DIM}in {log.elapsed()}{C.RESET}")
    print(f"{C.BLUE}{'─' * 58}{C.RESET}")
    for label, rel in artifacts:
        n = count_lines(ws.path(rel))
        if n:
            print(f"  {C.CYAN}{n:>7}{C.RESET}  {label:<14} {C.DIM}{rel}{C.RESET}")
    print(f"\n  ran {C.GREEN}{len(result.ran)}{C.RESET} · "
          f"skipped {C.YELLOW}{len(result.skipped)}{C.RESET} · "
          f"failed {C.RED}{len(result.failed)}{C.RESET}")
    if result.failed:
        print(f"  {C.RED}failed:{C.RESET} {', '.join(result.failed)}")
    findings = ws.root / "findings"
    hits = [f.name for f in sorted(findings.glob('*')) if count_lines(f) > 0]
    if hits:
        print(f"  {C.MAGENTA}findings/:{C.RESET} {', '.join(hits)}")
    print(f"\n  results in {C.BOLD}{ws.root}{C.RESET}\n")


_EPILOG = """
examples:
  recoo example.com                       run defaults on one domain
  recoo domains.txt -o acme               run on a list, custom output dir
  recoo -d example.com --only subfinder,httpx,katana
  recoo -d example.com --disable nuclei,amass_passive
  recoo -d example.com --stages subdomains,resolve,probe
  recoo -d example.com --exclude-tags slow,noisy,needs-key
  recoo --list-tools                      inspect/verify your selection
  recoo -d example.com --dry-run -v       preview the exact commands
"""


if __name__ == "__main__":
    sys.exit(main())
