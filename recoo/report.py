"""Generate a self-contained HTML report from a recon workspace.

Produces <workspace>/report.html: a single dark-themed page with summary
cards, the live-host table (parsed from httpx JSON), injection candidates
grouped by bug class, a screenshot gallery, and key findings. Inline CSS,
no external assets, so it opens anywhere.
"""
from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path
from typing import List

from .workspace import Workspace, count_lines, read_lines


def generate(ws: Workspace, targets: List[str], meta: dict) -> Path:
    parts: List[str] = []
    parts.append(_head(targets, meta))
    parts.append(_summary_cards(ws))
    parts.append(_live_hosts(ws))
    parts.append(_injection(ws))
    parts.append(_screenshots(ws))
    parts.append(_findings(ws))
    parts.append(_tool_status(meta))
    parts.append(_foot())
    out = ws.root / "report.html"
    out.write_text("\n".join(parts), encoding="utf-8")
    return out


# --------------------------------------------------------------------- #

def _esc(s: str) -> str:
    return html.escape(str(s), quote=True)


def _section(title: str, body: str, sub: str = "") -> str:
    subhtml = f'<span class="sub">{_esc(sub)}</span>' if sub else ""
    return (f'<section><h2>{_esc(title)} {subhtml}</h2>{body}</section>')


def _summary_cards(ws: Workspace) -> str:
    cards = [
        ("Subdomains", count_lines(ws.path("subs/all.txt"))),
        ("Resolved", count_lines(ws.path("subs/resolved.txt"))),
        ("Live hosts", count_lines(ws.path("hosts/live.txt"))),
        ("URLs", count_lines(ws.path("urls/clean.txt"))),
        ("With params", count_lines(ws.path("urls/with_params.txt"))),
        ("JS files", count_lines(ws.path("js/js_urls.txt"))),
        ("Params", count_lines(ws.path("params/all.txt"))),
        ("Inj. candidates", count_lines(ws.path("findings/injection_candidates.txt"))),
    ]
    items = "".join(
        f'<div class="card"><div class="num">{n}</div>'
        f'<div class="lbl">{_esc(label)}</div></div>'
        for label, n in cards)
    return f'<section><div class="cards">{items}</div></section>'


def _live_hosts(ws: Workspace) -> str:
    j = ws.path("hosts/httpx.json")
    rows = []
    for line in read_lines(j):
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        url = o.get("url") or o.get("input", "")
        sc = o.get("status_code") or o.get("status-code") or ""
        title = o.get("title", "")
        tech = o.get("tech") or o.get("technologies") or []
        if isinstance(tech, list):
            tech = ", ".join(tech)
        sc_cls = "ok" if str(sc).startswith("2") else (
            "warn" if str(sc).startswith("3") else "bad")
        rows.append(
            f"<tr><td><a href='{_esc(url)}' target='_blank'>{_esc(url)}</a></td>"
            f"<td><span class='pill {sc_cls}'>{_esc(sc)}</span></td>"
            f"<td>{_esc(title)}</td><td class='dim'>{_esc(tech)}</td></tr>")
    if not rows:
        return ""
    table = ("<table><thead><tr><th>URL</th><th>Status</th><th>Title</th>"
             "<th>Tech</th></tr></thead><tbody>"
             + "".join(rows) + "</tbody></table>")
    return _section("Live hosts", table, f"{len(rows)} hosts")


def _injection(ws: Workspace) -> str:
    combined = ws.path("findings/injection_candidates.txt")
    if count_lines(combined) == 0:
        return ""
    inj_dir = ws.root / "findings" / "injection"
    blocks = []
    if inj_dir.is_dir():
        for f in sorted(inj_dir.glob("*.txt")):
            urls = read_lines(f)
            if not urls:
                continue
            cls = f.stem
            lis = "".join(f"<li>{_esc(u)}</li>" for u in urls[:200])
            more = (f"<li class='dim'>… {len(urls) - 200} more</li>"
                    if len(urls) > 200 else "")
            blocks.append(
                f"<details><summary><span class='tag tag-{_esc(cls)}'>"
                f"{_esc(cls.upper())}</span> {len(urls)} URLs</summary>"
                f"<ul class='urls'>{lis}{more}</ul></details>")
    body = ("<p class='dim'>URLs whose parameters commonly map to each bug "
            "class. Triage manually — heuristic, not confirmation.</p>"
            + "".join(blocks))
    return _section("Injection candidates", body,
                    f"{count_lines(combined)} total")


def _screenshots(ws: Workspace) -> str:
    shots_dir = ws.root / "screenshots"
    if not shots_dir.is_dir():
        return ""
    imgs = []
    for ext in ("*.png", "*.jpeg", "*.jpg"):
        imgs.extend(shots_dir.rglob(ext))
    imgs = sorted(imgs)
    if not imgs:
        return ""
    cells = []
    for img in imgs[:300]:
        rel = img.relative_to(ws.root).as_posix()
        cells.append(
            f"<figure><img loading='lazy' src='{_esc(rel)}'>"
            f"<figcaption>{_esc(img.stem)}</figcaption></figure>")
    return _section("Screenshots", f"<div class='gallery'>{''.join(cells)}</div>",
                    f"{len(imgs)} captured")


