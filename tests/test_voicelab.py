import importlib.util
import json
import math
import shutil
import struct
import subprocess
import sys
from urllib.parse import quote
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VL = ROOT / "voicelab"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"vl_{name}", VL / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _wav(path, parts, rate=16000):
    """parts: list of (seconds, amplitude); amplitude 0 is silence."""
    frames = bytearray()
    for secs, amp in parts:
        for i in range(int(secs * rate)):
            v = int(amp * 32767 * math.sin(2 * math.pi * 440 * i / rate)) if amp else 0
            frames += struct.pack("<h", v)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg missing")
def test_prep_cuts_on_silence_and_writes_a_list(tmp_path):
    prep = _load("prep")
    src, out = tmp_path / "in", tmp_path / "out"
    src.mkdir()
    _wav(src / "a.wav", [(3, 0.5), (1, 0), (3, 0.5)])
    result = prep.prep(src, out, "testvoice", lang="en", transcribe=False)
    pieces = sorted((out / "wavs").glob("*.wav"))
    assert len(pieces) == 2
    for p in pieces:
        with wave.open(str(p)) as w:
            assert w.getframerate() == 32000 and w.getnchannels() == 1
            assert 2.5 <= w.getnframes() / 32000 <= 12
    lines = (out / "testvoice.list").read_text().splitlines()
    assert len(lines) == 2
    for ln in lines:
        path, speaker, lang, text = ln.split("|")
        assert speaker == "testvoice" and lang == "EN" and text == ""
        assert Path(path).exists()
    assert result["pieces"] == 2 and result["transcribed"] is False


def test_split_plan_respects_bounds():
    prep = _load("prep")
    # silences at 4-5s inside a 20 s file: pieces within 3..12 s
    plan = prep.plan_pieces(20.0, [(4.0, 5.0), (9.5, 10.0)])
    assert plan and all(3 <= b - a <= 12 for a, b in plan)


def test_serve_list_finds_voices(tmp_path, capsys):
    serve = _load("serve")
    v = tmp_path / "alpha"
    v.mkdir()
    (v / "voice.json").write_text(json.dumps({"gpt": "a.ckpt", "sovits": "a.pth", "ref_audio": "ref.wav", "ref_text": "hi", "lang": "en"}))
    (tmp_path / "junk").mkdir()
    assert [x["name"] for x in serve.list_voices(tmp_path)] == ["alpha"]
    assert serve.main(["--list", "--voices-dir", str(tmp_path)]) == 0
    assert "alpha" in capsys.readouterr().out


