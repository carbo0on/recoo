"""Generate a self-contained, interactive HTML report from a workspace.

Produces ``<workspace>/report.html``: a single dark-themed page with a
sticky header, summary cards, tabbed sections (hosts, DNS, ports, URLs,
JS, params, injection, findings, screenshots, tool status), a live
client-side search/filter, and a screenshot lightbox. Everything is
inlined (CSS + JS, no external assets) so the file opens anywhere.
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

from .workspace import Workspace, count_lines, read_lines

# Hard caps so a huge run still produces a browsable (not multi-hundred-MB)
# file. Truncated sections say how many more were omitted.
_URL_CAP = 4000
_LIST_CAP = 1000

_SEV_ORDER = {"critical": 0, "high": 1, "medium": 2,
              "low": 3, "info": 4, "unknown": 5}


def generate(ws: Workspace, targets: List[str], meta: dict) -> Path:
    sections = _build_sections(ws, meta)
    nav = "".join(
        f'<button class="tab" data-target="{sid}">{_esc(label)}'
        f'{f" <span class=count>{cnt}</span>" if cnt else ""}</button>'
        for sid, label, cnt, _ in sections)
    panels = "".join(
        f'<section class="panel" id="{sid}">{body}</section>'
        for sid, _, _, body in sections)
    doc = (_head(targets, meta)
           + f'<nav class="tabs">{nav}</nav>'
           + '<main>' + panels + '</main>'
           + _foot() + _script())
    out = ws.root / "report.html"
    out.write_text(doc, encoding="utf-8")
    return out


# --------------------------------------------------------------------- #
# section builders — each returns (id, label, count, html_body)
# --------------------------------------------------------------------- #

def _build_sections(ws: Workspace, meta: dict) -> List[Tuple[str, str, int, str]]:
    out: List[Tuple[str, str, int, str]] = []
    out.append(("overview", "Overview", 0, _overview(ws, meta)))

    hosts = _parse_httpx(ws)
    if hosts:
        out.append(("hosts", "Live hosts", len(hosts), _hosts_panel(hosts)))

    ports = _parse_ports(ws)
    if ports:
        n = sum(len(v) for v in ports.values())
        out.append(("ports", "Ports", n, _ports_panel(ports)))

    dns = read_lines(ws.path("subs/dnsx_records.txt"))
    if dns:
        out.append(("dns", "DNS", len(dns), _list_panel(
            "DNS records", dns, mono=True)))

    url_body, url_n = _urls_panel(ws)
    if url_n:
        out.append(("urls", "URLs", url_n, url_body))

    params = read_lines(ws.path("params/all.txt"))
    if params:
        out.append(("params", "Params", len(params),
                    _list_panel("Discovered parameters", params, mono=True)))

    inj_body, inj_n = _injection_panel(ws)
    if inj_n:
        out.append(("injection", "Injection", inj_n, inj_body))

    find_body, find_n = _findings_panel(ws)
    if find_n:
        out.append(("findings", "Findings", find_n, find_body))

    shots = _screenshots(ws)
    if shots:
        out.append(("shots", "Screenshots", len(shots),
                    _shots_panel(shots)))

    out.append(("tools", "Tools", 0, _tools_panel(meta)))
    return out


def _overview(ws: Workspace, meta: dict) -> str:
    cards = [
        ("Subdomains", count_lines(ws.path("subs/all.txt")), "hosts"),
        ("Resolved", count_lines(ws.path("subs/resolved.txt")), "hosts"),
        ("Live hosts", count_lines(ws.path("hosts/live.txt")), "hosts"),
        ("URLs", count_lines(ws.path("urls/clean.txt")), "urls"),
        ("With params", count_lines(ws.path("urls/with_params.txt")), "urls"),
        ("JS files", count_lines(ws.path("js/js_urls.txt")), "urls"),
        ("Params", count_lines(ws.path("params/all.txt")), "params"),
        ("Inj. candidates",
         count_lines(ws.path("findings/injection_candidates.txt")), "injection"),
    ]
    items = "".join(
        f'<button class="card" data-jump="{tgt}">'
        f'<div class="num">{n}</div><div class="lbl">{_esc(label)}</div></button>'
        for label, n, tgt in cards)
    ran = len(meta.get("ran", []))
    failed = len(meta.get("failed", {}) or {})
    skipped = len(meta.get("skipped", {}) or {})
    status = (
        f'<div class="runrow">'
        f'<span class="chip ok">ran {ran}</span>'
        f'<span class="chip bad">failed {failed}</span>'
        f'<span class="chip dim">skipped {skipped}</span>'
        f'<span class="chip dim">elapsed {_esc(meta.get("elapsed", "?"))}</span>'
        f'</div>')
    return f'<div class="cards">{items}</div>{status}'


def _hosts_panel(hosts: List[dict]) -> str:
    rows = []
    for o in hosts:
        sc = str(o.get("sc", ""))
        cls = "ok" if sc.startswith("2") else (
            "warn" if sc.startswith("3") else "bad" if sc else "dim")
        tech = ", ".join(o.get("tech", []))
        extra = " ".join(x for x in (o.get("webserver", ""),
                                     "CDN" if o.get("cdn") else "") if x)
        rows.append(
            f'<tr class="row"><td><a href="{_esc(o["url"])}" target="_blank" '
            f'rel="noopener">{_esc(o["url"])}</a></td>'
            f'<td><span class="pill {cls}">{_esc(sc or "-")}</span></td>'
            f'<td>{_esc(o.get("title",""))}</td>'
            f'<td class="dim">{_esc(o.get("ip",""))}</td>'
            f'<td class="dim">{_esc(tech)}</td>'
            f'<td class="dim">{_esc(extra)}</td></tr>')
    return (_searchbar()
            + '<table><thead><tr><th>URL</th><th>Status</th><th>Title</th>'
            '<th>IP</th><th>Tech</th><th>Server</th></tr></thead><tbody>'
            + "".join(rows) + '</tbody></table>')


def _ports_panel(ports: Dict[str, List[str]]) -> str:
    rows = []
    for host in sorted(ports):
        chips = "".join(f'<span class="port">{_esc(p)}</span>'
                        for p in sorted(ports[host], key=_intkey))
        rows.append(f'<tr class="row"><td>{_esc(host)}</td><td>{chips}</td></tr>')
    return (_searchbar()
            + '<table><thead><tr><th>Host</th><th>Open ports</th></tr></thead>'
            '<tbody>' + "".join(rows) + '</tbody></table>')


def _urls_panel(ws: Workspace) -> Tuple[str, int]:
    groups = [
        ("All URLs", read_lines(ws.path("urls/clean.txt"))),
        ("With parameters", read_lines(ws.path("urls/with_params.txt"))),
        ("JS files", read_lines(ws.path("js/js_urls.txt"))),
    ]
    total = sum(len(g) for _, g in groups)
    if total == 0:
        return "", 0
    blocks = [_searchbar()]
    for label, urls in groups:
        if not urls:
            continue
        shown = urls[:_URL_CAP]
        lis = "".join(
            f'<li class="row"><a href="{_esc(u)}" target="_blank" '
            f'rel="noopener">{_esc(u)}</a></li>' for u in shown)
        more = (f'<li class="dim">… {len(urls) - len(shown)} more</li>'
                if len(urls) > len(shown) else "")
        blocks.append(
            f'<details open><summary>{_esc(label)} '
            f'<span class="dim">({len(urls)})</span></summary>'
            f'<ul class="list">{lis}{more}</ul></details>')
    return "".join(blocks), total


def _injection_panel(ws: Workspace) -> Tuple[str, int]:
    combined = ws.path("findings/injection_candidates.txt")
    n = count_lines(combined)
    if n == 0:
        return "", 0
    inj_dir = ws.root / "findings" / "injection"
    blocks = [
        '<p class="dim">URLs whose parameter names commonly map to each bug '
        'class. Heuristic triage — confirm manually.</p>', _searchbar()]
    if inj_dir.is_dir():
        for f in sorted(inj_dir.glob("*.txt")):
            urls = read_lines(f)
            if not urls:
                continue
            cls = f.stem
            shown = urls[:_LIST_CAP]
            lis = "".join(f'<li class="row">{_esc(u)}</li>' for u in shown)
            more = (f'<li class="dim">… {len(urls) - len(shown)} more</li>'
                    if len(urls) > len(shown) else "")
            blocks.append(
                f'<details><summary><span class="tag tag-{_esc(cls)}">'
                f'{_esc(cls.upper())}</span> {len(urls)} URLs</summary>'
                f'<ul class="list">{lis}{more}</ul></details>')
    return "".join(blocks), n


def _findings_panel(ws: Workspace) -> Tuple[str, int]:
    fdir = ws.root / "findings"
    if not fdir.is_dir():
        return "", 0
    blocks = []
    total = 0

    # nuclei: render as a severity-sorted table when present.
    nuclei = ws.path("findings/nuclei.txt")
    nlines = read_lines(nuclei)
    if nlines:
        rows = []
        for ln in sorted(nlines, key=_sev_key):
            sev = _sev_of(ln)
            rows.append(
                f'<tr class="row"><td><span class="pill sev-{sev}">{sev}'
                f'</span></td><td class="mono">{_esc(ln)}</td></tr>')
        total += len(nlines)
        blocks.append(
            f'<h3>nuclei <span class="dim">({len(nlines)})</span></h3>'
            '<table><thead><tr><th>Severity</th><th>Match</th></tr></thead>'
            '<tbody>' + "".join(rows) + '</tbody></table>')

    skip = {"injection_candidates.txt", "nuclei.txt"}
    other = []
    for f in sorted(fdir.glob("*.txt")):
        if f.name in skip:
            continue
        lines = read_lines(f)
        if not lines:
            continue
        total += len(lines)
        shown = lines[:_LIST_CAP]
        lis = "".join(f'<li class="row mono">{_esc(x)}</li>' for x in shown)
        more = (f'<li class="dim">… {len(lines) - len(shown)} more</li>'
                if len(lines) > len(shown) else "")
        other.append(
            f'<details><summary>{_esc(f.stem)} '
            f'<span class="dim">({len(lines)})</span></summary>'
            f'<ul class="list">{lis}{more}</ul></details>')
    if total == 0:
        return "", 0
    return _searchbar() + "".join(blocks) + "".join(other), total


def _shots_panel(shots: List[Tuple[Path, str]]) -> str:
    cells = []
    for img, rel in shots:
        cells.append(
            f'<figure class="row" data-name="{_esc(img.stem)}">'
            f'<img loading="lazy" src="{_esc(rel)}" data-full="{_esc(rel)}">'
            f'<figcaption>{_esc(img.stem)}</figcaption></figure>')
    return (_searchbar()
            + f'<div class="gallery">{"".join(cells)}</div>'
            '<div id="lightbox" onclick="this.style.display=\'none\'">'
            '<img id="lightbox-img"></div>')


def _tools_panel(meta: dict) -> str:
    ran = meta.get("ran", []) or []
    failed = meta.get("failed", {}) or {}
    skipped = meta.get("skipped", {}) or {}
    ran_only = [t for t in ran if t not in failed]
    rows = []
    for name in ran_only:
        rows.append(f'<tr class="row"><td>{_esc(name)}</td>'
                    f'<td><span class="pill ok">ran</span></td><td></td></tr>')
    for name, reason in failed.items():
        rows.append(f'<tr class="row"><td>{_esc(name)}</td>'
                    f'<td><span class="pill bad">failed</span></td>'
                    f'<td class="dim mono">{_esc(reason)}</td></tr>')
    for name, reason in skipped.items():
        rows.append(f'<tr class="row"><td>{_esc(name)}</td>'
                    f'<td><span class="pill dim">skipped</span></td>'
                    f'<td class="dim">{_esc(reason)}</td></tr>')
    return (_searchbar()
            + '<table><thead><tr><th>Tool</th><th>State</th><th>Detail</th>'
            '</tr></thead><tbody>' + "".join(rows) + '</tbody></table>')


def _list_panel(title: str, lines: List[str], mono: bool = False) -> str:
    shown = lines[:_LIST_CAP * 4]
    cls = "row mono" if mono else "row"
    lis = "".join(f'<li class="{cls}">{_esc(x)}</li>' for x in shown)
    more = (f'<li class="dim">… {len(lines) - len(shown)} more</li>'
            if len(lines) > len(shown) else "")
    return _searchbar() + f'<ul class="list big">{lis}{more}</ul>'


# --------------------------------------------------------------------- #
# data parsing
# --------------------------------------------------------------------- #

def _parse_httpx(ws: Workspace) -> List[dict]:
    out = []
    for line in read_lines(ws.path("hosts/httpx.json")):
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        url = o.get("url") or o.get("input")
        if not url:
            continue
        tech = o.get("tech") or o.get("technologies") or []
        if isinstance(tech, str):
            tech = [tech]
        a = o.get("a")
        ip = o.get("host") or (a[0] if isinstance(a, list) and a else "")
        out.append({
            "url": url,
            "sc": o.get("status_code") or o.get("status-code") or "",
            "title": o.get("title", ""),
            "tech": tech,
            "ip": ip,
            "webserver": o.get("webserver", ""),
            "cdn": o.get("cdn", False),
        })
    return out


def _parse_ports(ws: Workspace) -> Dict[str, List[str]]:
    ports: Dict[str, List[str]] = {}
    for line in read_lines(ws.path("hosts/ports.txt")):
        if ":" not in line:
            continue
        host, _, port = line.rpartition(":")
        if not host or not port.isdigit():
            continue
        ports.setdefault(host, [])
        if port not in ports[host]:
            ports[host].append(port)
    return ports


def _screenshots(ws: Workspace) -> List[Tuple[Path, str]]:
    sdir = ws.root / "screenshots"
    if not sdir.is_dir():
        return []
    imgs: List[Path] = []
    for ext in ("*.png", "*.jpeg", "*.jpg"):
        imgs.extend(sdir.rglob(ext))
    return [(p, p.relative_to(ws.root).as_posix()) for p in sorted(imgs)[:400]]


def _sev_of(line: str) -> str:
    m = re.search(r"\[(critical|high|medium|low|info|unknown)\]", line, re.I)
    return m.group(1).lower() if m else "unknown"


def _sev_key(line: str) -> int:
    return _SEV_ORDER.get(_sev_of(line), 5)


def _intkey(p: str) -> int:
    try:
        return int(p)
    except ValueError:
        return 0


# --------------------------------------------------------------------- #
# shell / chrome
# --------------------------------------------------------------------- #

def _esc(s) -> str:
    return html.escape(str(s), quote=True)


def _searchbar() -> str:
    return ('<input class="search" type="search" '
            'placeholder="filter this section…" '
            'oninput="recooFilter(this)">')


def _head(targets: List[str], meta: dict) -> str:
    when = datetime.now().strftime("%Y-%m-%d %H:%M")
    tgt = ", ".join(targets[:5]) + ("…" if len(targets) > 5 else "")
    return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>recoo report · {_esc(tgt)}</title>
<style>{_CSS}</style></head><body>
<header>
  <div class="brand">recoo</div>
  <div class="meta">
    <div><span class="k">target</span><span>{_esc(tgt)}</span></div>
    <div><span class="k">profile</span><span>{_esc(meta.get('profile') or '—')}</span></div>
    <div><span class="k">generated</span><span>{_esc(when)}</span></div>
    <div><span class="k">elapsed</span><span>{_esc(meta.get('elapsed',''))}</span></div>
  </div>
</header>"""


