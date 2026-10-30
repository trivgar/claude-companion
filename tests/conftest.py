import os

# Never the real config: a test reads only what it sets.
os.environ["CLAUDE_COMPANION_CONFIG"] = os.path.join(os.path.dirname(__file__), "no-such-config.toml")
for k in [k for k in os.environ if k.startswith("CLAUDE_COMPANION_") and k != "CLAUDE_COMPANION_CONFIG"]:
    del os.environ[k]

# Owner keys and pairing codes live in the test's own folder, never the real config.
import tempfile  # noqa: E402
_cfg = tempfile.mkdtemp(prefix="cc-test-cfg-")
os.environ["CLAUDE_COMPANION_OWNER_KEYS"] = os.path.join(_cfg, "owner-keys.json")
os.environ["CLAUDE_COMPANION_PAIRING"] = os.path.join(_cfg, "pairing-codes.json")
os.environ["CLAUDE_COMPANION_ALLOWED_HOSTS"] = "testserver"   # TestClient sends Host: testserver
