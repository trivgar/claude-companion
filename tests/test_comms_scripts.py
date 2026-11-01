"""comms/*.sh and the hooks, in a temp dir with a fake piper. Never the real state."""
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

COMMS = Path(__file__).resolve().parent.parent / "comms"
FAKE_PIPER = """#!/bin/sh
out=""
while [ $# -gt 0 ]; do case "$1" in --output_file) out="$2"; shift;; esac; shift; done
cat > "$out"
"""


@pytest.fixture
def env(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    p = bin_ / "piper"
    p.write_text(FAKE_PIPER)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    voices = tmp_path / "voices"
    voices.mkdir()
    for line in (COMMS / "cast.tsv").read_text().splitlines():
        (voices / f"{line.split(chr(9))[1]}.onnx").write_text("x")
    e = dict(os.environ)
    e.update(HOME=str(tmp_path / "home"), PATH=f"{bin_}:{os.environ['PATH']}",
             COMMS_STATE_DIR=str(tmp_path / "state"), COMMS_CLIPS_DIR=str(tmp_path / "clips"),
             PIPER_VOICES=str(voices), TTS_SESSION_KEY="k1", TTS_SPEAKER="Juno")
    for k in ("PIPER_BIN", "COMMS_LOCAL_PLAY", "COMMS_JOB", "AGENT_COMPANION_JOB", "TTS_DIR"):
        e.pop(k, None)
    e["tmp"] = str(tmp_path)
    return e


def run(env, *args, **kw):
    return subprocess.run(list(args), env=env, capture_output=True, text=True, timeout=30, **kw)


def clips(env):
    return sorted(Path(env["COMMS_CLIPS_DIR"]).glob("*.json"))


def test_speak_renders_a_clip_with_a_sidecar(env):
    r = run(env, "bash", str(COMMS / "speak.sh"), "Hello there")
    assert r.returncode == 0, r.stderr
    (j,) = clips(env)
    meta = json.loads(j.read_text())
    assert meta["speaker"] == "Juno" and meta["text"] == "Hello there" and meta["at"] > 0
    assert j.with_suffix(".wav").read_text() == "Hello there"


def test_a_newer_clip_replaces_the_same_speakers_older_one_only(env):
    run(env, "bash", str(COMMS / "speak.sh"), "first")
    run({**env, "TTS_SPEAKER": "Wren"}, "bash", str(COMMS / "speak.sh"), "other speaker")
    run(env, "bash", str(COMMS / "speak.sh"), "second")
    texts = sorted(json.loads(j.read_text())["text"] for j in clips(env))
    assert texts == ["other speaker", "second"]
    assert len(list(Path(env["COMMS_CLIPS_DIR"]).glob("*.wav"))) == 2


def test_a_played_clip_is_not_replaced(env):
    run(env, "bash", str(COMMS / "speak.sh"), "first")
    (j,) = clips(env)
    j.with_suffix(".played").touch()
    run(env, "bash", str(COMMS / "speak.sh"), "second")
    assert len(clips(env)) == 2


def test_hold_keeps_clips_and_resume_lists_them(env):
    q = str(COMMS / "quiet.sh")
    run(env, "bash", q, "hold")
    assert "hold: on" in run(env, "bash", q, "held").stdout
    run(env, "bash", str(COMMS / "speak.sh"), "kept while held")
    assert len(clips(env)) == 1
    out = run(env, "bash", q, "held").stdout
    assert "Juno\tkept while held" in out
    assert "HELD" in (Path(env["COMMS_STATE_DIR"]) / "speak.log").read_text()
    run(env, "bash", q, "resume")
    assert "hold: off" in run(env, "bash", q, "held").stdout
    assert len(clips(env)) == 1


def test_a_timed_hold_ends(env):
    state = Path(env["COMMS_STATE_DIR"])
    run(env, "bash", str(COMMS / "quiet.sh"), "hold", "5")
    assert int((state / "hold").read_text()) > 0
    (state / "hold").write_text("1\n")           # long past
    assert "hold: off" in run(env, "bash", str(COMMS / "quiet.sh"), "held").stdout


def test_speak_says_when_piper_or_the_voice_is_missing(env):
    (Path(env["tmp"]) / "bin" / "piper").unlink()
    env = {**env, "PATH": "/usr/bin:/bin"}
    r = run(env, "bash", str(COMMS / "speak.sh"), "hi")
    assert r.returncode == 1 and "piper not found" in r.stderr


def test_local_play_runs_the_player_when_asked(env):
    log = Path(env["tmp"]) / "played.log"
    pa = Path(env["tmp"]) / "bin" / "paplay"
    pa.write_text(f'#!/bin/sh\necho "$1" >> "{log}"\n')
    pa.chmod(0o755)
    run({**env, "COMMS_LOCAL_PLAY": "1"}, "bash", str(COMMS / "speak.sh"), "out loud")
    import time
    for _ in range(50):
        if log.exists():
            break
        time.sleep(0.1)
    assert log.read_text().strip().endswith(".wav")
    run(env, "bash", str(COMMS / "speak.sh"), "silent")
    time.sleep(0.3)
    assert len(log.read_text().splitlines()) == 1


def test_identity_set_free_cast_roster(env):
    i = str(COMMS / "identity.sh")
    assert run(env, "bash", i, "set", "wren").stdout.strip() == "Wren"
    assert run(env, "bash", i, "get").stdout.strip() == "Wren"
    cast = dict((r.split("\t")[0], r.split("\t")[2]) for r in run(env, "bash", i, "cast").stdout.splitlines())
    assert cast["Wren"] == "taken" and cast["Juno"] == "free"
    assert run({**env, "TTS_SESSION_KEY": "k2"}, "bash", i, "set", "Wren").returncode == 1
    assert "Wren" in run(env, "bash", i, "roster").stdout
    run(env, "bash", i, "free")
    assert run({**env, "TTS_SESSION_KEY": "k2"}, "bash", i, "set", "Wren").returncode == 0
    assert run(env, "bash", i, "set", "Nobody").returncode == 1


def test_get_claims_the_first_free_name_and_jobs_get_none(env):
    i = str(COMMS / "identity.sh")
    assert run(env, "bash", i, "get").stdout.strip() == "Juno"
    assert run({**env, "TTS_SESSION_KEY": "k2"}, "bash", i, "get").stdout.strip() == "Delia"
    assert run({**env, "TTS_SESSION_KEY": "k3", "AGENT_COMPANION_JOB": "1"}, "bash", i, "get").stdout == ""


def test_speak_uses_the_session_name_when_no_speaker_is_given(env):
    env.pop("TTS_SPEAKER")
    run(env, "bash", str(COMMS / "identity.sh"), "set", "Vance")
    run(env, "bash", str(COMMS / "speak.sh"), "dispatch")
    assert json.loads(clips(env)[0].read_text())["speaker"] == "Vance"


def _stop(env, msg, fake):
    e = {**env, "COMMS_SPEAK": str(fake)}
    return subprocess.run([sys.executable, str(COMMS / "stop_hook.py")], input=json.dumps(
        {"last_assistant_message": msg}), env=e, capture_output=True, text=True, timeout=30)


def test_stop_hook_speaks_the_last_glyph_line_capped(env):
    out = Path(env["tmp"]) / "said.txt"
    fake = Path(env["tmp"]) / "fake_speak.sh"
    fake.write_text(f'printf "%s" "$1" > "{out}"\n')
    long = " ".join(f"w{i}" for i in range(60))
    r = _stop(env, f"Done.\n\n\U0001F50A early one\n\nmore\n\n\U0001F50A **{long}**", fake)
    assert r.returncode == 0
    said = out.read_text()
    assert said.startswith("w0 w1") and said.endswith("...") and len(said.split()) == 40
    out.unlink()
    assert _stop(env, "No clip line here.", fake).returncode == 0 and not out.exists()


def test_stop_hook_is_silent_on_garbage_and_a_failing_speaker(env):
    fake = Path(env["tmp"]) / "boom.sh"
    fake.write_text("exit 3\n")
    r = subprocess.run([sys.executable, str(COMMS / "stop_hook.py")], input="not json",
                       env=env, capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout == ""
    assert _stop(env, "\U0001F50A hello", fake).returncode == 0


def test_mcp_server_lists_and_calls_the_tool(env):
    out = Path(env["tmp"]) / "said.txt"
    fake = Path(env["tmp"]) / "fake_speak.sh"
    fake.write_text(f'printf "%s" "$1" > "{out}"\n')
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "text_to_speech", "arguments": {"text": "hello mcp"}}}]
    r = subprocess.run([sys.executable, str(COMMS / "mcp" / "server.py")], env={**env, "COMMS_SPEAK": str(fake)},
                       input="\n".join(json.dumps(m) for m in msgs) + "\n", capture_output=True, text=True, timeout=30)
    replies = [json.loads(line) for line in r.stdout.splitlines()]
    assert [x["id"] for x in replies] == [1, 2, 3]
    assert replies[1]["result"]["tools"][0]["name"] == "text_to_speech"
    assert "Spoke: hello mcp" in replies[2]["result"]["content"][0]["text"]
    assert out.read_text() == "hello mcp"


