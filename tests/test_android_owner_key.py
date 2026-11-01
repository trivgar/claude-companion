"""The Android app's native calls carry the paired owner key (issue #1).

Every app route is an owner route (require_token), and its writes also want
JSON and the page's own origin (require_write). The native side runs with no
page open, so it keeps its own copy of the key the page was paired with.
"""
import re
from pathlib import Path

from agent_companion.server import auth

ROOT = Path(__file__).resolve().parent.parent
JAVA = ROOT / "mobile/android/app/src/main/java/app/companion"
APP = (JAVA / "CompanionApp.java").read_text()
MAIN = (JAVA / "MainActivity.java").read_text()


def test_the_app_sends_the_header_the_server_reads():
    assert f'OWNER_HEADER = "{auth.OWNER_HEADER}"' in APP


def test_it_copies_the_key_from_where_pairing_keeps_it():
    stored = re.search(r"localStorage\.setItem\('([^']+)'", (ROOT / "agent_companion/server/static/pair.js").read_text())
    assert stored and f"localStorage.getItem('{stored.group(1)}')" in MAIN
    assert "CompanionApp.setOwnerKey(" in MAIN


def test_every_native_request_is_opened_where_the_key_goes_on():
    opened = re.search(r"static HttpURLConnection open\(.*?\n    \}", APP, re.S).group(0)
    assert "setRequestProperty(OWNER_HEADER" in opened
    post = re.search(r"static int post\(.*?\n    \}", APP, re.S).group(0)
    assert "open(c, path" in post and '"Origin", base(c)' in post and "application/json" in post
    for f in JAVA.glob("*.java"):
        if f.name != "CompanionApp.java":
            assert "openConnection(" not in f.read_text(), f"{f.name} opens its own connection, without the key"


def test_a_value_from_page_script_is_checked_before_it_is_kept():
    assert re.search(r'key\.matches\("\[A-Za-z0-9_\\\\-\]\{16,200\}"\)', APP)
