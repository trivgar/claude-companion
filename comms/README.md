# Comms

Your Claude sessions talk. A reply ends with one line that starts with the
speaker glyph (U+1F50A) and is under about 40 words; the Stop hook speaks it
with Piper, and the dashboard's Comms tab plays it on your phone while the page
is open.

## Install

    comms/install.sh --dry-run     # shows every step first
    comms/install.sh

It downloads Piper and the four voices in `cast.tsv` (official rhasspy
releases) into `~/.local/share/agent-companion/piper`, registers the MCP
server (`claude mcp add tts --scope user`), and adds the Stop and persona hooks
to `~/.claude/settings.json` after a timestamped backup. Open a new session
afterwards.

## Parts

- `speak.sh "text"`: renders a clip into `~/.local/share/agent-companion/clips/`
  (`<id>.wav` plus a `<id>.json` sidecar with speaker, text and time). One pending
  clip per speaker: a newer one replaces an older one nobody has played.
  `COMMS_LOCAL_PLAY=1` also plays it on this machine with paplay or aplay
  (and marks it played, so the phone page skips it).
- `identity.sh set|free|get|cast|roster`: a session's speaking name, stored by
  its session key. `get` claims the first free name. Voices come from `cast.tsv`.
- `quiet.sh hold [minutes]|resume|held`: while held, clips are kept and not
  delivered.
- `stop_hook.py`: the Stop hook. Silent and exit 0 on any failure.
- `persona_hook.py` and `personas/*.md`: how each speaker talks, handed to the
  session as context. Text only.
- `mcp/server.py`: a stdlib-only MCP server with one tool, `text_to_speech`,
  for something the owner must hear mid-turn. Ending a turn with the glyph line
  costs nothing extra; a tool call does.
- Server side: `agent_companion/server/comms.py` (`GET /api/comms/next`,
  `GET /api/comms/clip/{id}`, `POST /api/comms/played`) and
  `static/comms.js`.

## Paths

Every state path can be overridden: `COMMS_DATA_DIR`, `COMMS_STATE_DIR`,
`COMMS_CLIPS_DIR`, `PIPER_VOICES`, `PIPER_BIN`, `COMMS_CAST`, `TTS_DIR`. The
tests set all of them.

## Voices

A `cast.tsv` row whose second column is `server:<name>` is rendered by the
voice lab server instead (`voicelab/serve.py`, default port 9880, override
with `COMMS_VOICE_PORT`): `speak.sh` POSTs `{voice, text}` to
`http://127.0.0.1:<port>/tts` with the token from
`~/.config/agent-companion/voice-token`. If the server is down or refuses, it
falls back to the first Piper voice in the file, so keep at least one Piper row.

Only the Piper voices in `cast.tsv` ship. Add a row (name, tab, Piper voice id
from the official voice list) to add a speaker, then run `install.sh` to fetch it.

## Volume

The Settings page writes `volume=0.8` (0 to 1) to `comms/volume.conf` in the
comms state directory. `speak.sh` reads it, defaults to full, and puts it in the
clip's `.json`; the Comms page plays the clip at that gain.
