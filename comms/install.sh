#!/usr/bin/env bash
# Set up comms on this machine.
#
#   install.sh [--dry-run] [--skip-download] [--skip-mcp] [--skip-hooks] [--uninstall]
#
# 1. Downloads Piper and the cast's voices (comms/cast.tsv) from the official
#    rhasspy releases into ~/.local/share/agent-companion/piper.
# 2. Registers the MCP server:  claude mcp add tts --scope user -- python3 <here>/mcp/server.py
# 3. Adds the Stop hook (and the persona hook) to ~/.claude/settings.json,
#    after a timestamped backup.
# Every step is safe to repeat. It prints what it did, or with --dry-run what it would do.
set -u
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
DATA=${COMMS_DATA_DIR:-$HOME/.local/share/agent-companion}
SETTINGS=${CLAUDE_SETTINGS:-$HOME/.claude/settings.json}
PIPER_VERSION=2023.11.14-2
PIPER_ASSET=piper_linux_x86_64.tar.gz
PIPER_URL=https://github.com/rhasspy/piper/releases/download/$PIPER_VERSION/$PIPER_ASSET
# SHA-256 of $PIPER_ASSET at $PIPER_VERSION. The release publishes no checksum,
# so this was hashed from GitHub's download on 2026-10-30; its 26,460,462 bytes
# match the release's asset record. A changed asset fails here, not at run time.
PIPER_SHA256="a50cb45f355b7af1f6d758c1b360717877ba0a398cc8cbe6d2a7a3a26e225992"
VOICE_BASE=https://huggingface.co/rhasspy/piper-voices/resolve/main
dry=0; dl=1; mcp=1; hooks=1; uninstall=0
for a in "$@"; do
  case "$a" in
    --dry-run) dry=1 ;; --skip-download) dl=0 ;; --skip-mcp) mcp=0 ;; --skip-hooks) hooks=0 ;;
    --uninstall) uninstall=1; dl=0 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done
do_() { if (( dry )); then echo "would: $*"; else echo "doing: $*"; "$@"; fi; }

