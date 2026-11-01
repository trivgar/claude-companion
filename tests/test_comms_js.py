"""The browser player acknowledges actual audio delivery."""
from pathlib import Path


def test_comms_player_marks_played_only_after_play_and_ended():
    source = (Path(__file__).resolve().parent.parent / "agent_companion/server/static/comms.js").read_text()
    play = source[source.index("async function play"):source.index("async function loop")]
    assert "audio.onended = () => resolve({result: 'played'" in play
    assert "audio.onerror = () => resolve({result: 'failed'" in play
    assert "await mark(clip, 'failed', 'audio could not start')" in play
    assert play.index("await audio.play()") < play.index("const outcome = await finished")
    assert "await mark(clip, outcome.result, outcome.detail)" in play