def _foot() -> str:
    return ('<footer>generated by recoo · made by cataract · '
            'for authorized testing only</footer>')


_CSS = """
:root{--bg:#0b0f14;--panel:#141a22;--line:#222b36;--fg:#e6edf3;--dim:#8b949e;
--accent:#58a6ff;--accent2:#1f6feb;--green:#3fb950;--red:#f85149;--yellow:#d29922;
--purple:#bc8cff;--orange:#ffa657}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.55 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
header{display:flex;align-items:center;gap:28px;padding:18px 30px;
border-bottom:1px solid var(--line);
background:linear-gradient(120deg,#0d1620,#141a22);position:sticky;top:0;z-index:20}
.brand{font-size:26px;font-weight:800;letter-spacing:1px;
background:linear-gradient(90deg,#58a6ff,#bc8cff);-webkit-background-clip:text;
background-clip:text;-webkit-text-fill-color:transparent}
.meta{display:flex;gap:26px;flex-wrap:wrap;font-size:13px}
.meta .k{display:block;text-transform:uppercase;font-size:10px;letter-spacing:1px;color:var(--dim)}
nav.tabs{position:sticky;top:61px;z-index:15;display:flex;gap:4px;flex-wrap:wrap;
padding:10px 24px;background:rgba(11,15,20,.92);backdrop-filter:blur(6px);
border-bottom:1px solid var(--line)}
.tab{background:transparent;border:1px solid transparent;color:var(--dim);
padding:7px 14px;border-radius:8px;cursor:pointer;font-size:13px;font-weight:600}
.tab:hover{color:var(--fg);background:var(--panel)}
.tab.active{color:#fff;background:var(--accent2);border-color:var(--accent)}
.tab .count{display:inline-block;min-width:18px;padding:0 5px;margin-left:5px;
border-radius:10px;background:rgba(255,255,255,.18);font-size:11px;font-weight:700}
main{padding:22px 30px;max-width:1280px;margin:0 auto}
.panel{display:none;animation:fade .18s ease}
.panel.active{display:block}
@keyframes fade{from{opacity:0;transform:translateY(4px)}to{opacity:1}}
h3{font-size:15px;margin:24px 0 10px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:14px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;
padding:18px;text-align:center;cursor:pointer;transition:.15s}
.card:hover{border-color:var(--accent);transform:translateY(-2px)}
.card .num{font-size:30px;font-weight:800;color:var(--accent)}
.card .lbl{font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:var(--dim);margin-top:5px}
.runrow{margin-top:18px;display:flex;gap:8px;flex-wrap:wrap}
.search{width:100%;margin:0 0 14px;padding:10px 14px;border-radius:10px;
border:1px solid var(--line);background:var(--panel);color:var(--fg);font-size:14px}
.search:focus{outline:none;border-color:var(--accent)}
table{width:100%;border-collapse:collapse;background:var(--panel);
border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{text-align:left;padding:9px 12px;border-bottom:1px solid var(--line);
font-size:13px;vertical-align:top;word-break:break-all}
th{background:#1b2330;color:var(--dim);text-transform:uppercase;font-size:10px;
letter-spacing:1px;position:sticky;top:0}
tr:last-child td{border-bottom:none}
tbody tr:hover{background:rgba(88,166,255,.06)}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.dim{color:var(--dim)}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
.pill{display:inline-block;padding:1px 9px;border-radius:20px;font-size:12px;font-weight:700}
.pill.ok{background:rgba(63,185,80,.16);color:var(--green)}
.pill.warn{background:rgba(210,153,34,.16);color:var(--yellow)}
.pill.bad{background:rgba(248,81,73,.16);color:var(--red)}
.pill.dim{background:#1b2330;color:var(--dim)}
.sev-critical{background:rgba(248,81,73,.2);color:#ff7b72}
.sev-high{background:rgba(255,123,114,.16);color:var(--orange)}
.sev-medium{background:rgba(210,153,34,.16);color:var(--yellow)}
.sev-low{background:rgba(88,166,255,.14);color:var(--accent)}
.sev-info,.sev-unknown{background:#1b2330;color:var(--dim)}
.port{display:inline-block;margin:2px;padding:1px 8px;border-radius:6px;
background:#1b2330;border:1px solid var(--line);font-family:ui-monospace,monospace;font-size:12px}
details{background:var(--panel);border:1px solid var(--line);border-radius:10px;
padding:8px 14px;margin:10px 0}
summary{cursor:pointer;font-weight:600}
ul.list{list-style:none;margin:10px 0 4px;padding:0;max-height:420px;overflow:auto}
ul.list.big{max-height:640px}
ul.list li{padding:4px 0;border-bottom:1px solid var(--line);
font-family:ui-monospace,monospace;font-size:12px;word-break:break-all}
.tag{padding:1px 8px;border-radius:6px;font-size:11px;font-weight:800;color:#0b0f14}
.tag-sqli{background:#f85149}.tag-xss{background:#d29922}.tag-ssrf{background:#bc8cff}
.tag-lfi{background:#3fb950}.tag-rce{background:#ff7b72}.tag-ssti{background:#58a6ff}
.tag-redirect{background:#79c0ff}.tag-idor{background:#ffa657}
.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:14px}
figure{margin:0;background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden;cursor:pointer}
figure img{width:100%;height:170px;object-fit:cover;object-position:top;display:block;background:#000}
figcaption{padding:7px 10px;font-size:11px;color:var(--dim);word-break:break-all}
.chip{display:inline-block;padding:3px 11px;border-radius:20px;font-size:12px;
background:#1b2330;border:1px solid var(--line)}
.chip.ok{color:var(--green)}.chip.bad{color:var(--red)}.chip.dim{color:var(--dim)}
#lightbox{display:none;position:fixed;inset:0;z-index:50;background:rgba(0,0,0,.9);
align-items:center;justify-content:center;padding:30px}
#lightbox img{max-width:100%;max-height:100%;border:1px solid var(--line);border-radius:8px}
footer{padding:24px 30px;color:var(--dim);font-size:12px;border-top:1px solid var(--line);text-align:center}
"""