def test_persona_hook_hands_over_the_persona_once(env):
    e = {**env, "TTS_DIR": str(COMMS)}
    run(e, "bash", str(COMMS / "identity.sh"), "set", "Delia")

    def hook(event):
        return subprocess.run([sys.executable, str(COMMS / "persona_hook.py")], env=e, capture_output=True, text=True,
                              input=json.dumps({"hook_event_name": event, "session_id": "s1"})).stdout
    first = json.loads(hook("SessionStart"))["hookSpecificOutput"]["additionalContext"]
    assert "Delia" in first and "the owner" in first
    assert hook("UserPromptSubmit") == ""


def test_install_dry_run_changes_nothing(env):
    settings = Path(env["tmp"]) / "settings.json"
    r = run({**env, "CLAUDE_SETTINGS": str(settings), "COMMS_DATA_DIR": str(Path(env["tmp"]) / "data")},
            "bash", str(COMMS / "install.sh"), "--dry-run")
    assert r.returncode == 0 and "would:" in r.stdout and "mcp add tts --scope user" in r.stdout
    assert "verify its SHA-256" in r.stdout
    assert "rhasspy/piper-voices" in r.stdout
    assert not settings.exists() and not (Path(env["tmp"]) / "data").exists()


def test_install_hooks_backs_up_and_is_idempotent(env):
    settings = Path(env["tmp"]) / "settings.json"
    settings.write_text(json.dumps({"theme": "dark", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo hi"}]}]}}))
    e = {**env, "CLAUDE_SETTINGS": str(settings)}
    for _ in range(2):
        r = run(e, "bash", str(COMMS / "install.sh"), "--skip-download", "--skip-mcp")
        assert r.returncode == 0, r.stderr
    cfg = json.loads(settings.read_text())
    cmds = [h["command"] for g in cfg["hooks"]["Stop"] for h in g["hooks"]]
    assert cmds.count(f"python3 {COMMS}/stop_hook.py") == 1 and "echo hi" in cmds and cfg["theme"] == "dark"
    assert len(list(Path(env["tmp"]).glob("settings.json.bak-*"))) == 2


def test_install_pins_a_real_piper_sha256():
    m = re.search(r'^PIPER_SHA256="([0-9a-f]*)"$', (COMMS / "install.sh").read_text(), re.M)
    assert m and len(m.group(1)) == 64


def test_install_refuses_a_piper_download_that_fails_its_sha256(env):
    data = Path(env["tmp"]) / "data"
    curl = Path(env["tmp"]) / "bin" / "curl"
    # A tampered archive: curl "succeeds" with bytes that are not the pinned asset.
    curl.write_text('#!/bin/sh\nwhile [ $# -gt 0 ]; do [ "$1" = -o ] && { echo tampered > "$2"; exit 0; }; shift; done\nexit 1\n')
    curl.chmod(0o755)
    (Path(env["tmp"]) / "bin" / "piper").unlink()
    r = run({**env, "COMMS_DATA_DIR": str(data)}, "bash", str(COMMS / "install.sh"),
            "--skip-mcp", "--skip-hooks")
    assert r.returncode == 1
    assert "SHA-256 verification failed" in r.stderr
    assert not (data / "piper" / "piper").exists()


def test_install_uninstall_removes_only_its_hooks(env):
    settings = Path(env["tmp"]) / "settings.json"
    settings.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": "echo hi"}]}]}}))
    e = {**env, "CLAUDE_SETTINGS": str(settings)}
    assert run(e, "bash", str(COMMS / "install.sh"), "--skip-download", "--skip-mcp").returncode == 0
    assert run(e, "bash", str(COMMS / "install.sh"), "--uninstall", "--skip-mcp").returncode == 0
    cfg = json.loads(settings.read_text())
    assert cfg == {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo hi"}]}]}}


def _voice_server(tmp_path, status=200):
    """A tiny HTTP server on a free port that records the request and answers with fake audio."""
    import http.server
    import threading
    seen = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            seen.update(path=self.path, token=self.headers.get("X-Voice-Token"),
                        body=json.loads(self.rfile.read(n)))
            self.send_response(status)
            self.send_header("Content-Type", "audio/wav")
            self.end_headers()
            if status == 200:
                self.wfile.write(b"SERVER-AUDIO")

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, seen


def _server_env(env, tmp_path, port):
    cast = tmp_path / "cast.tsv"
    cast.write_text("Juno\tserver:myvoice\nWren\ten_GB-alba-medium\n")
    cfg = tmp_path / "xdg" / "agent-companion"
    cfg.mkdir(parents=True)
    (cfg / "voice-token").write_text("tok123\n")
    return {**env, "COMMS_CAST": str(cast), "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
            "COMMS_VOICE_PORT": str(port)}


def test_a_server_voice_is_rendered_by_the_voice_server(env, tmp_path):
    srv, seen = _voice_server(tmp_path)
    try:
        e = _server_env(env, tmp_path, srv.server_address[1])
        r = run(e, "bash", str(COMMS / "speak.sh"), "Hello there")
    finally:
        srv.shutdown()
    assert r.returncode == 0, r.stderr
    assert seen["path"] == "/tts" and seen["token"] == "tok123"
    assert seen["body"] == {"voice": "myvoice", "text": "Hello there"}
    (j,) = clips(env)
    assert j.with_suffix(".wav").read_text() == "SERVER-AUDIO"


def test_a_failing_voice_server_falls_back_to_the_first_piper_voice(env, tmp_path):
    srv, seen = _voice_server(tmp_path, status=500)
    try:
        e = _server_env(env, tmp_path, srv.server_address[1])
        r = run(e, "bash", str(COMMS / "speak.sh"), "Hello there")
    finally:
        srv.shutdown()
    assert r.returncode == 0, r.stderr
    (j,) = clips(env)
    assert j.with_suffix(".wav").read_text() == "Hello there"   # the fake piper echoes the text


def test_a_dead_voice_server_falls_back_too(env, tmp_path):
    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    e = _server_env(env, tmp_path, port)
    r = run(e, "bash", str(COMMS / "speak.sh"), "Hello there")
    assert r.returncode == 0, r.stderr
    (j,) = clips(env)
    assert j.with_suffix(".wav").read_text() == "Hello there"