echo "Piper and voices -> $DATA/piper"
if (( dl )); then
  if [[ -x "$DATA/piper/piper/piper" ]]; then echo "  piper already installed"
  else
    if (( dry )); then
      echo "would: download $PIPER_URL, verify its SHA-256, and unpack into $DATA/piper"
    elif [[ -z "$PIPER_SHA256" ]]; then
      echo "Piper download refused: PIPER_SHA256 is empty. Fill it with the SHA-256 for $PIPER_ASSET from the official Piper $PIPER_VERSION release page." >&2
      exit 1
    elif ! command -v sha256sum >/dev/null; then
      echo "Piper download refused: sha256sum is required to verify $PIPER_ASSET." >&2
      exit 1
    else
      mkdir -p "$DATA/piper/voices" || exit 1
      archive=$(mktemp "$DATA/piper/.piper-archive.XXXXXX") || exit 1
      echo "doing: download $PIPER_URL"
      if ! curl -fsSL "$PIPER_URL" -o "$archive"; then
        echo "  piper download failed" >&2
        rm -f "$archive"
        exit 1
      fi
      if ! printf '%s  %s\n' "$PIPER_SHA256" "$archive" | sha256sum -c -; then
        echo "  piper SHA-256 verification failed; archive was not extracted" >&2
        rm -f "$archive"
        exit 1
      fi
      if ! tar -xzf "$archive" -C "$DATA/piper"; then
        echo "  piper archive could not be extracted" >&2
        rm -f "$archive"
        exit 1
      fi
      rm -f "$archive"
    fi
  fi
  while IFS=$'\t' read -r name id; do
    [[ -z "$id" || "$id" == server:* ]] && continue   # server voices come from voicelab
    family=${id%%_*}; lang=${id%%-*}; rest=${id#*-}; vname=${rest%-*}; quality=${rest##*-}
    for ext in onnx onnx.json; do
      f="$DATA/piper/voices/$id.$ext"
      if [[ -s "$f" ]]; then echo "  $name: $id.$ext present"; continue; fi
      url="$VOICE_BASE/$family/$lang/$vname/$quality/$id.$ext"
      if (( dry )); then echo "would: download $url"
      else mkdir -p "$DATA/piper/voices"; echo "doing: download $id.$ext"; curl -fsSL "$url" -o "$f" || { echo "  failed: $url" >&2; rm -f "$f"; }; fi
    done
  done < "$HERE/cast.tsv"
else echo "  skipped"; fi

echo "MCP server"
if (( mcp )); then
  if command -v claude >/dev/null; then
    if (( uninstall )); then do_ claude mcp remove tts --scope user
    else do_ claude mcp add tts --scope user -- python3 "$HERE/mcp/server.py"; fi
  elif (( uninstall )); then echo "  claude not on PATH; remove by hand: claude mcp remove tts --scope user"
  else echo "  claude not on PATH; register by hand: claude mcp add tts --scope user -- python3 $HERE/mcp/server.py"; fi
else echo "  skipped"; fi

echo "Hooks -> $SETTINGS"
if (( hooks )); then
  if (( dry )); then
    if (( uninstall )); then
      echo "would: back up $SETTINGS, then remove this install's Stop, SessionStart, and UserPromptSubmit hooks"
    else
      echo "would: back up $SETTINGS, then add a Stop hook (python3 $HERE/stop_hook.py) and SessionStart and UserPromptSubmit persona hooks (python3 $HERE/persona_hook.py)"
    fi
  else
    mkdir -p "$(dirname "$SETTINGS")"
    if [[ -e "$SETTINGS" ]]; then b="$SETTINGS.bak-$(date +%Y%m%d%H%M%S)-$$"; cp -p "$SETTINGS" "$b"; echo "  backup: $b"; fi
    SETTINGS="$SETTINGS" HERE="$HERE" UNINSTALL="$uninstall" python3 - <<'PY'
import json, os
path, here = os.environ["SETTINGS"], os.environ["HERE"]
uninstall = os.environ["UNINSTALL"] == "1"
try:
    with open(path) as fh:
        cfg = json.load(fh)
except FileNotFoundError:
    cfg = {}
if not isinstance(cfg, dict):
    raise SystemExit("settings.json is not an object; left alone")
hooks = cfg.setdefault("hooks", {})
want = {"Stop": f"python3 {here}/stop_hook.py",
        "SessionStart": f"python3 {here}/persona_hook.py",
        "UserPromptSubmit": f"python3 {here}/persona_hook.py"}
for event, cmd in want.items():
    groups = hooks.get(event, [])
    if uninstall:
        kept = []
        removed = False
        for group in groups:
            if not isinstance(group, dict):
                kept.append(group)
                continue
            entries = group.get("hooks", [])
            remaining = [entry for entry in entries if not isinstance(entry, dict) or entry.get("command") != cmd]
            removed = removed or len(remaining) != len(entries)
            if remaining:
                kept.append({**group, "hooks": remaining})
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event, None)
        print(f"  {event}: {'removed' if removed else 'already absent'}")
        continue
    groups = hooks.setdefault(event, [])
    if any(isinstance(h, dict) and h.get("command") == cmd
           for g in groups if isinstance(g, dict) for h in g.get("hooks", [])):
        print(f"  {event}: already there")
        continue
    groups.append({"hooks": [{"type": "command", "command": cmd}]})
    print(f"  {event}: added")
if not hooks:
    cfg.pop("hooks", None)
tmp = path + ".tmp"
with open(tmp, "w") as fh:
    json.dump(cfg, fh, indent=2)
os.replace(tmp, path)
PY
  fi
else echo "  skipped"; fi
if (( dry )); then
  echo "Dry run: nothing changed."
elif (( uninstall )); then
  echo "Done. Removed this install's Comms hooks and user-scope MCP registration."
else
  echo "Done. Open a new Claude Code session; end replies with a line starting with the speaker glyph."
fi
