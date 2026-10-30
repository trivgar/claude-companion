import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(tmp_path, text, firebase=True):
    cfg = tmp_path / "c.toml"
    fb = tmp_path / "google-services.json"
    if firebase:
        fb.write_text('{"project_info": {"project_id": "x"}}')
        text += f'\nfirebase_app_file = "{fb}"\n'
    cfg.write_text(text)
    out = tmp_path / "out"
    return subprocess.run([sys.executable, str(ROOT / "mobile" / "configure.py"), "--out", str(out)],
                          env={"CLAUDE_COMPANION_CONFIG": str(cfg), "PATH": "/usr/bin:/bin"},
                          capture_output=True, text=True), out


def test_it_writes_the_app_from_settings(tmp_path):
    r, out = _run(tmp_path, 'app_name = "Hilltop"\nurl = "https://pc.example.com"\n')
    assert r.returncode == 0, r.stderr
    cap = json.loads((out / "capacitor.config.json").read_text())
    assert cap["appName"] == "Hilltop" and cap["appId"] == "app.companion.hilltop"
    assert cap["server"]["url"] == "https://pc.example.com/"
    assert cap["android"]["appendUserAgent"] == "HilltopApp"
    props = (out / "android" / "app" / "app.properties").read_text()
    assert "applicationId=app.companion.hilltop" in props and "appName=Hilltop" in props
    assert (out / "android" / "app" / "google-services.json").exists()


def test_an_app_id_setting_wins(tmp_path):
    r, out = _run(tmp_path, 'app_name = "X"\nurl = "https://a.example"\napp_id = "com.example.mine"\n')
    assert json.loads((out / "capacitor.config.json").read_text())["appId"] == "com.example.mine"


def test_missing_settings_are_named(tmp_path):
    r, _ = _run(tmp_path, "", firebase=False)
    assert r.returncode == 1
    for k in ("app_name", "url", "firebase_app_file"):
        assert k in r.stderr
