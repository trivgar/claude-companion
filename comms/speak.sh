#!/usr/bin/env bash
# Speak one clip: render it with Piper into the clips directory, where the
# web player picks it up. One pending clip per speaker: a newer clip replaces
# an older one that nobody has played yet. While a hold is on, clips are kept
# and not delivered.
#
#   speak.sh "text"          (or the text on stdin)
#   TTS_SPEAKER=Juno speak.sh "text"
set -u
# shellcheck source=_env.sh
. "$(dirname "${BASH_SOURCE[0]}")/_env.sh"

text="${*:-}"
[[ -z "$text" && ! -t 0 ]] && text=$(cat)
text=$(printf '%s' "$text" | tr '\n\t' '  ' | sed 's/  */ /g; s/^ //; s/ $//')
[[ -z "$text" ]] && exit 0

speaker="${TTS_SPEAKER:-$("$TTS_DIR/identity.sh" get 2>/dev/null)}"
[[ -z "$speaker" ]] && exit 0            # a headless job has no voice
safe=$(printf '%s' "$speaker" | tr -c 'A-Za-z0-9_' '_')

voice=$(awk -F'\t' -v n="$speaker" 'tolower($1)==tolower(n){print $2; exit}' "$CAST")
[[ -z "$voice" ]] && voice=$(awk -F'\t' 'NF>=2 && $2 !~ /^server:/{print $2; exit}' "$CAST")
id="$(date +%s%N)-$safe"
tmp="$CLIPS/.$id.wav.tmp"

# A cast.tsv voice written `server:<name>` is rendered by the voice server
# (voicelab/serve.py) and falls back to the first Piper voice if that fails.
rendered=0
if [[ "$voice" == server:* ]]; then
  port="${COMMS_VOICE_PORT:-9880}"
  tokfile="${XDG_CONFIG_HOME:-$HOME/.config}/claude-companion/voice-token"
  tok=$(tr -d '\r\n' < "$tokfile" 2>/dev/null)
  body=$(VNAME="${voice#server:}" TXT="$text" python3 -c '
import json, os
print(json.dumps({"voice": os.environ["VNAME"], "text": os.environ["TXT"]}))')
  if [[ -n "$tok" ]] && curl -sf --max-time "${COMMS_VOICE_TIMEOUT:-60}" -X POST \
       -H 'Content-Type: application/json' -H "X-Voice-Token: $tok" \
       -d "$body" "http://127.0.0.1:$port/tts" -o "$tmp" 2>/dev/null && [[ -s "$tmp" ]]; then
    rendered=1
  else
    rm -f "$tmp"
    echo "speak: voice server failed for $voice; using the first Piper voice" >&2
    voice=$(awk -F'\t' 'NF>=2 && $2 !~ /^server:/{print $2; exit}' "$CAST")
  fi
fi

if (( ! rendered )); then
  model="$VOICES/$voice.onnx"
  if [[ -z "$voice" || ! -r "$model" ]]; then
    echo "speak: voice $voice is not installed in $VOICES (run comms/install.sh)" >&2
    exit 1
  fi
  piper="${PIPER_BIN:-}"
  if [[ -z "$piper" ]]; then
    if [[ -x "$DATA/piper/piper/piper" ]]; then piper="$DATA/piper/piper/piper"; else piper=$(command -v piper || true); fi
  fi
  if [[ -z "$piper" ]]; then
    echo "speak: piper not found (run comms/install.sh)" >&2
    exit 1
  fi
  if ! printf '%s' "$text" | "$piper" --model "$model" --output_file "$tmp" >/dev/null 2>&1 || [[ ! -s "$tmp" ]]; then
    rm -f "$tmp"
    echo "speak: piper failed to render" >&2
    exit 1
  fi
fi

held=0; hold_active && held=1
# The page's volume slider writes $STATE/volume.conf ("volume=0.8"); default full.
vol=$(sed -n 's/^volume=\([0-9.]*\)$/\1/p' "$STATE/volume.conf" 2>/dev/null | head -1)
[[ "$vol" =~ ^(0|1|0?\.[0-9]+|1\.0+)$ ]] || vol=1
(
  exec 9>"$STATE/queue.lock"; flock 9
  # A newer clip replaces this speaker's older one that was never played.
  for old in "$CLIPS"/*-"$safe".json; do
    [[ -e "$old" ]] || continue
    base="${old%.json}"
    [[ -e "$base.played" ]] && continue
    rm -f "$base.json" "$base.wav"
  done
  mv "$tmp" "$CLIPS/$id.wav"
  SPK="$speaker" TXT="$text" AT="$(date +%s)" VOL="$vol" python3 -c '
import json, os
print(json.dumps({"speaker": os.environ["SPK"], "text": os.environ["TXT"], "at": int(os.environ["AT"]),
                  "volume": float(os.environ["VOL"])}))
' > "$CLIPS/.$id.json.tmp"
  mv "$CLIPS/.$id.json.tmp" "$CLIPS/$id.json"
  # Day-old files go.
  find "$CLIPS" -maxdepth 1 -type f -mtime +0 -delete 2>/dev/null
)
if (( held )); then
  echo "$(date '+%F %T') HELD $speaker $id" >> "$STATE/speak.log"
  exit 0
fi
echo "$(date '+%F %T') SPOKE $speaker $id" >> "$STATE/speak.log"

if [[ "${COMMS_LOCAL_PLAY:-0}" == 1 ]]; then
  player=$(command -v paplay || command -v aplay || true)
  if [[ -n "$player" ]]; then
    ( "$player" "$CLIPS/$id.wav" >/dev/null 2>&1; : > "$CLIPS/$id.played" ) </dev/null >/dev/null 2>&1 &
    disown
  fi
fi
exit 0
