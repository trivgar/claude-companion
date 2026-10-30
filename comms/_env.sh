# Shared paths for the comms scripts. Every one can be overridden, which is how
# the tests keep away from the real state.
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
TTS_DIR=${TTS_DIR:-$HERE}
DATA=${COMMS_DATA_DIR:-$HOME/.local/share/claude-companion}
STATE=${COMMS_STATE_DIR:-$DATA/comms}
CLIPS=${COMMS_CLIPS_DIR:-$DATA/clips}
VOICES=${PIPER_VOICES:-$DATA/piper/voices}
CAST=${COMMS_CAST:-$TTS_DIR/cast.tsv}
mkdir -p "$STATE" "$CLIPS" 2>/dev/null

# Is a hold in force? The file holds the epoch it ends at, or 0 for no end.
hold_active() {
  local until
  [[ -r "$STATE/hold" ]] || return 1
  until=$(tr -dc '0-9' < "$STATE/hold")
  [[ -z "$until" || "$until" == 0 ]] && return 0
  (( $(date +%s) < until ))
}
