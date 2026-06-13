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

def _append_new(path, candidates) -> int:
    """Append only lines not already in ``path``. Returns count added."""
    existing = set(read_lines(path))
    new = [c for c in dedup(candidates) if c not in existing]
    if new:
        with path.open("a") as fh:
            fh.write("\n".join(new) + "\n")
    return len(new)


def derive_live(engine: "Engine") -> None:
    """Build hosts/live.txt from httpx JSON output (one URL per line).

    If httpx produced nothing usable (it errored, was the wrong binary, or
    the target throttled it), fall back to seeding live hosts from the
    resolved subdomains (https://) so the crawl/screenshot/JS stages still
    have input instead of the whole downstream pipeline being skipped.
    """
    live = engine.ws.artifact("live")
    anew(live, [])  # ensure the file exists

    httpx_json = engine.ws.path("hosts/httpx.json")
    urls = []
    for line in read_lines(httpx_json):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        url = obj.get("url") or obj.get("input")
        if url:
            urls.append(url)

    if urls:
        _append_new(live, urls)
        engine.log.ok(f"derived {count_lines(live)} live hosts -> hosts/live.txt")
        return

    # Fallback: no usable httpx output. Seed from resolved hosts so the rest
    # of the pipeline can run (better a few dead hosts than skipping it all).
    if count_lines(live) > 0:
        return
    resolved = engine.ws.artifact("resolved")
    hosts = read_lines(resolved)
    if not hosts:
        return
    seeded = [h if h.startswith(("http://", "https://")) else f"https://{h}"
              for h in hosts]
    n = _append_new(live, seeded)
    engine.log.warn(
        f"httpx produced no live hosts — falling back to {n} resolved "
        f"host(s) as https:// so crawl/screenshots/JS can still run "
        f"(check that the real 'httpx' is on PATH, not the python one)")


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

# Parameter-name heuristics: which query params commonly expose which bug
# class. Used to triage URLs into injection candidates with no external deps.
INJECTION_KEYWORDS = {
    "sqli":     ["id", "uid", "pid", "user", "userid", "item", "itemid", "cat",
                 "category", "product", "order", "sort", "select", "where",
                 "query", "search", "num", "no", "key", "name", "page", "col"],
    "xss":      ["q", "s", "search", "query", "keyword", "kw", "term", "name",
                 "message", "msg", "comment", "content", "title", "redirect",
                 "return", "url", "callback", "jsonp", "lang", "ref"],
    "ssrf":     ["url", "uri", "link", "src", "source", "dest", "destination",
                 "redirect", "return", "next", "target", "host", "domain",
                 "callback", "fetch", "file", "path", "proxy", "site", "html",
                 "page", "feed", "to", "out", "image", "img", "load", "open"],
    "lfi":      ["file", "path", "page", "doc", "document", "folder", "dir",
                 "download", "read", "include", "inc", "template", "tpl",
                 "view", "content", "name", "lang", "locale", "pg", "style"],
    "rce":      ["cmd", "exec", "command", "run", "ping", "code", "do", "func",
                 "function", "system", "eval", "query", "jump", "process"],
    "ssti":     ["name", "template", "tpl", "view", "lang", "locale", "preview",
                 "id", "page", "content", "message", "title"],
    "redirect": ["redirect", "redir", "url", "return", "returnurl", "return_url",
                 "next", "goto", "dest", "destination", "continue", "r", "u",
                 "to", "out", "target", "rurl", "link", "checkout_url",
                 "redirect_uri", "redirect_url", "callback", "forward"],
    "idor":     ["id", "uid", "user", "userid", "account", "acct", "number",
                 "no", "order", "orderid", "doc", "file", "fileid", "key",
                 "profile", "group", "invoice", "ticket", "record", "ref"],
}


def injection_classify(engine: "Engine", tool: "Tool" = None) -> None:
    """Extract URLs likely vulnerable to injection and classify by bug class.

    Reads urls/with_params.txt (falling back to urls/clean.txt), inspects
    each query parameter name, and writes:
        findings/injection/<class>.txt          one file per bug class
        findings/injection_candidates.txt        combined, tagged report
    Pure-python — no external tool required, so it always runs.
    """
    from urllib.parse import urlparse, parse_qs

    src = engine.ws.artifact("with_params")
    if count_lines(src) == 0:
        src = engine.ws.artifact("urls_clean")
    if count_lines(src) == 0:
        engine.log.warn("injection_classify: no URLs with parameters yet")
        return

    buckets = {cls: [] for cls in INJECTION_KEYWORDS}
    combined = []
    seen_combined = set()

    for url in read_lines(src):
        try:
            q = urlparse(url).query
        except ValueError:
            continue
        if not q:
            continue
        params = list(parse_qs(q).keys())
        if not params:
            continue
        matched = set()
        for pname in params:
            low = pname.lower()
            for cls, keys in INJECTION_KEYWORDS.items():
                if low in keys:
                    matched.add(cls)
        if not matched:
            continue
        for cls in matched:
            buckets[cls].append(url)
        tag = ",".join(sorted(matched))
        line = f"[{tag}] {url}"
        if line not in seen_combined:
            seen_combined.add(line)
            combined.append(line)

    inj_dir = engine.ws.root / "findings" / "injection"
    inj_dir.mkdir(parents=True, exist_ok=True)
    total_by_class = {}
    for cls, urls in buckets.items():
        urls = dedup(urls)
        total_by_class[cls] = len(urls)
        if urls:
            (inj_dir / f"{cls}.txt").write_text("\n".join(urls) + "\n")

    out = engine.ws.path("findings/injection_candidates.txt")
    out.write_text("\n".join(combined) + ("\n" if combined else ""))

    summary = " · ".join(f"{c}:{n}" for c, n in total_by_class.items() if n)
    engine.log.ok(f"injection candidates: {len(combined)} URLs "
                  f"[{summary or 'none'}] -> findings/injection_candidates.txt")


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
    "injection_classify": injection_classify,
    "gf_patterns": gf_patterns,
    "api_docs": api_docs,
    "secrets_grep": secrets_grep,
    "source_maps": source_maps,
    "monitor_diff": monitor_diff,
}


def _q(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"
