#!/usr/bin/env bash
# Pause delivery without losing clips.
#
#   quiet.sh hold [minutes]   clips are kept, not played (no minutes: until resume)
#   quiet.sh resume           deliver what was kept
#   quiet.sh held             hold state and the clips waiting
set -u
# shellcheck source=_env.sh
. "$(dirname "${BASH_SOURCE[0]}")/_env.sh"

case "${1:-}" in
  hold)
    if [[ "${2:-}" =~ ^[0-9]+$ && "$2" -gt 0 ]]; then echo $(( $(date +%s) + $2 * 60 )) > "$STATE/hold"
    else echo 0 > "$STATE/hold"; fi
    echo "held" ;;
  resume)
    rm -f "$STATE/hold"; echo "resumed" ;;
  held)
    if hold_active; then echo "hold: on"; else echo "hold: off"; fi
    for j in "$CLIPS"/*.json; do
      [[ -e "$j" && ! -e "${j%.json}.played" ]] || continue
      python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print("%s\t%s" % (d["speaker"], d["text"]))' "$j"
    done ;;
  *) echo "usage: quiet.sh hold [minutes] | resume | held" >&2; exit 2 ;;
esac
