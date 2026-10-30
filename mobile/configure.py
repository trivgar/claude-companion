"""Write the local Android app configuration from claude-companion settings."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from claude_companion import config


def _slug(value: str) -> str:
    return "".join(char for char in value.lower() if "a" <= char <= "z" or char.isdigit())


def user_agent(app_name: str) -> str:
    """The tag the page looks for to know it is inside the app: the name's
    letters and digits, then "App" ("Hilltop" -> "HilltopApp")."""
    return "".join(c for c in app_name if c.isascii() and c.isalnum()) + "App"


def _write_properties(path: Path, app_id: str, app_name: str) -> None:
    path.write_text(f"applicationId={app_id}\nappName={app_name}\n")


def configure(out: Path) -> int:
    settings = {key: config.get(key) for key in ("app_name", "url", "firebase_app_file")}
    missing = [key for key, value in settings.items() if not value]
    if missing:
        print("Missing settings: " + ", ".join(missing), file=sys.stderr)
        return 1

    app_name = str(settings["app_name"])
    app_id = config.get("app_id") or "app.companion." + _slug(app_name)
    firebase = Path(str(settings["firebase_app_file"]))
    if not firebase.is_file():
        print("firebase_app_file does not name a file", file=sys.stderr)
        return 1

    url = str(settings["url"])
    if not url.endswith("/"):
        url += "/"
    out.mkdir(parents=True, exist_ok=True)
    (out / "capacitor.config.json").write_text(json.dumps({
        "appId": app_id,
        "appName": app_name,
        "webDir": "www",
        "server": {"url": url, "androidScheme": "https"},
        "android": {"appendUserAgent": user_agent(app_name)},
    }, indent=2) + "\n")
    app_dir = out / "android" / "app"
    app_dir.mkdir(parents=True, exist_ok=True)
    _write_properties(app_dir / "app.properties", str(app_id), app_name)
    shutil.copyfile(firebase, app_dir / "google-services.json")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent)
    return configure(parser.parse_args().out)


if __name__ == "__main__":
    raise SystemExit(main())
