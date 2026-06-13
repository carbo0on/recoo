"""Configuration: load the tool registry + settings, then apply overrides.

Resolution order (later wins):
    1. packaged default registry .......... recoo/tools.yaml
    2. user config file ................... --config / config.yaml
    3. command-line filters ............... --enable/--disable/--only/...

This is the layer that makes tool selection effortless: a single
`enabled: false` in YAML, or `--disable amass` on the CLI, takes a tool
out of the run without touching anything else.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .tool import Tool

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

_PKG = Path(__file__).resolve().parent
DEFAULT_REGISTRY = _PKG / "tools.yaml"

# Built-in defaults for the {placeholders} usable in command templates.
DEFAULT_SETTINGS: Dict[str, object] = {
    "threads": 40,
    "timeout": 1800,            # global per-tool timeout (seconds)
    "resolvers": "wordlists/resolvers.txt",

    # --- Wordlists: OneListForAll (six2dez) integration -----------------
    # The {wordlist_*} placeholders resolve from `wordlists_dir` +
    # `wordlist_sizes[role][wordlist_size]`. Pick the tier with
    # `wordlist_size` (or --wordlist-size); depth profiles set it too
    # (fast->micro, medium->short, deep->full). A non-empty per-role
    # override below always wins.
    "wordlists_dir": "wordlists/OneListForAll",
    "wordlist_size": "short",          # micro | short | full
    "wordlist_sizes": {
        "content": {"micro": "onelistforallmicro.txt",
                    "short": "onelistforallshort.txt",
                    "full":  "onelistforall_big.txt"},
        "dns":     {"micro": "dict/dns_short.txt",
                    "short": "dict/subdomains_short.txt",
                    "full":  "dict/subdomains_long.txt"},
        "params":  {"micro": "dict/parameters_short.txt",
                    "short": "dict/parameters_short.txt",
                    "full":  "dict/parameters_long.txt"},
        "perms":   {"micro": "dict/permutations_short.txt",
                    "short": "dict/permutations_short.txt",
                    "full":  "dict/permutations_long.txt"},
    },
    # Explicit per-role overrides (leave empty to use the size tiers).
    "wordlist_dns": "",
    "wordlist_content": "",
    "wordlist_params": "",
    "wordlist_perms": "",

    "github_token": "",
    "gf_patterns": ["ssrf", "redirect", "idor", "xss", "lfi", "sqli",
                    "rce", "ssti", "debug_logic"],
    "api_paths": ["/swagger-ui", "/swagger.json", "/openapi.json",
                  "/api-docs", "/v2/api-docs", "/v3/api-docs", "/redoc",
                  "/graphql", "/graphiql", "/.git/config", "/actuator",
                  "/actuator/health", "/.env"],
    "notify": False,
    "vars": {},
}

# Roles whose {wordlist_<role>} placeholder is size-resolved.
WORDLIST_ROLES = ("content", "dns", "params", "perms")


@dataclass
class Config:
    settings: Dict[str, object] = field(default_factory=dict)
    tools: List[Tool] = field(default_factory=list)
    profiles: Dict[str, dict] = field(default_factory=dict)

    def by_name(self, name: str) -> Optional[Tool]:
        for t in self.tools:
            if t.name == name:
                return t
        return None

    def stages_in_order(self) -> List[str]:
        seen: List[str] = []
        for t in self.tools:
            if t.stage not in seen:
                seen.append(t.stage)
        return seen


def _load_yaml(path: Path) -> dict:
    if yaml is None:
        raise SystemExit(
            "PyYAML is required. Install it with:  pip install pyyaml")
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}


def load(user_config: Optional[str] = None,
         registry: Optional[str] = None) -> Config:
    """Build a Config from the default registry + optional user config."""
    reg_path = Path(registry) if registry else DEFAULT_REGISTRY
    reg = _load_yaml(reg_path)

    settings = dict(DEFAULT_SETTINGS)
    settings.update(reg.get("settings", {}) or {})

    tool_defs: Dict[str, dict] = dict(reg.get("tools", {}) or {})

    if user_config:
        user = _load_yaml(Path(user_config))
        settings.update(user.get("settings", {}) or {})
        # User config may override individual fields per tool (e.g. enabled).
        for name, override in (user.get("tools", {}) or {}).items():
            if name not in tool_defs:
                tool_defs[name] = {}
            if isinstance(override, bool):       # shorthand: `mytool: false`
                tool_defs[name]["enabled"] = override
            elif isinstance(override, dict):
                tool_defs[name].update(override)

    profiles: Dict[str, dict] = dict(reg.get("profiles", {}) or {})
    if user_config:
        user = _load_yaml(Path(user_config))
        for name, override in (user.get("profiles", {}) or {}).items():
            profiles[name] = override

    tools = [Tool.from_dict(name, d) for name, d in tool_defs.items()]
    return Config(settings=settings, tools=tools, profiles=profiles)


def wordlist_path(settings: Dict[str, object], role: str,
                  size: Optional[str] = None) -> str:
    """Resolve a {wordlist_<role>} path.

    An explicit per-role override (settings['wordlist_<role>']) wins;
    otherwise build it from wordlists_dir + the size tier (defaulting to
    the active settings['wordlist_size']).
    """
    explicit = settings.get(f"wordlist_{role}")
    if explicit:
        return str(explicit)
    sizes = (settings.get("wordlist_sizes") or {}).get(role, {})
    size = size or settings.get("wordlist_size", "short")
    rel = sizes.get(size) or sizes.get("short") or ""
    if not rel:
        return ""
    return str(Path(settings.get("wordlists_dir", "wordlists")) / rel)


# When the requested tier isn't on disk, fall back to a smaller one that is
# (rather than skipping the step). Order = requested first, then degrade.
_FALLBACK_ORDER = {
    "full":  ["full", "short", "micro"],
    "short": ["short", "micro", "full"],
    "micro": ["micro", "short", "full"],
}


def resolve_wordlist(settings: Dict[str, object], role: str,
                     size: Optional[str] = None) -> str:
    """Resolve a wordlist to an existing file, auto-downgrading the tier.

    If the requested size tier (or the active one) isn't present on disk,
    transparently fall back to the next smaller tier that *does* exist —
    so e.g. a missing ``full`` list quietly uses ``short`` or ``micro``
    instead of skipping the tool entirely. An explicit per-role override
    always wins. Returns the nominal requested path if nothing exists yet
    (so the caller can decide to download or skip).
    """
    explicit = settings.get(f"wordlist_{role}")
    if explicit:
        return str(explicit)
    size = size or settings.get("wordlist_size", "short")
    order = _FALLBACK_ORDER.get(size, [size, "short", "micro", "full"])
    seen: List[str] = []
    for sz in order:
        if sz in seen:
            continue
        seen.append(sz)
        p = wordlist_path(settings, role, sz)
        if p and Path(p).exists():
            return p
    return wordlist_path(settings, role, size)


def apply_profile(cfg: Config, profile: str) -> None:
    """Enable exactly the tools listed in the named depth profile.

    A profile is a curated, depth-based slice of the pipeline (fast /
    medium / deep) — an axis orthogonal to stages. Selecting one sets the
    enabled set; --enable/--disable and interactive selection refine it.
    """
    prof = cfg.profiles.get(profile)
    if prof is None:
        avail = ", ".join(cfg.profiles) or "(none defined)"
        raise SystemExit(f"Unknown profile '{profile}'. Available: {avail}")
    wanted = set(prof.get("tools", []))
    names = {t.name for t in cfg.tools}
    unknown = wanted - names
    if unknown:
        raise SystemExit(
            f"Profile '{profile}' references unknown tool(s): "
            f"{', '.join(sorted(unknown))}")
    for t in cfg.tools:
        t.enabled = t.name in wanted
    # A profile may also pick a wordlist size tier (CLI flag still wins).
    if prof.get("wordlist_size"):
        cfg.settings["wordlist_size"] = prof["wordlist_size"]


def apply_cli_filters(cfg: Config,
                      only: Optional[List[str]] = None,
                      enable: Optional[List[str]] = None,
                      disable: Optional[List[str]] = None,
                      stages: Optional[List[str]] = None,
                      skip_stages: Optional[List[str]] = None,
                      tags_exclude: Optional[List[str]] = None) -> None:
    """Mutate tool.enabled flags according to CLI selection flags."""
    names = {t.name for t in cfg.tools}

    def _warn_unknown(label: str, given: List[str]) -> None:
        unknown = [g for g in given if g not in names]
        if unknown:
            raise SystemExit(f"Unknown tool(s) for {label}: {', '.join(unknown)}")

    if only:
        _warn_unknown("--only", only)
        for t in cfg.tools:
            t.enabled = t.name in only
    if enable:
        _warn_unknown("--enable", enable)
        for t in cfg.tools:
            if t.name in enable:
                t.enabled = True
    if disable:
        _warn_unknown("--disable", disable)
        for t in cfg.tools:
            if t.name in disable:
                t.enabled = False
    if stages:
        for t in cfg.tools:
            if t.stage not in stages:
                t.enabled = False
    if skip_stages:
        for t in cfg.tools:
            if t.stage in skip_stages:
                t.enabled = False
    if tags_exclude:
        excl = set(tags_exclude)
        for t in cfg.tools:
            if excl & set(t.tags):
                t.enabled = False
