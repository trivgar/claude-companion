"""The voice lab on an install with no coordinator: it lists what the
voicelab/ pipeline built (voices_dir), as before the comms move, instead of
"comms is not set up"."""
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from claude_companion import server  # noqa: E402

TOKEN = "t" * 32
OWNER = "o" * 64
KEY = {"x-claude-companion-key": OWNER}


@pytest.fixture
def client(tmp_path, monkeypatch):
    voices = tmp_path / "voices"
    monkeypatch.setenv("CLAUDE_COMPANION_VOICES_DIR", str(voices))
    monkeypatch.setenv("CLAUDE_COMPANION_TTS_DIR", str(tmp_path / "no-coordinator"))
    monkeypatch.setattr(server, "FEATURES", ["voicelab"])
    a = voices / "alpha"
    (a / "audition").mkdir(parents=True)
    (a / "dataset").mkdir()
    (a / "dataset" / "x.wav").write_bytes(b"1234")
    (a / "audition" / "one.wav").write_bytes(b"RIFFxxxx")
    b = voices / "beta"
    b.mkdir()
    (b / "voice.json").write_text("{}")
    (b / "model.pth").write_bytes(b"zz")
    (voices / "secret.txt").write_text("nope")
    (tmp_path / "secret.txt").write_text("nope")
    return TestClient(server.create_app(token=TOKEN, owner_key=OWNER))


def test_listing_shows_the_pipelines_voices_and_stages(client):
    assert client.get("/api/voicelab").status_code == 401
    body = client.get("/api/voicelab", headers=KEY).json()
    assert set(body) >= {"now", "needs", "running", "voices", "other", "pipeline"}
    rows = {r["name"]: r for r in body["pipeline"]["voices"]}
    assert rows["alpha"]["stage"] == "prepped" and rows["alpha"]["audition"] == ["one.wav"]
    assert rows["beta"]["stage"] == "served" and rows["beta"]["bytes"] >= 2
    # The coordinator-only sections say why they are empty, not a 503.
    assert body["voices"]["ok"] is False and body["voices"]["why"]


def test_an_empty_install_lists_nothing_and_says_where(client, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_COMPANION_VOICES_DIR", str(tmp_path / "none"))
    body = client.get("/api/voicelab", headers=KEY).json()
    assert body["pipeline"]["voices"] == []
    assert "voicelab/README.md" in body["now"]["text"]


def test_audition_serves_inside_and_refuses_outside(client):
    ok = client.get("/api/voicelab/audition/alpha/one.wav", headers=KEY)
    assert ok.status_code == 200 and ok.content.startswith(b"RIFF")
    assert client.get("/api/voicelab/audition/alpha/one.wav").status_code == 401
    for bad in ("/api/voicelab/audition/alpha/..%2F..%2Fsecret.txt",
                "/api/voicelab/audition/..%2F/secret.txt",
                "/api/voicelab/audition/alpha/%2e%2e/%2e%2e/secret.txt",
                "/api/voicelab/audition/alpha/nothere.wav"):
        assert client.get(bad, headers=KEY).status_code == 404


def test_the_page_lists_pipeline_voices_with_auditions(tmp_path, monkeypatch):
    sync_api = pytest.importorskip("playwright.sync_api")
    import threading
    import time

    import uvicorn
    voices = tmp_path / "voices"
    (voices / "alpha" / "audition").mkdir(parents=True)
    (voices / "alpha" / "audition" / "one.wav").write_bytes(b"RIFFxxxx")
    monkeypatch.setenv("CLAUDE_COMPANION_VOICES_DIR", str(voices))
    monkeypatch.setenv("CLAUDE_COMPANION_TTS_DIR", str(tmp_path / "no-coordinator"))
    monkeypatch.setattr(server, "FEATURES", ["voicelab"])
    srv = uvicorn.Server(uvicorn.Config(server.create_app(token=TOKEN, owner_key=OWNER),
                                        host="127.0.0.1", port=0, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    base = f"http://127.0.0.1:{srv.servers[0].sockets[0].getsockname()[1]}"
    try:
        with sync_api.sync_playwright() as p:
            try:
                b = p.chromium.launch()
            except Exception as exc:  # browser not installed
                pytest.skip(f"chromium unavailable: {exc}")
            pg = b.new_page(viewport={"width": 390, "height": 844})
            pg.add_init_script(f"localStorage.setItem('cc-owner-key', '{OWNER}')")
            pg.goto(f"{base}/?view=voicelab")
            pg.wait_for_function("document.body.innerText.includes('alpha')")
            text = pg.inner_text("body")
            assert "not set up" not in text and "503" not in text
            assert "prepped" in text and "Play 1" in text
            b.close()
    finally:
        srv.should_exit = True
