"""Auto-provisioning: make a run self-sufficient with zero manual setup.

Before the pipeline runs, this module best-effort installs whatever the
*enabled* tools need but the machine is missing:

* external binaries (Go / pip / apt packages),
* OneListForAll wordlists for the active tier (+ resolvers),
* nuclei templates,
* gf patterns.

Everything here is best-effort and network-tolerant: each step is wrapped
so a failure (no network, no Go, no sudo) is logged and skipped rather
than aborting the run. recoo already degrades gracefully — a tool whose
binary is still missing is simply skipped — so bootstrap only ever
improves coverage, never blocks it.

Disable with ``--no-bootstrap``.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import Config, resolve_wordlist, wordlist_path
from .ui import Logger

# --- how to obtain each external binary ------------------------------------
# name -> ("go"|"pip"|"apt", spec). Mirrors install.sh, plus the tools that
# install.sh left as "manual" but are in fact go/pip installable.
GO_INSTALL: Dict[str, str] = {
    "subfinder": "github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest",
    "httpx":     "github.com/projectdiscovery/httpx/cmd/httpx@latest",
    "dnsx":      "github.com/projectdiscovery/dnsx/cmd/dnsx@latest",
    "naabu":     "github.com/projectdiscovery/naabu/v2/cmd/naabu@latest",
    "katana":    "github.com/projectdiscovery/katana/cmd/katana@latest",
    "nuclei":    "github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest",
    "asnmap":    "github.com/projectdiscovery/asnmap/cmd/asnmap@latest",
    "chaos":     "github.com/projectdiscovery/chaos-client/cmd/chaos@latest",
    "notify":    "github.com/projectdiscovery/notify/cmd/notify@latest",
    "gau":       "github.com/lc/gau/v2/cmd/gau@latest",
    "waybackurls": "github.com/tomnomnom/waybackurls@latest",
    "anew":      "github.com/tomnomnom/anew@latest",
    "gf":        "github.com/tomnomnom/gf@latest",
    "qsreplace": "github.com/tomnomnom/qsreplace@latest",
    "assetfinder": "github.com/tomnomnom/assetfinder@latest",
    "kxss":      "github.com/Emoe/kxss@latest",
    "subzy":     "github.com/PentestPad/subzy@latest",
    "ffuf":      "github.com/ffuf/ffuf/v2@latest",
    "gospider":  "github.com/jaeles-project/gospider@latest",
    "jsluice":   "github.com/BishopFox/jsluice/cmd/jsluice@latest",
    "puredns":   "github.com/d3mondev/puredns/v2@latest",
    "gotator":   "github.com/Josue87/gotator@latest",
    "gowitness": "github.com/sensepost/gowitness@latest",
    "hakrawler": "github.com/hakluke/hakrawler@latest",
    "dalfox":    "github.com/hahwul/dalfox/v2@latest",
    "getJS":     "github.com/003random/getJS/v2@latest",
    # extra: install.sh marks these "manual", but they are go-installable.
    "amass":     "github.com/owasp-amass/amass/v4/...@master",
    "github-subdomains": "github.com/gwen001/github-subdomains@latest",
    "trufflehog": "github.com/trufflesecurity/trufflehog/v3@latest",
    "mantra":    "github.com/Brosck/mantra@latest",
}

PIP_INSTALL: Dict[str, str] = {
    "arjun":       "arjun",
    "uro":         "uro",
    "paramspider": "paramspider",
    "s3scanner":   "s3scanner",
}

# System packages best-effort via apt (Debian/Ubuntu codespaces). massdns is
# a hard dependency of puredns and is otherwise a manual C build.
APT_INSTALL: Dict[str, str] = {
    "curl":    "curl",
    "jq":      "jq",
    "massdns": "massdns",
}


def _sh(cmd: List[str], timeout: int = 600) -> Tuple[int, str]:
    """Run a command quietly; return (rc, combined-output). Never raises."""
    try:
        proc = subprocess.run(cmd, timeout=timeout, capture_output=True,
                              text=True)
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except FileNotFoundError:
        return 127, "not found"
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    except Exception as e:  # pragma: no cover
        return 1, str(e)


def _have(binary: str) -> bool:
    return shutil.which(binary) is not None


def _go_bin_dir() -> Optional[str]:
    """Best guess at GOPATH/bin so freshly installed Go tools are usable."""
    rc, out = _sh(["go", "env", "GOPATH"], timeout=30)
    gopath = out.strip().splitlines()[0] if rc == 0 and out.strip() else ""
    if not gopath:
        gopath = os.path.expanduser("~/go")
    return str(Path(gopath) / "bin")


def _ensure_path(extra: Optional[str], prepend: bool = True) -> None:
    """Make ``extra`` usable on PATH for this process.

    ``prepend`` puts it first (moving it ahead if already present) — used
    for the Go bin dir so ProjectDiscovery tools win. With ``prepend=False``
    the dir is *appended*, so e.g. a pip-installed ``httpx`` in
    ``~/.local/bin`` can never shadow the real Go ``httpx`` on PATH.
    """
    if not extra or not Path(extra).is_dir():
        return
    parts = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    if prepend:
        parts = [extra] + [p for p in parts if p != extra]
    elif extra not in parts:
        parts = parts + [extra]
    os.environ["PATH"] = os.pathsep.join(parts)


class Bootstrap:
    def __init__(self, cfg: Config, log: Logger, repo_root: Path):
        self.cfg = cfg
        self.log = log
        self.repo_root = repo_root
        self._go_ok: Optional[bool] = None
        self._pip: Optional[List[str]] = None
        self._apt_ok: Optional[bool] = None

    # ---- capability probes ----------------------------------------------

    def _go(self) -> bool:
        if self._go_ok is None:
            self._go_ok = _have("go")
            if self._go_ok:
                _ensure_path(_go_bin_dir())
        return self._go_ok

    def _pip_cmd(self) -> Optional[List[str]]:
        if self._pip is None:
            for c in (["pip3"], ["pip"], ["python3", "-m", "pip"]):
                if _have(c[0]):
                    self._pip = c
                    break
            else:
                self._pip = []
            _ensure_path(os.path.expanduser("~/.local/bin"), prepend=False)
        return self._pip or None

    def _apt(self) -> bool:
        if self._apt_ok is None:
            self._apt_ok = _have("apt-get")
        return self._apt_ok

    # ---- installers ------------------------------------------------------

    def _install_binary(self, name: str) -> bool:
        """Try every known channel to obtain ``name``. Returns success."""
        if name in APT_INSTALL and self._apt():
            if self._apt_install(APT_INSTALL[name]) and _have(name):
                return True
        if name in GO_INSTALL and self._go():
            self.log.info(f"  go install {name} …")
            rc, _ = _sh(["go", "install", "-v", GO_INSTALL[name]], timeout=600)
            _ensure_path(_go_bin_dir())
            if rc == 0 and _have(name):
                self.log.ok(f"  installed {name}")
                return True
        if name in PIP_INSTALL and self._pip_cmd():
            self.log.info(f"  pip install {name} …")
            rc, _ = _sh(self._pip_cmd() + ["install", "--quiet", "--user",
                                           PIP_INSTALL[name]], timeout=600)
            _ensure_path(os.path.expanduser("~/.local/bin"), prepend=False)
            if rc == 0 and _have(name):
                self.log.ok(f"  installed {name}")
                return True
        return False

    def _apt_install(self, pkg: str) -> bool:
        sudo = ["sudo"] if (os.geteuid() != 0 and _have("sudo")) else []
        rc, _ = _sh(sudo + ["apt-get", "install", "-y", "-q", pkg], timeout=300)
        return rc == 0

    # ---- steps -----------------------------------------------------------

    def install_missing_binaries(self) -> None:
        """Install binaries required by enabled tools that aren't present."""
        wanted: List[str] = []
        for t in self.cfg.tools:
            if not t.enabled:
                continue
            for b in t.bins:
                if b not in wanted and not _have(b):
                    wanted.append(b)
        if not wanted:
            return
        installable = [b for b in wanted
                       if b in GO_INSTALL or b in PIP_INSTALL or b in APT_INSTALL]
        if not installable:
            return
        self.log.info(f"bootstrap: installing {len(installable)} missing "
                      f"tool(s): {', '.join(installable)}")
        if any(b in GO_INSTALL for b in installable) and not self._go():
            self.log.warn("  Go not found — skipping Go-based tools "
                          "(install Go to enable them)")
        failed = []
        for b in installable:
            if not self._install_binary(b):
                failed.append(b)
        unknown = [b for b in wanted if b not in installable]
        if unknown:
            self.log.warn(f"  no auto-install recipe for: {', '.join(unknown)} "
                          f"(install manually if you need them)")
        if failed:
            self.log.warn(f"  could not auto-install: {', '.join(failed)} "
                          f"(network/toolchain?) — they'll be skipped")

    def ensure_wordlists(self) -> None:
        """Download the active wordlist tier if no usable list is present."""
        s = self.cfg.settings
        roles_needed = self._wordlist_roles_in_use()
        if not roles_needed:
            return
        missing = [r for r in roles_needed
                   if not Path(resolve_wordlist(s, r)).exists()]
        if not missing:
            return
        tier = str(s.get("wordlist_size", "short"))
        script = self.repo_root / "download-wordlists.sh"
        if not script.exists():
            self.log.warn("bootstrap: download-wordlists.sh not found; "
                          "wordlist steps may be skipped")
            return
        self.log.info(f"bootstrap: fetching '{tier}' wordlists "
                      f"(missing: {', '.join(missing)}) …")
        rc, _ = _sh(["bash", str(script), tier], timeout=900)
        # 'full' big lists aren't in the repo; the smaller tiers it does
        # fetch are enough thanks to the auto-downgrade in resolve_wordlist.
        still = [r for r in missing if not Path(resolve_wordlist(s, r)).exists()]
        if still:
            self.log.warn(f"  wordlists still missing for: {', '.join(still)}")
        else:
            self.log.ok("  wordlists ready")

    def ensure_resolvers(self) -> None:
        """Guarantee a resolvers file exists (download, else write a default)."""
        s = self.cfg.settings
        res = str(s.get("resolvers", "wordlists/resolvers.txt"))
        if Path(res).exists() and Path(res).stat().st_size > 0:
            return
        # Only matters if something actually uses {resolvers}.
        if not any(t.enabled and "{resolvers}" in t.cmd for t in self.cfg.tools):
            return
        Path(res).parent.mkdir(parents=True, exist_ok=True)
        url = ("https://raw.githubusercontent.com/trickest/resolvers/"
               "main/resolvers.txt")
        rc, _ = _sh(["bash", "-c",
                     f"curl -fsSL {url!r} -o {res!r}"], timeout=120)
        if rc == 0 and Path(res).exists() and Path(res).stat().st_size > 0:
            self.log.ok("bootstrap: resolvers ready")
            return
        # Offline fallback: a small set of reliable public resolvers.
        Path(res).write_text("\n".join([
            "1.1.1.1", "1.0.0.1", "8.8.8.8", "8.8.4.4",
            "9.9.9.9", "149.112.112.112", "208.67.222.222", "208.67.220.220",
            "64.6.64.6", "64.6.65.6",
        ]) + "\n")
        self.log.warn(f"bootstrap: resolvers download failed; wrote "
                      f"{Path(res).name} with public DNS fallback")

    def ensure_nuclei_templates(self) -> None:
        """Update nuclei templates so nuclei stages actually have signatures."""
        if not any(t.enabled and "nuclei" in t.bins for t in self.cfg.tools):
            return
        if not _have("nuclei"):
            return
        templ = Path(os.path.expanduser("~/nuclei-templates"))
        if templ.is_dir() and any(templ.iterdir()):
            return
        self.log.info("bootstrap: updating nuclei templates …")
        rc, _ = _sh(["nuclei", "-update-templates", "-silent"], timeout=600)
        if rc == 0:
            self.log.ok("  nuclei templates ready")
        else:
            self.log.warn("  nuclei template update failed (network?)")

    def ensure_gf_patterns(self) -> None:
        """Install gf patterns so the gf triage step has something to match."""
        uses_gf = any(t.enabled and ("gf" in t.bins or t.builtin == "gf_patterns")
                      for t in self.cfg.tools)
        if not uses_gf or not _have("gf"):
            return
        gf_dir = Path(os.path.expanduser("~/.gf"))
        if gf_dir.is_dir() and any(gf_dir.glob("*.json")):
            return
        if not _have("git"):
            return
        gf_dir.mkdir(parents=True, exist_ok=True)
        self.log.info("bootstrap: installing gf patterns …")
        # tomnomnom's stock patterns + the popular community pack.
        for repo, sub in (
            ("https://github.com/tomnomnom/gf", "examples"),
            ("https://github.com/1ndianl33t/Gf-Patterns", ""),
        ):
            tmp = gf_dir / ".src"
            _sh(["rm", "-rf", str(tmp)], timeout=30)
            rc, _ = _sh(["git", "clone", "--depth", "1", repo, str(tmp)],
                        timeout=120)
            if rc != 0:
                continue
            src = tmp / sub if sub else tmp
            for j in Path(src).glob("*.json"):
                shutil.copy(j, gf_dir / j.name)
            _sh(["rm", "-rf", str(tmp)], timeout=30)
        if any(gf_dir.glob("*.json")):
            self.log.ok(f"  gf patterns ready ({len(list(gf_dir.glob('*.json')))})")
        else:
            self.log.warn("  gf pattern install failed (network?)")

    # ---- helpers ---------------------------------------------------------

    def _wordlist_roles_in_use(self) -> List[str]:
        roles = []
        for role in ("content", "dns", "params", "perms"):
            token = f"{{wordlist_{role}}}"
            if any(t.enabled and token in t.cmd for t in self.cfg.tools):
                roles.append(role)
        return roles

    # ---- orchestration ---------------------------------------------------

    def run(self) -> None:
        self.log.info("bootstrap: checking prerequisites "
                      "(use --no-bootstrap to skip)")
        for step in (self.install_missing_binaries,
                     self.ensure_wordlists,
                     self.ensure_resolvers,
                     self.ensure_nuclei_templates,
                     self.ensure_gf_patterns):
            try:
                step()
            except Exception as e:  # never let provisioning abort a run
                self.log.warn(f"bootstrap step {step.__name__} errored: {e}")


def run(cfg: Config, log: Logger, repo_root: Path) -> None:
    Bootstrap(cfg, log, repo_root).run()