def _findings(ws: Workspace) -> str:
    fdir = ws.root / "findings"
    if not fdir.is_dir():
        return ""
    skip = {"injection_candidates.txt"}
    blocks = []
    for f in sorted(fdir.glob("*.txt")):
        if f.name in skip or count_lines(f) == 0:
            continue
        lines = read_lines(f)
        lis = "".join(f"<li>{_esc(x)}</li>" for x in lines[:200])
        blocks.append(
            f"<details><summary>{_esc(f.stem)} "
            f"<span class='dim'>({len(lines)})</span></summary>"
            f"<ul class='urls'>{lis}</ul></details>")
    if not blocks:
        return ""
    return _section("Findings", "".join(blocks))


def _tool_status(meta: dict) -> str:
    ran = meta.get("ran", [])
    skipped = meta.get("skipped", {})
    failed = meta.get("failed", {})
    def chips(items, cls):
        return "".join(f"<span class='chip {cls}'>{_esc(i)}</span>"
                       for i in items)
    body = (f"<p><b>Ran ({len(ran)}):</b> {chips(ran, 'ok')}</p>"
            f"<p><b>Failed ({len(failed)}):</b> {chips(failed, 'bad')}</p>"
            f"<p><b>Skipped ({len(skipped)}):</b> "
            f"{chips(list(skipped), 'dim')}</p>")
    return _section("Tool run status", body)


def _head(targets: List[str], meta: dict) -> str:
    when = datetime.now().strftime("%Y-%m-%d %H:%M")
    tgt = ", ".join(targets[:5]) + ("…" if len(targets) > 5 else "")
    elapsed = meta.get("elapsed", "")
    return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>recoo report · {_esc(tgt)}</title>
<style>{_CSS}</style></head><body>
<header>
  <div class="brand">recoo</div>
  <div class="meta">
    <div><span class="k">target</span> {_esc(tgt)}</div>
    <div><span class="k">generated</span> {_esc(when)}</div>
    <div><span class="k">elapsed</span> {_esc(elapsed)}</div>
  </div>
</header>
<main>"""


def _foot() -> str:
    return ("</main><footer>generated by recoo · made by cataract · "
            "for authorized testing only</footer></body></html>")


_CSS = """
:root{--bg:#0d1117;--panel:#161b22;--line:#21262d;--fg:#e6edf3;
--dim:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;--yellow:#d29922}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.55 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
header{display:flex;align-items:center;gap:28px;padding:22px 32px;
border-bottom:1px solid var(--line);background:var(--panel);position:sticky;top:0;z-index:5}
.brand{font-size:26px;font-weight:800;letter-spacing:1px;color:var(--accent)}
.meta{display:flex;gap:26px;flex-wrap:wrap;color:var(--dim);font-size:13px}
.meta .k{display:block;text-transform:uppercase;font-size:10px;letter-spacing:1px;color:var(--dim)}
.meta div div,.meta>div{color:var(--fg)}
main{padding:24px 32px;max-width:1200px;margin:0 auto}
section{margin:0 0 30px}
h2{font-size:17px;margin:0 0 14px;padding-bottom:8px;border-bottom:1px solid var(--line)}
h2 .sub{font-size:12px;color:var(--dim);font-weight:400;margin-left:8px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:14px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px;text-align:center}
.card .num{font-size:28px;font-weight:800;color:var(--accent)}
.card .lbl{font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:var(--dim);margin-top:4px}
table{width:100%;border-collapse:collapse;background:var(--panel);
border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{text-align:left;padding:9px 12px;border-bottom:1px solid var(--line);font-size:13px;
vertical-align:top;word-break:break-all}
th{background:#1c2230;color:var(--dim);text-transform:uppercase;font-size:10px;letter-spacing:1px}
tr:last-child td{border-bottom:none}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.dim{color:var(--dim)}
.pill{padding:1px 8px;border-radius:20px;font-size:12px;font-weight:700}
.pill.ok{background:rgba(63,185,80,.15);color:var(--green)}
.pill.warn{background:rgba(210,153,34,.15);color:var(--yellow)}
.pill.bad{background:rgba(248,81,73,.15);color:var(--red)}
details{background:var(--panel);border:1px solid var(--line);border-radius:8px;
padding:8px 14px;margin:8px 0}
summary{cursor:pointer;font-weight:600}
ul.urls{list-style:none;margin:10px 0 4px;padding:0;max-height:340px;overflow:auto}
ul.urls li{padding:3px 0;border-bottom:1px solid var(--line);font-family:ui-monospace,monospace;
font-size:12px;word-break:break-all}
.tag{padding:1px 8px;border-radius:6px;font-size:11px;font-weight:800;color:#0d1117}
.tag-sqli{background:#f85149}.tag-xss{background:#d29922}.tag-ssrf{background:#bc8cff}
.tag-lfi{background:#3fb950}.tag-rce{background:#ff7b72}.tag-ssti{background:#58a6ff}
.tag-redirect{background:#79c0ff}.tag-idor{background:#ffa657}
.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:14px}
figure{margin:0;background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden}
figure img{width:100%;height:170px;object-fit:cover;object-position:top;display:block;background:#000}
figcaption{padding:7px 10px;font-size:11px;color:var(--dim);word-break:break-all}
.chip{display:inline-block;padding:2px 9px;margin:2px;border-radius:20px;font-size:12px;
background:#1c2230;border:1px solid var(--line)}
.chip.ok{color:var(--green)}.chip.bad{color:var(--red)}.chip.dim{color:var(--dim)}
footer{padding:24px 32px;color:var(--dim);font-size:12px;border-top:1px solid var(--line);text-align:center}
"""
