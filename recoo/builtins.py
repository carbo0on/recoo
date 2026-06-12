"""Built-in pipeline steps implemented in Python.

These cover the "glue" that does not map cleanly to a single external
binary: deriving the live-host list from httpx JSON, classifying URLs,
running gf pattern triage, probing common API/secret paths, and writing
the monitoring diff. Each is still represented as a toggleable Tool in
the registry, so users can switch them on/off like any other tool.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from .ui import C
from .workspace import anew, count_lines, dedup, read_lines

if TYPE_CHECKING:  # avoid circular import at runtime
    from .engine import Engine
    from .tool import Tool


def dispatch(name: str, engine: "Engine", tool: "Tool") -> None:
    handler = _HANDLERS.get(name)
    if not handler:
        engine.log.warn(f"unknown builtin '{name}'")
        return
    handler(engine, tool)


# --- post-stage plumbing (called unconditionally by the engine) ----------

def derive_live(engine: "Engine") -> None:
    """Build hosts/live.txt from httpx JSON output (one URL per line)."""
    httpx_json = engine.ws.path("hosts/httpx.json")
    if count_lines(httpx_json) == 0:
        return
    live = engine.ws.artifact("live")
    urls = []
    for line in read_lines(httpx_json):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        url = obj.get("url") or obj.get("input")
        if url:
            urls.append(url)
    added = anew(live, [])  # ensure file exists
    if urls:
        existing = set(read_lines(live))
        new = [u for u in dedup(urls) if u not in existing]
        if new:
            with live.open("a") as fh:
                fh.write("\n".join(new) + "\n")
        engine.log.ok(f"derived {count_lines(live)} live hosts -> hosts/live.txt")


def process_urls(engine: "Engine", tool: "Tool" = None) -> None:
    """Classify urls/all.txt into clean / with_params / js_urls."""
    all_urls = engine.ws.artifact("urls_all")
    if count_lines(all_urls) == 0:
        return
    urls = read_lines(all_urls)

    clean_path = engine.ws.artifact("urls_clean")
    if engine._have("uro"):
        # uro removes semantic duplicates; pipe through it.
        try:
            proc = subprocess.run(["uro"], input="\n".join(urls),
                                  text=True, capture_output=True, timeout=600)
            clean = dedup(proc.stdout.splitlines())
        except Exception:
            clean = dedup(urls)
    else:
        clean = dedup(urls)
    clean_path.write_text("\n".join(clean) + ("\n" if clean else ""))

    params = [u for u in clean if "?" in u and "=" in u]
    engine.ws.artifact("with_params").write_text(
        "\n".join(params) + ("\n" if params else ""))

    js = [u for u in clean if re.search(r"\.js(\?|$)", u, re.I)]
    engine.ws.artifact("js_urls").write_text(
        "\n".join(js) + ("\n" if js else ""))

    engine.log.ok(f"urls: {len(clean)} clean · {len(params)} with params · "
                  f"{len(js)} js")


# --- toggleable builtin tools --------------------------------------------

def gf_patterns(engine: "Engine", tool: "Tool") -> None:
    """Run gf patterns over urls/clean.txt into findings/gf_<pattern>.txt."""
    if not engine._have("gf"):
        engine.log.warn("gf not installed; skipping pattern triage")
        return
    src = engine.ws.artifact("urls_clean")
    if count_lines(src) == 0:
        return
    data = src.read_text(errors="ignore")
    patterns = engine.cfg.settings.get("gf_patterns", [])
    total = 0
    for pat in patterns:
        try:
            proc = subprocess.run(["gf", pat], input=data, text=True,
                                  capture_output=True, timeout=300)
        except Exception:
            continue
        hits = dedup(proc.stdout.splitlines())
        if hits:
            out = engine.ws.path(f"findings/gf_{pat}.txt")
            out.write_text("\n".join(hits) + "\n")
            total += len(hits)
            engine.log.debug(f"  gf {pat}: {len(hits)} hits")
    engine.log.ok(f"gf triage: {total} candidate URLs across "
                  f"{len(patterns)} patterns")


def api_docs(engine: "Engine", tool: "Tool") -> None:
    """Probe each live host for common API/doc/secret paths via httpx."""
    if not engine._have("httpx"):
        engine.log.warn("httpx not installed; skipping API path probe")
        return
    live = engine.ws.artifact("live")
    if count_lines(live) == 0:
        return
    paths = engine.cfg.settings.get("api_paths", [])
    out = engine.ws.path("findings/api_surface.txt")
    cmd = (f"httpx -silent -mc 200,401,403 -sc -title "
           f"-l {_q(str(live))} -path {_q(','.join(paths))} "
           f">> {_q(str(out))} 2>/dev/null")
    engine._run_cmd(cmd, tool.timeout or int(engine.cfg.settings.get("timeout", 1800)))
    engine.log.ok(f"api surface probe -> findings/api_surface.txt "
                  f"({count_lines(out)} hits)")


def secrets_grep(engine: "Engine", tool: "Tool") -> None:
    """Lightweight regex secret scan across live JS files."""
    js = engine.ws.artifact("js_urls")
    if count_lines(js) == 0:
        return
    pattern = (r"(api[_-]?key|secret|token|passwd|password|aws_|firebase|"
               r"bearer|authorization)[\"' :=]+[A-Za-z0-9_\-]{12,}")
    out = engine.ws.path("findings/js_secrets.txt")
    urls = read_lines(js)
    found = 0
    with out.open("w") as fh:
        for url in urls:
            try:
                proc = subprocess.run(
                    ["bash", "-c",
                     f"curl -s -m 20 {_q(url)} | grep -aoiE {_q(pattern)}"],
                    capture_output=True, text=True, timeout=40)
            except Exception:
                continue
            for line in dedup(proc.stdout.splitlines()):
                fh.write(f"{url}\t{line}\n")
                found += 1
    engine.log.ok(f"secrets scan: {found} candidate strings -> "
                  f"findings/js_secrets.txt")


def source_maps(engine: "Engine", tool: "Tool") -> None:
    """Detect exposed .map files for live JS (high-value source recovery)."""
    if not engine._have("httpx"):
        engine.log.warn("httpx not installed; skipping source-map check")
        return
    js = engine.ws.artifact("js_urls")
    if count_lines(js) == 0:
        return
    maps = [u + ".map" for u in read_lines(js)]
    tmp = engine.ws.path("js/.map_candidates.txt")
    tmp.write_text("\n".join(maps) + "\n")
    out = engine.ws.path("findings/source_maps.txt")
    cmd = (f"httpx -silent -mc 200 -l {_q(str(tmp))} "
           f">> {_q(str(out))} 2>/dev/null")
    engine._run_cmd(cmd, tool.timeout or 900)
    engine.log.ok(f"source maps: {count_lines(out)} exposed -> "
                  f"findings/source_maps.txt")


def monitor_diff(engine: "Engine", tool: "Tool") -> None:
    """Snapshot key artifacts and report what is new since last run."""
    import time
    stamp = time.strftime("%Y%m%d-%H%M%S")
    snap = engine.ws.root / "monitoring" / stamp
    snap.mkdir(parents=True, exist_ok=True)
    tracked = {
        "subs_all": "subs/all.txt",
        "resolved": "subs/resolved.txt",
        "live": "hosts/live.txt",
        "urls_clean": "urls/clean.txt",
    }
    summary = []
    for name, rel in tracked.items():
        cur = engine.ws.path(rel)
        if count_lines(cur) == 0:
            continue
        baseline = engine.ws.root / "monitoring" / f"baseline_{name}.txt"
        added = anew(baseline, [cur])
        shutil.copy(cur, snap / Path(rel).name)
        summary.append(f"{name}: +{added} new")
    report = engine.ws.path("monitoring/last_diff.txt")
    report.write_text("\n".join(summary) + "\n")
    engine.log.ok("monitoring diff: " + (", ".join(summary) or "no changes"))
    if engine.cfg.settings.get("notify") and engine._have("notify") and summary:
        subprocess.run(["bash", "-c",
                        f"cat {_q(str(report))} | notify -silent"])


_HANDLERS = {
    "process_urls": process_urls,
    "gf_patterns": gf_patterns,
    "api_docs": api_docs,
    "secrets_grep": secrets_grep,
    "source_maps": source_maps,
    "monitor_diff": monitor_diff,
}


def _q(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"
