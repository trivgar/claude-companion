import json
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from claude_companion import server  # noqa: E402
from claude_companion.server import auth  # noqa: E402

TOKEN = "t" * 32
OWNER = "o" * 64
KEY = {"x-claude-companion-key": OWNER}
SAME = {"origin": "http://testserver", "host": "testserver"}
W = {**KEY, **SAME}
COMMS = Path(__file__).resolve().parent.parent / "comms"

FAKE = """#!/bin/sh
echo "$(basename "$0") $*" >> "$FAKE_LOG"
echo "speaker=${TTS_SPEAKER:-}" >> "$FAKE_LOG"
"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    tts = tmp_path / "tts"
    tts.mkdir()
    for n in ("quiet.sh", "speak.sh"):
        (tts / n).write_text(FAKE.replace("$(basename \"$0\")", n))
        (tts / n).chmod(0o755)
    (tts / "cast.tsv").write_text("Juno\ten_US-amy-medium\nWren\ten_GB-alba-medium\n")
    (tmp_path / "state").mkdir()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("COMMS_DATA_DIR", raising=False)
    monkeypatch.delenv("PIPER_BIN", raising=False)
    from claude_companion.server import settings as settings_mod
    monkeypatch.setattr(settings_mod, "_which", lambda name: None)
    monkeypatch.setenv("CLAUDE_COMPANION_TTS_DIR", str(tts))
    monkeypatch.setenv("COMMS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("COMMS_CLIPS_DIR", str(tmp_path / "clips"))
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "log"))
    cfgfile = tmp_path / "config.toml"
    cfgfile.write_text('name = "x"\n')
    monkeypatch.setenv("CLAUDE_COMPANION_CONFIG", str(cfgfile))
    monkeypatch.setenv("CLAUDE_COMPANION_OWNER_KEYS", str(tmp_path / "owner-keys.json"))
    monkeypatch.setattr(server, "FEATURES", ["settings", "comms"])
    c = TestClient(server.create_app(token=TOKEN, owner_key=OWNER))
    c.tmp, c.cfg = tmp_path, cfgfile
    c.log = lambda: (tmp_path / "log").read_text() if (tmp_path / "log").exists() else ""
    return c


def install_piper(client):
    piper = client.tmp / "home" / ".local" / "share" / "claude-companion" / "piper" / "piper" / "piper"
    piper.parent.mkdir(parents=True)
    piper.write_text("#!/bin/sh\n")
    piper.chmod(0o755)


def test_reads_need_the_owner_key(client):
    assert client.get("/api/settings").status_code == 401
    assert client.get("/api/settings", headers={"x-claude-companion-token": TOKEN}).status_code == 401
    assert client.get("/api/settings", headers=KEY).status_code == 200


def test_the_feature_is_listed_with_its_page(client):
    assert {"id": "settings", "title": "Settings"} in client.get("/api/features").json()
    assert client.get("/static/settings.js").status_code == 200


@pytest.mark.parametrize("path,body", [
    ("/api/settings/comms/hold", {}), ("/api/settings/comms/resume", {}),
    ("/api/settings/comms/volume", {"volume": 0.5}), ("/api/settings/comms/test", {"name": "Juno"}),
    ("/api/settings/devices/unpair", {"name": "x"}),
])
def test_writes_need_key_json_and_origin(client, path, body):
    assert client.post(path, json=body, headers=SAME).status_code == 401
    assert client.post(path, content=json.dumps(body), headers={**W, "content-type": "text/plain"}).status_code == 415
    assert client.post(path, json=body, headers=KEY).status_code == 403
    assert client.post(path, json=body, headers={**KEY, "origin": "http://evil.example", "host": "testserver"}).status_code == 403


def test_the_read_shape(client):
    s = client.get("/api/settings", headers=KEY).json()
    assert s["version"] and s["config_path"] == str(client.cfg)
    assert s["comms"]["cast"] == {"Juno": "en_US-amy-medium", "Wren": "en_GB-alba-medium"}
    assert s["comms"]["installed"] is False
    assert s["comms"]["held"] is False and s["comms"]["volume"] == 1.0
    assert [f["id"] for f in s["features"]] == ["settings", "comms"] or {f["id"] for f in s["features"]} >= {"settings", "comms"}
    on = {f["id"]: f for f in s["features"]}
    assert on["settings"]["enabled"] and not on["mail"]["enabled"]
    assert on["mail"]["line"].startswith('features = "') and "mail" in on["mail"]["line"]
    assert s["collab"] == {"label": None, "from": None, "repo": None}


def test_collab_shows_what_is_set(client):
    client.cfg.write_text('collab_label = "for-alex"\ncollab_repo = "example/app"\n')
    assert client.get("/api/settings", headers=KEY).json()["collab"] == {
        "label": "for-alex", "from": None, "repo": "example/app"}


def test_devices_have_name_and_created_never_a_hash(client):
    auth.add_device("phone", "k" * 64)
    d = client.get("/api/settings", headers=KEY).json()["devices"]
    assert d[0]["name"] == "phone" and d[0]["created"] and "hash" not in json.dumps(d)


def test_hold_and_resume_call_quiet_sh(client):
    assert client.post("/api/settings/comms/hold", json={}, headers=W).status_code == 200
    assert client.post("/api/settings/comms/hold", json={"minutes": 30}, headers=W).status_code == 200
    assert client.post("/api/settings/comms/resume", json={}, headers=W).status_code == 200
    lines = [l for l in client.log().splitlines() if l.startswith("quiet.sh")]
    assert lines == ["quiet.sh hold", "quiet.sh hold 30", "quiet.sh resume"]
    assert client.post("/api/settings/comms/hold", json={"minutes": -5}, headers=W).status_code == 400


def test_a_failing_script_is_reported(client):
    (client.tmp / "tts" / "quiet.sh").write_text("#!/bin/sh\necho boom >&2\nexit 3\n")
    r = client.post("/api/settings/comms/resume", json={}, headers=W)
    assert r.status_code == 500 and "boom" in r.json()["detail"]


def test_test_clip_speaks_as_a_cast_name_only(client):
    install_piper(client)
    assert client.post("/api/settings/comms/test", json={"name": "Wren"}, headers=W).status_code == 200
    assert "speak.sh Comms test" in client.log() and "speaker=Wren" in client.log()
    assert client.post("/api/settings/comms/test", json={"name": "Nobody"}, headers=W).status_code == 400


def test_comms_test_requires_an_installed_piper(client):
    assert client.get("/api/settings", headers=KEY).json()["comms"]["installed"] is False
    r = client.post("/api/settings/comms/test", json={"name": "Juno"}, headers=W)
    assert r.status_code == 409
    assert r.json()["detail"] == "Comms is not installed yet: INSTALL.md step 10."

    install_piper(client)
    assert client.get("/api/settings", headers=KEY).json()["comms"]["installed"] is True


def test_volume_persists_and_is_read_back(client):
    assert client.post("/api/settings/comms/volume", json={"volume": 0.4}, headers=W).status_code == 200
    assert (client.tmp / "state" / "volume.conf").read_text() == "volume=0.4\n"
    assert client.get("/api/settings", headers=KEY).json()["comms"]["volume"] == 0.4
    assert client.post("/api/settings/comms/volume", json={"volume": 1.5}, headers=W).status_code == 400
    assert client.post("/api/settings/comms/volume", json={"volume": -1}, headers=W).status_code == 400


def test_unpair_by_name_and_self_needs_confirm(client):
    auth.add_device("laptop", "l" * 64)
    auth.add_device("phone", "p" * 64)
    me = {"x-claude-companion-key": "p" * 64}
    assert client.post("/api/settings/devices/unpair", json={"name": "laptop"}, headers={**me, **SAME}).status_code == 200
    assert client.post("/api/settings/devices/unpair", json={"name": "laptop"}, headers={**me, **SAME}).status_code == 404
    r = client.post("/api/settings/devices/unpair", json={"name": "phone"}, headers={**me, **SAME})
    assert r.status_code == 409 and [d["name"] for d in auth.devices()] == ["phone"]
    r = client.post("/api/settings/devices/unpair", json={"name": "phone", "confirm": True}, headers={**me, **SAME})
    assert r.status_code == 200 and auth.devices() == []


def test_the_page_marks_this_device(client):
    auth.add_device("phone", "p" * 64)
    d = client.get("/api/settings", headers={"x-claude-companion-key": "p" * 64}).json()["devices"]
    assert d[0]["this_device"] is True


def test_no_route_writes_config_toml(client):
    before = client.cfg.read_text()
    for path, body in [("/api/settings/comms/hold", {}), ("/api/settings/comms/volume", {"volume": 0.3}),
                       ("/api/settings/comms/test", {"name": "Juno"}), ("/api/settings/devices/unpair", {"name": "z"})]:
        client.post(path, json=body, headers=W)
    for bad in ("/api/settings", "/api/settings/config"):
        for m in ("put", "patch", "post"):
            assert getattr(client, m)(bad, json={"name": "evil"}, headers=W).status_code in (404, 405)
    assert client.cfg.read_text() == before
    src = (Path(server.__file__).parent / "settings.py").read_text()
    assert not re.search(r"config\.path\(\)\.(write|open)|tomli_w|write_text\(.*config", src)


# --- speak.sh reads the volume ---

def test_speak_sh_writes_the_volume_into_the_clip(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "piper").write_text('#!/bin/sh\nout=""\nwhile [ $# -gt 0 ]; do case "$1" in --output_file) out="$2"; shift;; esac; shift; done\ncat > "$out"\n')
    (bin_ / "piper").chmod(0o755)
    voices = tmp_path / "voices"
    voices.mkdir()
    for line in (COMMS / "cast.tsv").read_text().splitlines():
        (voices / f"{line.split(chr(9))[1]}.onnx").write_text("x")
    env = {**os.environ, "HOME": str(tmp_path / "h"), "PATH": f"{bin_}:{os.environ['PATH']}",
           "COMMS_STATE_DIR": str(tmp_path / "state"), "COMMS_CLIPS_DIR": str(tmp_path / "clips"),
           "PIPER_VOICES": str(voices), "TTS_SPEAKER": "Juno"}
    for k in ("PIPER_BIN", "TTS_DIR", "COMMS_CAST"):
        env.pop(k, None)

    def clip_volume():
        subprocess.run(["bash", str(COMMS / "speak.sh"), "hi"], env=env, check=True, capture_output=True, timeout=30)
        (j,) = (tmp_path / "clips").glob("*.json")
        v = json.loads(j.read_text())["volume"]
        j.unlink()
        j.with_suffix(".wav").unlink()
        return v
    assert clip_volume() == 1.0                      # no file: full
    (tmp_path / "state" / "volume.conf").write_text("volume=0.35\n")
    assert clip_volume() == 0.35
    (tmp_path / "state" / "volume.conf").write_text("volume=loud\n")
    assert clip_volume() == 1.0                      # junk: full
    (tmp_path / "state" / "volume.conf").write_text("volume=7\n")
    assert clip_volume() == 1.0
