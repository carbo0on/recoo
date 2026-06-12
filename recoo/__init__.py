"""recoo — modular, config-driven reconnaissance automation framework.

recoo turns the "Advanced Reconnaissance Methodology" into a single,
controllable pipeline. You feed it one or more domains and it walks the
recon phases (seed discovery -> subdomain enum -> resolve -> probe ->
crawl -> JS analysis -> params -> APIs -> cloud/OSINT -> triage), wiring
the output of each phase into the next.

The defining feature: every individual tool can be switched on or off,
from the config file or the command line, so you decide exactly which
parts of the pipeline run on any given target.
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
