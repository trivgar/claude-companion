#!/usr/bin/env python3
"""A small voice server over trained GPT-SoVITS v2 voices.

    GPT_SOVITS_DIR=~/GPT-SoVITS python3 serve.py [--port 9880] [--bind 127.0.0.1]
    python3 serve.py --list

    curl -s -X POST localhost:9880/tts -H 'Content-Type: application/json' \\
         -H "X-Voice-Token: $(cat ~/.config/claude-companion/voice-token)" \\
         -d '{"voice": "myvoice", "text": "Hello there."}' -o out.wav

Voices live in ~/.local/share/claude-companion/voices/<name>/ (override with
--voices-dir or CLAUDE_COMPANION_VOICES_DIR). Each has a voice.json:

    {"gpt": "weights/gpt/x.ckpt", "sovits": "weights/sovits/x.pth",
     "ref_audio": "ref.wav", "ref_text": "what the reference clip says", "lang": "en"}

Paths are relative to the voice's folder. Binds to loopback by default.
Every request needs the header X-Voice-Token, the contents of
~/.config/claude-companion/voice-token (mode 600, made on first run; callers
read the same file). POSTs must be application/json and the Host header must be
127.0.0.1:<port> or localhost:<port>, so a web page cannot reach it by DNS
rebinding or a cross-site form.
"""
from __future__ import annotations

import argparse
import io
import json
import hmac
import os
import secrets
import sys
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAX_TEXT = 1000
LANGS = {"en": "en", "zh": "zh", "ja": "ja", "ko": "ko", "yue": "yue"}


def default_dir() -> Path:
    return Path(os.environ.get("CLAUDE_COMPANION_VOICES_DIR") or Path.home() / ".local/share/claude-companion/voices")


def token_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "claude-companion" / "voice-token"


def load_or_create_token(path: Path | None = None) -> str:
    path = path or token_path()
    try:
        tok = path.read_text().strip()
        if tok:
            return tok
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tok = secrets.token_urlsafe(24)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(tok + "\n")
    return tok


def list_voices(root: Path) -> list[dict]:
    out = []
    for d in sorted(Path(root).iterdir()) if Path(root).is_dir() else []:
        meta = d / "voice.json"
        if not meta.is_file():
            continue
        try:
            info = json.loads(meta.read_text())
        except ValueError:
            continue
        out.append({"name": d.name, "dir": str(d), **{k: info.get(k) for k in ("gpt", "sovits", "ref_audio", "ref_text", "lang")}})
    return out


class Engine:
    """Upstream's TTS pipeline, loaded on first use, one voice at a time."""

    def __init__(self, gsv_dir: Path):
        self.dir, self.lock, self.tts, self.loaded = gsv_dir, threading.Lock(), None, None

    def _load(self, voice: dict) -> None:
        if self.tts is None:
            sys.path[:0] = [str(self.dir), str(self.dir / "GPT_SoVITS")]
            os.chdir(self.dir)
            from GPT_SoVITS.TTS_infer_pack.TTS import TTS, TTS_Config
            cfg = TTS_Config("GPT_SoVITS/configs/tts_infer.yaml")
            cfg.version = "v2"
            self.tts = TTS(cfg)
        if self.loaded != voice["name"]:
            base = Path(voice["dir"])
            self.tts.init_t2s_weights(str(base / voice["gpt"]))
            self.tts.init_vits_weights(str(base / voice["sovits"]))
            self.loaded = voice["name"]

    def speak(self, voice: dict, text: str) -> bytes:
        import numpy as np
        lang = LANGS.get((voice.get("lang") or "en").lower(), "en")
        with self.lock:
            self._load(voice)
            chunks, rate = [], 32000
            for rate, audio in self.tts.run({
                "text": text, "text_lang": lang,
                "ref_audio_path": str(Path(voice["dir"]) / voice["ref_audio"]),
                "prompt_text": voice.get("ref_text") or "", "prompt_lang": lang,
                "text_split_method": "cut5", "streaming_mode": False,
            }):
                chunks.append(np.asarray(audio))
        pcm = np.concatenate(chunks) if chunks else np.zeros(1, dtype="int16")
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm.astype("int16").tobytes())
        return buf.getvalue()


def make_handler(root: Path, speak, token: str):
    if not token:
        raise ValueError("a voice token is required")

    class H(BaseHTTPRequestHandler):
        def _allowed(self) -> bool:
            """Host, then token. Answers the refusal itself and returns False."""
            port = self.server.server_address[1]
            host = (self.headers.get("Host") or "").strip().lower()
            if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
                self._send(403, b'{"error":"bad host"}')
                return False
            given = (self.headers.get("X-Voice-Token") or "").encode()
            if not hmac.compare_digest(given, token.encode()):
                self._send(401, b'{"error":"missing or invalid token"}')
                return False
            return True

        def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self._allowed():
                return
            if self.path.split("?")[0] == "/voices":
                self._send(200, json.dumps([v["name"] for v in list_voices(root)]).encode())
            else:
                self._send(404, b'{"error":"not found"}')

        def do_POST(self):
            if not self._allowed():
                return
            if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
                return self._send(415, b'{"error":"send application/json"}')
            if self.path.split("?")[0] != "/tts":
                return self._send(404, b'{"error":"not found"}')
            try:
                n = int(self.headers.get("Content-Length") or 0)
                req = json.loads(self.rfile.read(min(n, 65536)) or b"{}")
                name, text = str(req["voice"]), str(req["text"]).strip()
            except (KeyError, ValueError):
                return self._send(400, b'{"error":"send JSON {voice, text}"}')
            voice = next((v for v in list_voices(root) if v["name"] == name), None)
            if voice is None:
                return self._send(404, b'{"error":"no such voice"}')
            if not text or len(text) > MAX_TEXT:
                return self._send(400, b'{"error":"text empty or too long"}')
            try:
                self._send(200, speak(voice, text), "audio/wav")
            except Exception as exc:  # the engine's failures are the caller's to read
                self._send(500, json.dumps({"error": f"{type(exc).__name__}: {exc}"}).encode())

        def log_message(self, fmt, *args):
            sys.stderr.write("serve: " + fmt % args + "\n")
    return H


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--list", action="store_true", help="print the voices found and exit")
    ap.add_argument("--voices-dir", type=Path, default=None)
    ap.add_argument("--port", type=int, default=9880)
    ap.add_argument("--bind", default="127.0.0.1")
    a = ap.parse_args(argv)
    root = a.voices_dir or default_dir()
    if a.list:
        found = list_voices(root)
        for v in found:
            print(f"{v['name']}\t{v['lang'] or '-'}\t{v['dir']}")
        if not found:
            print(f"no voices under {root}", file=sys.stderr)
        return 0
    gsv = os.environ.get("GPT_SOVITS_DIR")
    if not gsv:
        print("GPT_SOVITS_DIR is required to serve", file=sys.stderr)
        return 2
    engine = Engine(Path(gsv).expanduser().resolve())
    srv = ThreadingHTTPServer((a.bind, a.port), make_handler(root, engine.speak, load_or_create_token()))
    print(f"voices on http://{a.bind}:{a.port}/tts  ({len(list_voices(root))} found)", flush=True)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
