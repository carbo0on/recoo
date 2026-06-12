"""The Tool model: a single declarative step in the pipeline.

Every recon tool (subfinder, httpx, katana, ...) is described as data, not
code. That is what makes the pipeline fully controllable: enabling,
disabling, filtering and reordering tools is just reading/editing these
records — no Python changes required.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


# Canonical pipeline artifacts. A tool reads from one (``input``) and/or
# feeds its results into one (``produces``). The engine maps these names
# to concrete files inside the workspace and merges results automatically.
ARTIFACTS = {
    "seeds":       "seeds/apex_domains.txt",
    "cidrs":       "seeds/cidrs.txt",
    "subs_all":    "subs/all.txt",
    "resolved":    "subs/resolved.txt",
    "live":        "hosts/live.txt",
    "ports":       "hosts/ports.txt",
    "urls_all":    "urls/all.txt",
    "urls_clean":  "urls/clean.txt",
    "with_params": "urls/with_params.txt",
    "js_urls":     "js/js_urls.txt",
    "params_all":  "params/all.txt",
}


@dataclass
class Tool:
    """One pipeline step.

    Attributes
    ----------
    name      : unique identifier, used for --enable/--disable/--only.
    stage     : which phase this belongs to (ordered by the engine).
    desc      : human description shown in listings/logs.
    bins      : external binaries that must be on PATH for this to run.
    cmd       : shell command template (placeholders like {input}, {item}).
    builtin   : name of an internal handler instead of a shell command.
    mode      : 'list'  -> run once, pass the input file as {input}
                'each'  -> run once per line of the input file ({item})
                'none'  -> run once, no input file required.
    input     : canonical artifact this tool consumes (or None).
    output    : workspace-relative path for this tool's raw output.
    produces  : canonical artifact to merge this tool's output into.
    stdout    : True -> capture stdout into ``output``;
                False -> the command writes ``output`` itself.
    enabled   : master on/off switch (overridable from the CLI).
    timeout   : per-run timeout in seconds (0 = inherit global default).
    tags      : free-form labels (e.g. 'slow', 'needs-key', 'noisy').
    """

    name: str
    stage: str
    desc: str = ""
    bins: List[str] = field(default_factory=list)
    cmd: str = ""
    builtin: Optional[str] = None
    mode: str = "list"
    input: Optional[str] = None
    output: Optional[str] = None
    produces: Optional[str] = None
    stdout: bool = True
    enabled: bool = True
    timeout: int = 0
    tags: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, name: str, d: dict) -> "Tool":
        return cls(
            name=name,
            stage=d["stage"],
            desc=d.get("desc", ""),
            bins=_as_list(d.get("bins") or d.get("bin")),
            cmd=d.get("cmd", ""),
            builtin=d.get("builtin"),
            mode=d.get("mode", "list"),
            input=d.get("input"),
            output=d.get("output"),
            produces=d.get("produces"),
            stdout=bool(d.get("stdout", True)),
            enabled=bool(d.get("enabled", True)),
            timeout=int(d.get("timeout", 0)),
            tags=_as_list(d.get("tags")),
        )


def _as_list(value) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return list(value)