def test_train_dry_run_prints_and_runs_nothing(tmp_path):
    env = {"PATH": "/usr/bin:/bin", "GPT_SOVITS_DIR": str(tmp_path / "gsv"), "HOME": str(tmp_path)}
    r = subprocess.run(["bash", str(VL / "train.sh"), "--dry-run", "myvoice", str(tmp_path / "ds")],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert "myvoice" in r.stdout
    assert "s1_train" in r.stdout and "s2_train" in r.stdout
    assert not (tmp_path / "gsv").exists() and not (tmp_path / ".local").exists()


def test_train_needs_gpt_sovits_dir(tmp_path):
    r = subprocess.run(["bash", str(VL / "train.sh"), "--dry-run", "v", str(tmp_path)],
                       capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    assert r.returncode != 0 and "GPT_SOVITS_DIR" in r.stderr


# routes
fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from agent_companion import server  # noqa: E402

TOKEN = "t" * 32
OWNER = "o" * 64
KEY = {"x-agent-companion-key": OWNER}


@pytest.fixture
def client(tmp_path, monkeypatch):
    tts = tmp_path / "tts"
    tts.mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "audio-effects.cfg").write_text("Speaker|gain 8||0.7\n")
    (tts / "voicelab").mkdir()
    (tts / "voicelab" / "voicelab.py").write_text("STAGES = ['prepare', 'audition']\n")
    (tts / "remote-voices.conf").write_text("Speaker=speaker\n")
    lab = tmp_path / "lab"
    build = lab / "speaker-build"
    (build / "audition").mkdir(parents=True)
    (build / "voice.json").write_text('{"name": "Speaker"}')
    (build / ".done-prepare").write_text("done")
    (build / "audition" / "new-1.mp3").write_bytes(b"ID3x")
    audition = tts / "auditions" / "Trial"
    audition.mkdir(parents=True)
    (audition / "one.mp3").write_bytes(b"ID3x")
    clips = tmp_path / "clips"
    clips.mkdir()
    tracks = tmp_path / "audio" / "tracks"
    tracks.mkdir(parents=True)
    (tracks / "track.mp3").write_bytes(b"ID3x")
    (tts / "station.conf").write_text("music=track.mp3\n")
    outbox = tts / "outbox"
    outbox.mkdir()
    (outbox / "1.json").write_text('{"seq": 1, "name": "Speaker", "at": 1}')
    (outbox / "1.opus").write_bytes(b"OggS")
    monkeypatch.setenv("AGENT_COMPANION_TTS_DIR", str(tts))
    monkeypatch.setenv("AGENT_COMPANION_VOICELAB_DIR", str(lab))
    monkeypatch.setenv("COMMS_CLIPS_DIR", str(clips))
    monkeypatch.setattr(server, "FEATURES", ["comms", "voicelab"])
    (tmp_path / "secret.txt").write_text("nope")
    c = TestClient(server.create_app(token=TOKEN, owner_key=OWNER))
    c.tmp_path, c.tts, c.lab, c.clips, c.tracks = tmp_path, tts, lab, clips, tracks
    return c


def test_listing_needs_token_and_shows_the_full_sections(client):
    assert client.get("/api/voicelab").status_code == 401
    body = client.get("/api/voicelab", headers=KEY).json()
    assert set(body) == {"now", "needs", "running", "voices", "other"}
    assert body["voices"]["voices"][0]["name"] == "Speaker"
    assert body["running"]["builds"]
    assert body["needs"]["items"][0]["kind"] == "audition"


def test_audition_and_build_clips_stay_inside_their_roots(client):
    ok = client.get("/api/voices/auditions/Trial/one.mp3", headers=KEY)
    assert ok.status_code == 200 and ok.content.startswith(b"ID3")
    assert client.get("/api/voices/auditions/Trial/one.mp3").status_code == 401
    assert client.get("/api/voicelab/clip/speaker-build/new-1.mp3", headers=KEY).status_code == 200
    for bad in ("/api/voices/auditions/Trial/..%2F..%2Fsecret.txt",
                "/api/voices/auditions/..%2F/secret.txt",
                "/api/voicelab/clip/speaker-build/result.json",
                "/api/voicelab/clip/..%2Fspeaker-build/new-1.mp3"):
        assert client.get(bad, headers=KEY).status_code == 404


def test_file_routes_refuse_links_and_path_escapes(client):
    outside = client.tmp_path / "secret.txt"
    file_build = client.lab / "file-link"
    (file_build / "audition").mkdir(parents=True)
    (file_build / "voice.json").write_text("{}")
    (file_build / "audition" / "new-1.mp3").symlink_to(outside)
    folder_build = client.lab / "folder-link"
    folder_build.mkdir()
    (folder_build / "voice.json").write_text("{}")
    (folder_build / "audition").symlink_to(client.tmp_path, target_is_directory=True)
    (client.clips / "100-Speaker.wav").symlink_to(outside)
    (client.tts / "outbox" / "1.opus").unlink()
    (client.tts / "outbox" / "1.opus").symlink_to(outside)
    (client.tracks / "track-link.mp3").symlink_to(outside)
    (client.tracks / "bed-link.mp3").symlink_to(outside)
    (client.tts / "station.conf").write_text("music=bed-link.mp3\n")
    (client.tts / "auditions" / "Trial" / "link.mp3").symlink_to(outside)

    escaped = quote(str(outside), safe="")
    for path in (
        "/api/voicelab/clip/file-link/new-1.mp3",
        "/api/voicelab/clip/folder-link/secret.txt",
        "/api/comms/clip/100-Speaker",
        "/api/tts/clip/1?who=web",
        "/api/tts/track/track-link.mp3",
        "/api/tts/bed",
        "/api/voices/auditions/Trial/link.mp3",
        "/api/voicelab/clip/..%2Fspeaker-build/new-1.mp3",
        f"/api/tts/track/{escaped}",
        "/api/tts/track/track%2Flink.mp3",
    ):
        assert client.get(path, headers=KEY).status_code == 404


# ---- the voice server's walls ----

@pytest.fixture
def voice_server(tmp_path):
    import http.client
    import threading
    from http.server import ThreadingHTTPServer
    serve = _load("serve")
    v = tmp_path / "alpha"
    v.mkdir()
    (v / "voice.json").write_text(json.dumps({"gpt": "a", "sovits": "b", "ref_audio": "r.wav"}))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), serve.make_handler(tmp_path, lambda voice, text: b"RIFFwav", "sekret"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]

    def call(method, path, body=None, headers=None, host=True):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.putrequest(method, path, skip_host=True)
        if host is True:
            c.putheader("Host", f"127.0.0.1:{port}")
        elif host:
            c.putheader("Host", host)
        for k, val in (headers or {}).items():
            c.putheader(k, val)
        data = json.dumps(body).encode() if body is not None else b""
        c.putheader("Content-Length", str(len(data)))
        c.endheaders(data)
        r = c.getresponse()
        out = r.status, r.read()
        c.close()
        return out
    yield call, port
    srv.shutdown()
    srv.server_close()