def _script() -> str:
    return """<script>
(function(){
  var tabs=[].slice.call(document.querySelectorAll('.tab'));
  var panels=[].slice.call(document.querySelectorAll('.panel'));
  function show(id){
    tabs.forEach(function(t){t.classList.toggle('active',t.dataset.target===id)});
    panels.forEach(function(p){p.classList.toggle('active',p.id===id)});
  }
  tabs.forEach(function(t){t.onclick=function(){show(t.dataset.target)}});
  document.querySelectorAll('.card[data-jump]').forEach(function(c){
    c.onclick=function(){var id=c.dataset.jump;
      if(document.getElementById(id))show(id);}});
  if(tabs.length)show(tabs[0].dataset.target);

  window.recooFilter=function(input){
    var q=input.value.toLowerCase();
    var panel=input.closest('.panel');
    panel.querySelectorAll('.row').forEach(function(el){
      el.style.display=el.textContent.toLowerCase().indexOf(q)>-1?'':'none';
    });
  };

  var lb=document.getElementById('lightbox');
  if(lb){var img=document.getElementById('lightbox-img');
    document.querySelectorAll('figure img[data-full]').forEach(function(im){
      im.onclick=function(){img.src=im.dataset.full;lb.style.display='flex';};
    });}
})();
</script></body></html>"""
