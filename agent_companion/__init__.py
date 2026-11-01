"""Agent Companion. It was claude-companion until 2026-11-01."""
import os

# The old CLAUDE_COMPANION_* names still work: launchers and hooks written
# before the rename set them, and the new name wins when both are set.
for _k in [k for k in os.environ if k.startswith("CLAUDE_COMPANION_")]:
    os.environ.setdefault("AGENT_COMPANION_" + _k[len("CLAUDE_COMPANION_"):], os.environ[_k])
