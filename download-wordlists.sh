#!/usr/bin/env bash
# ---------------------------------------------------------------------
# download-wordlists.sh — fetch six2dez/OneListForAll wordlists for recoo
#
# Maps OneListForAll's natural sizes onto recoo's three tiers:
#   micro  -> tiny curated lists (pairs with --profile fast)
#   short  -> balanced *_short category lists + onelistforall.txt (medium)
#   full   -> the big *_long lists (deep) — some are generated locally and
#             not stored in the repo; those are reported if unavailable.
#
# Usage:
#   ./download-wordlists.sh [micro|short|full]  [dest-dir]
#   ./download-wordlists.sh                     # defaults to: short
#
# Files land in  wordlists/OneListForAll/  (matching recoo's defaults);
# resolvers (a separate project) land in  wordlists/resolvers.txt
# ---------------------------------------------------------------------
set -u

GREEN='\033[32m'; YELLOW='\033[33m'; RED='\033[31m'; DIM='\033[2m'; RST='\033[0m'
info() { echo -e "${GREEN}[+]${RST} $*"; }
warn() { echo -e "${YELLOW}[!]${RST} $*"; }
err()  { echo -e "${RED}[x]${RST} $*"; }

TIER="${1:-short}"
DEST="${2:-wordlists/OneListForAll}"
BASE="https://raw.githubusercontent.com/six2dez/OneListForAll/main"
RESOLVERS_URL="https://raw.githubusercontent.com/trickest/resolvers/main/resolvers.txt"

command -v curl >/dev/null 2>&1 || { err "curl is required"; exit 1; }

# fetch <relative-path-in-repo>
fetch() {
  local rel="$1" out="$DEST/$1"
  mkdir -p "$(dirname "$out")"
  if [ -s "$out" ]; then echo -e "${DIM}  · have $rel${RST}"; return 0; fi
  local try
  for try in 1 2 3; do
    if curl -fsSL "$BASE/$rel" -o "$out"; then
      info "  $rel ($(wc -l < "$out" 2>/dev/null || echo '?') lines)"
      return 0
    fi
    sleep $((try * 2))
  done
  warn "  unavailable: $rel"
  rm -f "$out"
  return 1
}

case "$TIER" in
  micro)
    files=(onelistforallmicro.txt
           dict/dns_short.txt
           dict/parameters_short.txt
           dict/permutations_short.txt) ;;
  short)
    files=(onelistforallmicro.txt
           onelistforall.txt
           dict/subdomains_short.txt
           dict/dns_short.txt
           dict/directories_short.txt
           dict/api_short.txt
           dict/parameters_short.txt
           dict/permutations_short.txt) ;;
  full|all)
    files=(onelistforallmicro.txt
           onelistforall.txt
           onelistforall_big.txt
           dict/subdomains_short.txt
           dict/subdomains_long.txt
           dict/dns_short.txt
           dict/directories_short.txt
           dict/parameters_short.txt
           dict/parameters_long.txt
           dict/permutations_short.txt
           dict/permutations_long.txt) ;;
  *)
    err "unknown tier '$TIER' (use: micro | short | full)"; exit 1 ;;
esac

info "fetching OneListForAll [$TIER] -> $DEST"
missing=0
for f in "${files[@]}"; do fetch "$f" || missing=$((missing + 1)); done

if [ "$missing" -gt 0 ] && [ "$TIER" = "full" ]; then
  warn "$missing big *_long file(s) are not stored in the repo (>100MB)."
  warn "Generate them locally with the official tool (needs Go + ~15GB):"
  warn "    git clone https://github.com/six2dez/OneListForAll && \\"
  warn "    cd OneListForAll && go run ./cmd/olfa pipeline"
fi

# resolvers — separate project, required by puredns
mkdir -p wordlists
if [ ! -s wordlists/resolvers.txt ]; then
  info "fetching trickest resolvers -> wordlists/resolvers.txt"
  curl -fsSL "$RESOLVERS_URL" -o wordlists/resolvers.txt \
    && info "  resolvers.txt ($(wc -l < wordlists/resolvers.txt) lines)" \
    || warn "  resolvers fetch failed"
fi

info "done. Verify with:  python3 recoo.py --list-wordlists"