OK = {"X-Voice-Token": "sekret", "Content-Type": "application/json"}


def test_voice_server_happy_path(voice_server):
    call, port = voice_server
    assert call("GET", "/voices", headers={"X-Voice-Token": "sekret"})[0] == 200
    assert call("POST", "/tts", {"voice": "alpha", "text": "hi"}, OK) == (200, b"RIFFwav")
    assert call("POST", "/tts", {"voice": "alpha", "text": "hi"}, OK, host=f"localhost:{port}")[0] == 200


def test_voice_server_needs_the_token(voice_server):
    call, _ = voice_server
    assert call("GET", "/voices")[0] == 401
    assert call("GET", "/voices", headers={"X-Voice-Token": "wrong"})[0] == 401
    assert call("POST", "/tts", {"voice": "alpha", "text": "hi"}, {"Content-Type": "application/json"})[0] == 401


def test_voice_server_needs_json(voice_server):
    call, _ = voice_server
    for ct in ("text/plain", "application/x-www-form-urlencoded", None):
        h = {"X-Voice-Token": "sekret"}
        if ct:
            h["Content-Type"] = ct
        assert call("POST", "/tts", {"voice": "alpha", "text": "hi"}, h)[0] == 415


def test_voice_server_refuses_a_foreign_host(voice_server):
    call, port = voice_server
    for host in ("evil.example.com", f"evil.example.com:{port}", "127.0.0.1", f"127.0.0.1:{port + 1}", None):
        assert call("GET", "/voices", headers={"X-Voice-Token": "sekret"}, host=host)[0] == 403
        assert call("POST", "/tts", {"voice": "alpha", "text": "hi"}, OK, host=host)[0] == 403


def test_voice_token_file_is_made_600_and_reused(tmp_path):
    serve = _load("serve")
    p = tmp_path / "cfg" / "voice-token"
    t = serve.load_or_create_token(p)
    assert t and oct(p.stat().st_mode & 0o777) == "0o600"
    assert serve.load_or_create_token(p) == t
