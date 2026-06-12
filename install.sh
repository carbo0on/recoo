#!/usr/bin/env bash
# ---------------------------------------------------------------------
# recoo — install the external recon toolchain.
#
# recoo itself only needs Python 3.8+ and PyYAML. The actual recon work
# is done by well-known community binaries. This script installs the
# common ones; recoo gracefully skips any tool that is not present, so
# you can install only what you need.
#
# Usage:  ./install.sh            # install everything we can
#         ./install.sh --go       # only the Go (ProjectDiscovery etc.) tools
#         ./install.sh --python   # only the pip-based tools
# ---------------------------------------------------------------------
set -u

GREEN='\033[32m'; YELLOW='\033[33m'; RED='\033[31m'; DIM='\033[2m'; RST='\033[0m'
info() { echo -e "${GREEN}[+]${RST} $*"; }
warn() { echo -e "${YELLOW}[!]${RST} $*"; }
err()  { echo -e "${RED}[x]${RST} $*"; }

WHAT="${1:-all}"

# --- recoo's own dependency ------------------------------------------
install_python_deps() {
  info "installing recoo python dependency (PyYAML)"
  pip3 install --quiet --user -r "$(dirname "$0")/requirements.txt" \
    || pip install --quiet --user pyyaml || warn "pip failed; install pyyaml manually"
}

# --- Go-based tools (ProjectDiscovery + friends) ---------------------
GO_TOOLS=(
  "github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest"
  "github.com/projectdiscovery/httpx/cmd/httpx@latest"
  "github.com/projectdiscovery/dnsx/cmd/dnsx@latest"
  "github.com/projectdiscovery/naabu/v2/cmd/naabu@latest"
  "github.com/projectdiscovery/katana/cmd/katana@latest"
  "github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest"
  "github.com/projectdiscovery/asnmap/cmd/asnmap@latest"
  "github.com/projectdiscovery/chaos-client/cmd/chaos@latest"
  "github.com/projectdiscovery/notify/cmd/notify@latest"
  "github.com/lc/gau/v2/cmd/gau@latest"
  "github.com/tomnomnom/waybackurls@latest"
  "github.com/tomnomnom/anew@latest"
  "github.com/tomnomnom/gf@latest"
  "github.com/Emoe/kxss@latest"
  "github.com/PentestPad/subzy@latest"
  "github.com/ffuf/ffuf/v2@latest"
  "github.com/jaeles-project/gospider@latest"
  "github.com/BishopFox/jsluice/cmd/jsluice@latest"
  "github.com/d3mondev/puredns/v2@latest"
  "github.com/Josue87/gotator@latest"
  "github.com/tomnomnom/assetfinder@latest"
  "github.com/sensepost/gowitness@latest"
  "github.com/hakluke/hakrawler@latest"
  "github.com/hahwul/dalfox/v2@latest"
  "github.com/tomnomnom/qsreplace@latest"
  "github.com/003random/getJS/v2@latest"
)

install_go_tools() {
  if ! command -v go >/dev/null 2>&1; then
    err "Go is not installed — skipping Go tools. Get it: https://go.dev/dl/"
    return
  fi
  for t in "${GO_TOOLS[@]}"; do
    name="$(basename "${t%@*}")"
    if command -v "$name" >/dev/null 2>&1; then
      echo -e "${DIM}  · $name already present${RST}"; continue
    fi
    info "go install $name"
    GO111MODULE=on go install -v "$t" >/dev/null 2>&1 \
      && info "  installed $name" || warn "  failed $name"
  done
  warn "ensure \$(go env GOPATH)/bin is on your PATH"
}

# --- pip-based tools -------------------------------------------------
PIP_TOOLS=(arjun uro)
install_pip_tools() {
  for t in "${PIP_TOOLS[@]}"; do
    info "pip install $t"
    pip3 install --quiet --user "$t" || warn "  failed $t"
  done
  warn "LinkFinder, cloud_enum, graphw00f, trufflehog, paramspider, findomain,"
  warn "s3scanner, aquatone, x8, mantra: install from their own repos"
}

case "$WHAT" in
  --go)     install_go_tools ;;
  --python) install_python_deps; install_pip_tools ;;
  all)      install_python_deps; install_go_tools; install_pip_tools ;;
  *)        err "unknown option: $WHAT (use: all | --go | --python)"; exit 1 ;;
esac

info "done. Verify what recoo can see:  ./recoo.py --list-tools"
