#!/usr/bin/env python3
"""Comms MCP server: one tool, text_to_speech, which runs speak.sh.

Stdlib only, one file. Transport is MCP stdio: newline-delimited JSON-RPC 2.0.
Nothing but JSON-RPC may go to stdout; diagnostics go to the log.
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SPEAK = os.environ.get("COMMS_SPEAK") or os.path.join(os.path.dirname(HERE), "speak.sh")
STATE = os.environ.get("COMMS_STATE_DIR") or os.path.expanduser("~/.local/share/claude-companion/comms")
LOG = os.path.join(STATE, "mcp.log")

SERVER_NAME = "tts"
SERVER_VERSION = "1.0.0"
FALLBACK_PROTOCOL = "2025-06-18"
SPEAK_TIMEOUT = 180   # speak.sh renders; bound it so a hang is an error, not silence


def log(msg):
    try:
        os.makedirs(STATE, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


TOOL = {
    "name": "text_to_speech",
    "description": (
        "Speak a short clip aloud on the owner's phone.\n\n"
        "Keep it under ~40 words. Clips play one at a time across every live "
        "session, so a long clip is silence for everyone else.\n\n"
        "Your speaking name and voice come from identity.sh, not from this call."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "What to say. Under ~40 words."}},
        "required": ["text"],
    },
}


def speak(text):
    if not os.path.exists(SPEAK):
        return False, "speak.sh not found at %s" % SPEAK
    try:
        proc = subprocess.run(["bash", SPEAK, text], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=SPEAK_TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, "speak.sh timed out after %ss" % SPEAK_TIMEOUT
    except Exception as exc:
        return False, "could not run speak.sh: %s" % exc
    if proc.returncode == 0:
        return True, ""
    err = (proc.stderr or b"").decode("utf-8", "replace").strip()
    out = (proc.stdout or b"").decode("utf-8", "replace").strip()
    return False, (err or out or "exit %d with no output" % proc.returncode).split("\n")[0][:300]


def call_tool(params):
    name = params.get("name")
    args = params.get("arguments") or {}
    if name != "text_to_speech":
        return {"content": [{"type": "text", "text": "Unknown tool: %s" % name}], "isError": True}
    text = args.get("text")
    if not isinstance(text, str) or not text.strip():
        return {"content": [{"type": "text", "text": "text is required"}], "isError": True}
    ok, detail = speak(text)
    shown = text if len(text) <= 50 else text[:50] + "..."
    if ok:
        log("SPOKE :: %s" % shown)
        return {"content": [{"type": "text", "text": "Spoke: %s" % shown}]}
    log("SPEAK-FAIL %s :: %s" % (detail, shown))
    return {"content": [{"type": "text", "text": "TTS failed: %s" % detail}], "isError": True}


def handle(msg):
    """A response dict, or None for notifications."""
    method = msg.get("method")
    mid = msg.get("id")
    if method == "initialize":
        version = (msg.get("params") or {}).get("protocolVersion") or FALLBACK_PROTOCOL
        result = {"protocolVersion": version, "capabilities": {"tools": {}},
                  "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}}
    elif method == "tools/list":
        result = {"tools": [TOOL]}
    elif method == "tools/call":
        result = call_tool(msg.get("params") or {})
    elif method == "ping":
        result = {}
    elif mid is None:
        return None
    else:
        return {"jsonrpc": "2.0", "id": mid,
                "error": {"code": -32601, "message": "Method not found: %s" % method}}
    if mid is None:
        return None
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception as exc:
            log("bad json: %s" % exc)
            continue
        try:
            reply = handle(msg)
        except Exception as exc:
            log("handler error: %s" % exc)
            if msg.get("id") is None:
                continue
            reply = {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32603, "message": str(exc)}}
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
